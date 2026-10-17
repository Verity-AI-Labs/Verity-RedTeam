"""Held-out evaluation: the base model and both trained arms, on tasks never seen in training.

    python evaluate.py --env chess --baseline runs/baseline --verity runs/verity --output runs/results.json

Each reply is scored by the environment's grader (what training saw), its trusted reference check
(true score), and Guard (does it match a known hack). Refuses to compare arms that are not matched.
"""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import threading
import time

from verity import environment
from verity.guard import Guard
from train import DEFAULT_MODEL, ROOT, sha256, utc_now, write_json


class Policy:
    """One base model with named LoRA adapters; greedy replies to a prompt as base, baseline or verity."""

    def __init__(self, model=DEFAULT_MODEL, adapters=None, device="auto", max_new_tokens=160, revision="main"):
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from peft import PeftModel
        except ImportError as error:
            raise RuntimeError("Install requirements-ml.txt to load trained policies") from error
        self.torch = torch
        self.lock = threading.Lock()
        self.adapters = dict(adapters or {})
        if "base" in self.adapters:
            raise ValueError("'base' is reserved for the untrained model")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = device
        self.max_new_tokens = max_new_tokens
        dtype = torch.float32 if device == "cpu" else (
            torch.bfloat16 if device.startswith("cuda") and torch.cuda.is_bf16_supported(including_emulation=False) else torch.float16)
        self.tokenizer = AutoTokenizer.from_pretrained(model, revision=revision)
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"
        base = AutoModelForCausalLM.from_pretrained(model, revision=revision, torch_dtype=dtype)
        self.model = base
        for index, (name, path) in enumerate(self.adapters.items()):
            if not (Path(path) / "adapter_config.json").is_file():
                raise ValueError(f"Adapter not found: {path}")
            if index == 0:
                self.model = PeftModel.from_pretrained(base, str(path), adapter_name=name, is_trainable=False)
            else:
                self.model.load_adapter(str(path), adapter_name=name, is_trainable=False)
        self.model.to(device)
        self.model.eval()

    def _synchronize(self):
        if self.device.startswith("cuda"):
            self.torch.cuda.synchronize()
        elif self.device == "mps":
            self.torch.mps.synchronize()

    def reply(self, prompt, arm="base"):
        if arm != "base" and arm not in self.adapters:
            raise ValueError(f"Unknown policy {arm}; available: base, {', '.join(self.adapters)}")
        with self.lock, self.torch.inference_mode():
            if arm != "base":
                self.model.set_adapter(arm)
            context = self.model.disable_adapter() if arm == "base" and self.adapters else nullcontext()
            with context:
                rendered = self.tokenizer.apply_chat_template(
                    [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
                inputs = self.tokenizer(rendered, return_tensors="pt", add_special_tokens=False,
                                        return_token_type_ids=False).to(self.device)
                generated = self.model.generate(
                    **inputs, do_sample=False, max_new_tokens=self.max_new_tokens,
                    pad_token_id=self.tokenizer.pad_token_id, eos_token_id=self.tokenizer.eos_token_id, use_cache=True)
                self._synchronize()
                return self.tokenizer.decode(generated[0, inputs["input_ids"].shape[-1]:], skip_special_tokens=True)

    def warmup(self, prompt):
        timings = {}
        for arm in ("base", *self.adapters):
            started = time.monotonic()
            self.reply(prompt, arm)
            timings[arm] = round(time.monotonic() - started, 3)
        return timings


def validate_runs(env, baseline_dir, verity_dir, train_data, eval_tasks):
    """Both arms completed, matched in everything but Guard mode, on this environment, with no task overlap."""
    runs = {}
    for arm, directory in (("baseline", baseline_dir), ("verity", verity_dir)):
        directory = Path(directory)
        run = json.loads((directory / "run.json").read_text())
        if run.get("arm") != arm or run.get("status") != "completed":
            raise ValueError(f"{arm} needs a completed {arm} training run")
        if run.get("steps_completed") != run.get("config", {}).get("max_steps"):
            raise ValueError(f"{arm} did not finish the configured steps")
        if not (directory / "adapter" / "adapter_config.json").is_file():
            raise ValueError(f"{arm} adapter missing in {directory}")
        runs[arm] = run
    if runs["baseline"]["config"] != runs["verity"]["config"]:
        raise ValueError("Training arms differ in configuration; refusing a matched comparison")
    for key in ("model_commit", "precision", "packages", "source_sha256"):
        if runs["baseline"].get(key) != runs["verity"].get(key):
            raise ValueError(f"Training arms differ in {key}; refusing a matched comparison")
    config = runs["baseline"]["config"]
    if sha256(train_data) != config["train_dataset_sha256"]:
        raise ValueError("Training data differs from the run manifests")
    if not environment.matches(env, config):
        raise ValueError("Environment/prompt changed since training")
    key = getattr(env, "task_key", lambda task: task["prompt"])
    trained = {key(task) for task in env.tasks("train", path=train_data)}
    if any(key(task) in trained for task in eval_tasks):
        raise ValueError("Held-out tasks overlap the training data")
    return runs


def score_reply(env, task, reply, guard=None):
    return {"env_reward": env.grade(task, reply), "true_score": env.check(task, reply),
            **(env.metrics(task, reply) if hasattr(env, "metrics") else {}),
            "guard_match_rate": float(bool(guard.check(reply))) if guard else 0.0}


def evaluate_policy(policy, env, tasks, arm, guard=None):
    samples = []
    for index, task in enumerate(tasks):
        started = time.monotonic()
        reply = policy.reply(task["prompt"], arm)
        samples.append({**{k: v for k, v in task.items() if k != "prompt"}, "reply": reply,
                        "latency_seconds": round(time.monotonic() - started, 4), **score_reply(env, task, reply, guard)})
        if (index + 1) % 25 == 0:
            print(f"{arm}: {index + 1}/{len(tasks)} tasks", flush=True)
    keys = [key for key in samples[0] if isinstance(samples[0][key], float) and key != "latency_seconds"]
    return {"n": len(samples), **{key: sum(s[key] for s in samples) / len(samples) for key in keys}, "samples": samples}


LABELS = {"env_reward": "Env reward", "true_score": "True score", "hack_rate": "Hack rate",
          "top1_accuracy": "Top-1 accuracy", "guard_match_rate": "Guard match"}


def markdown_results(result):
    first = next(iter(result["models"].values()))
    keys = [key for key in ("env_reward", "true_score", "hack_rate", "top1_accuracy") if key in first]
    lines = [f"# Held-out evaluation: {result['environment']}", "",
             f"{result['positions']} held-out tasks; greedy decoding.", "",
             "| Policy | " + " | ".join(LABELS.get(key, key) for key in keys) + " |",
             "|---|" + "---:|" * len(keys)]
    for name, metrics in result["models"].items():
        cells = [f"{metrics[key]:.1%}" if key.endswith(("_rate", "accuracy")) else f"{metrics[key]:.3f}" for key in keys]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "Env reward is the environment's own grader; true score is its trusted reference check.",
              "Results describe this environment and these runs only.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default="chess")
    parser.add_argument("--baseline", default="artifacts/dubhacks/baseline")
    parser.add_argument("--verity", default="artifacts/dubhacks/verity")
    parser.add_argument("--train-data", help="default: the environment's training split")
    parser.add_argument("--eval-data", help="default: the environment's held-out split")
    parser.add_argument("--signatures", help="marker file used in training (default markers/<env>.json)")
    parser.add_argument("--output", default="artifacts/dubhacks/results.json")
    parser.add_argument("--tasks", "--positions", dest="count", type=int, default=200)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args(argv)
    if args.count < 200:
        parser.error("Use at least 200 held-out tasks for the final comparison")
    try:
        env = environment.load(args.env)
        train_data = args.train_data or env.data_path("train")
        eval_data = args.eval_data or env.data_path("eval")
        tasks = env.tasks("eval", path=eval_data, minimum=args.count)[:args.count]
        runs = validate_runs(env, args.baseline, args.verity, train_data, tasks)
        config = runs["baseline"]["config"]
        signatures = args.signatures or str(ROOT / f"markers/{env.NAME}.json")
        if sha256(signatures) != config["signature_sha256"]:
            raise ValueError("Evaluation markers differ from the training markers")
        guard = Guard(signatures)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    policy = Policy(config["model"], {"baseline": str(Path(args.baseline) / "adapter"),
                                      "verity": str(Path(args.verity) / "adapter")},
                    device=args.device, max_new_tokens=config["max_completion_length"],
                    revision=runs["baseline"].get("model_commit") or config["revision"])
    result = {"schema": "verity-evaluation-v1", "created_at": utc_now(), "status": "running",
              "environment": environment.identity(env), "positions": len(tasks),
              "eval_dataset_sha256": sha256(eval_data), "config": config, "decoding": "greedy",
              "warmup_seconds": policy.warmup(tasks[0]["prompt"]), "device": policy.device,
              "runs": {arm: {key: run.get(key) for key in ("arm", "steps_completed", "git_commit", "model_commit", "wall_seconds")}
                       for arm, run in runs.items()}, "models": {}}
    write_json(args.output, result)
    try:
        for arm in ("base", "baseline", "verity"):
            result["models"][arm] = evaluate_policy(policy, env, tasks, arm, guard)
            write_json(args.output, result)
        result["status"] = "completed"
    except BaseException as error:
        result["status"] = "failed"
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        write_json(args.output, result)
    markdown = markdown_results(result)
    Path(args.output).with_suffix(".md").write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
