"""Held-out evaluation: the base model and both trained arms, on tasks never seen in training.

    python evaluate.py --env chess --baseline runs/baseline --verity runs/verity --output runs/results.json
    python evaluate.py --env shop --baseline runs/shop/baseline --verity runs/shop/verity --deploy --backend vllm \
        --record 5 --output runs/shop/results-ood.json

Each reply or episode is scored by the environment's grader (what training saw), its trusted reference check
(true score), and its own metrics. Refuses to compare arms that are not matched. Tools environments play every
episode in lockstep, one batched model turn at a time (transformers, or vLLM with the LoRA adapters);
--deploy uses the environment's DEPLOYMENT tools, prompt and held-out split.
"""
import argparse
from contextlib import nullcontext
import inspect
import json
from pathlib import Path
import re
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
        base = AutoModelForCausalLM.from_pretrained(model, revision=revision, dtype=dtype)
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
        return self.chat([{"role": "user", "content": prompt}], arm)

    def chat(self, messages, arm="base", tools=None):
        """One greedy assistant turn for a conversation, optionally with tool schemas."""
        if arm != "base" and arm not in self.adapters:
            raise ValueError(f"Unknown policy {arm}; available: base, {', '.join(self.adapters)}")
        with self.lock, self.torch.inference_mode():
            if arm != "base":
                self.model.set_adapter(arm)
            context = self.model.disable_adapter() if arm == "base" and self.adapters else nullcontext()
            with context:
                rendered = self.tokenizer.apply_chat_template(
                    messages, tools=tools, tokenize=False, add_generation_prompt=True, enable_thinking=False)
                inputs = self.tokenizer(rendered, return_tensors="pt", add_special_tokens=False,
                                        return_token_type_ids=False).to(self.device)
                generated = self.model.generate(
                    **inputs, do_sample=False, max_new_tokens=self.max_new_tokens,
                    pad_token_id=self.tokenizer.pad_token_id, eos_token_id=self.tokenizer.eos_token_id, use_cache=True)
                self._synchronize()
                return self.tokenizer.decode(generated[0, inputs["input_ids"].shape[-1]:], skip_special_tokens=True)

    def chats(self, conversations, arm="base", tools=None, batch=8):
        """One greedy assistant turn for each of several conversations, `batch` at a time (left padding). Only CUDA in
        bfloat16 batches: in float16, or on Apple's MPS, padded positions turn into NaN and spoil the real tokens."""
        if len(conversations) == 1 or not (self.device.startswith("cuda") and self.model.dtype == self.torch.bfloat16):
            return [self.chat(messages, arm, tools) for messages in conversations]
        replies = []
        for start in range(0, len(conversations), batch):
            with self.lock, self.torch.inference_mode():
                if arm != "base":
                    self.model.set_adapter(arm)
                context = self.model.disable_adapter() if arm == "base" and self.adapters else nullcontext()
                with context:
                    rendered = [self.tokenizer.apply_chat_template(messages, tools=tools, tokenize=False,
                                                                   add_generation_prompt=True, enable_thinking=False)
                                for messages in conversations[start:start + batch]]
                    inputs = self.tokenizer(rendered, return_tensors="pt", padding=True, add_special_tokens=False,
                                            return_token_type_ids=False).to(self.device)
                    generated = self.model.generate(
                        **inputs, do_sample=False, max_new_tokens=self.max_new_tokens,
                        pad_token_id=self.tokenizer.pad_token_id, eos_token_id=self.tokenizer.eos_token_id, use_cache=True)
                    self._synchronize()
                    width = inputs["input_ids"].shape[-1]
                    replies += [self.tokenizer.decode(row[width:], skip_special_tokens=True) for row in generated]
        return replies

    def warmup(self, prompt):
        timings = {}
        for arm in ("base", *self.adapters):
            started = time.monotonic()
            self.reply(prompt, arm)
            timings[arm] = round(time.monotonic() - started, 3)
        return timings


TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def tool_calls(text):
    """The tool calls in a reply, in Qwen's format: <tool_call>{"name": ..., "arguments": {...}}</tool_call>."""
    calls = []
    for raw in TOOL_CALL.findall(text):
        try:
            call = json.loads(raw)
        except ValueError:
            continue
        if isinstance(call, dict) and isinstance(call.get("name"), str):
            arguments = call.get("arguments") or {}
            calls.append({"name": call["name"], "arguments": arguments if isinstance(arguments, dict) else {}})
    return calls


def tool_names(episode_class):
    return [name for name, _ in inspect.getmembers(episode_class, inspect.isfunction)
            if not name.startswith("_") and name not in ("reset", "get_reward")]


def play(policy, env, task, arm="base", disabled=(), episode=None, rounds=None, deploy=False):
    """Play one episode of a tools environment.

    deploy: use the environment's DEPLOYMENT tools and prompt (training-only tools do not exist for the agent).
    disabled: tools that answer "not available". episode: an already reset episode to play instead of a fresh
    env.Episode; rounds: model turns allowed. episode.transcript records what the model said and every tool
    call with its result; episode.unavailable counts calls to tools the agent was not given."""
    from transformers.utils import get_json_schema
    first = None
    if episode is None:
        episode = env.Episode()
        first = episode.reset(**task)
    names = tool_names(type(episode))
    if deploy:
        names = [name for name in names if name in env.DEPLOYMENT["tools"]]
    prompt = (env.DEPLOYMENT["prompt"](task) if deploy else task["prompt"]) + (first or "")
    tools = [get_json_schema(getattr(episode, name)) for name in names]
    messages, episode.unavailable, episode.transcript = [{"role": "user", "content": prompt}], 0, []
    for _ in range(rounds or getattr(env, "MAX_TOOL_CALLS", 24)):
        if hasattr(env, "done") and env.done(episode):
            break
        calls = tool_calls(reply := policy.chat(messages, arm, tools))
        if said := TOOL_CALL.sub("", reply).strip():
            episode.transcript.append({"say": said[:500]})
        if not calls:
            break
        messages.append({"role": "assistant", "content": said,
                         "tool_calls": [{"type": "function", "function": call} for call in calls]})
        for call in calls:
            if call["name"] in disabled:
                episode.unavailable += 1
                result = f"{call['name']}() is not available here."
            elif call["name"] in names:
                try:
                    result = getattr(episode, call["name"])(**call["arguments"])
                except Exception as error:
                    result = f"error: {error}"
            else:
                episode.unavailable += 1
                result = f"error: unknown tool {call['name']}"
            episode.transcript.append({"call": call["name"], "arguments": call["arguments"], "result": str(result)[:300]})
            messages.append({"role": "tool", "name": call["name"], "content": str(result)})
    return episode


def episode_sample(env, task, episode, seconds=None):
    """One played episode as a results row: scores, the environment's metrics, its record and transcript."""
    return {**{k: v for k, v in task.items() if k != "prompt"}, **(env.metrics(episode) if hasattr(env, "metrics") else {}),
            "env_reward": float(env.grade(episode)), "true_score": float(env.check(episode)),
            "unavailable_calls": float(episode.unavailable), "record": environment.record(env, episode),
            "transcript": episode.transcript, **({"latency_seconds": round(seconds, 4)} if seconds is not None else {})}


def summary(env, samples):
    """Means of the scores and of every metric the environment reports (averaged its own way)."""
    means = environment.summarize(env, [{k: v for k, v in s.items() if k not in ("latency_seconds",)
                                         and (v is None or isinstance(v, (int, float)))} for s in samples])
    return {"n": len(samples), **{k: v for k, v in means.items() if v is not None}, "samples": samples}


def evaluate_episodes(policy, env, tasks, arm, disabled=(), deploy=False):
    samples = []
    for index, task in enumerate(tasks):
        started = time.monotonic()
        episode = play(policy, env, task, arm, disabled, deploy=deploy)
        samples.append(episode_sample(env, task, episode, time.monotonic() - started))
        if (index + 1) % 10 == 0:
            print(f"{arm}: {index + 1}/{len(tasks)} episodes", flush=True)
    return summary(env, samples)


def run_call(episode, names, call, disabled=()):
    """One tool call the way the trainer runs it: a bad call comes back as an error message."""
    if call["name"] in disabled:
        episode.unavailable += 1
        return f"{call['name']}() is not available here."
    if call["name"] not in names:
        episode.unavailable += 1
        return f"error: unknown tool {call['name']}"
    try:
        return getattr(episode, call["name"])(**call["arguments"])
    except Exception as error:
        return f"error: {error}"


def play_all(env, tasks, respond, deploy=False, disabled=(), keep=0):
    """Play every task's episode in lockstep: each round, one batched model turn for every unfinished episode,
    whose tool calls then run on its episode. respond(conversations, tools) -> replies. The first `keep` episodes
    also record every observation in full (for replays on the site)."""
    names = tool_names(env.Episode)
    if deploy:
        names = [name for name in names if name in env.DEPLOYMENT["tools"]]
    from transformers.utils import get_json_schema
    tools = [get_json_schema(getattr(env.Episode(), name)) for name in names]  # each tool's schema, from a bound method
    episodes, chats = [], []
    for index, task in enumerate(tasks):
        episode = env.Episode()
        first = episode.reset(**task)
        episode.transcript, episode.unavailable = [], 0
        if index < keep:
            episode.frames = [{"observation": first}]
        episodes.append(episode)
        prompt = env.DEPLOYMENT["prompt"](task) if deploy else task["prompt"]
        chats.append([{"role": "user", "content": prompt + (first or "")}])
    live = list(range(len(tasks)))
    for _ in range(getattr(env, "MAX_TOOL_CALLS", 24)):
        live = [i for i in live if not (hasattr(env, "done") and env.done(episodes[i]))]
        if not live:
            break
        replies, still = respond([chats[i] for i in live], tools), []
        for i, reply in zip(live, replies):
            episode, calls = episodes[i], tool_calls(reply)
            said = re.sub(r"<think>.*?</think>", "", TOOL_CALL.sub("", reply), flags=re.S).strip()
            if said:
                episode.transcript.append({"say": said[:500]})
            chats[i].append({"role": "assistant", "content": said,
                             **({"tool_calls": [{"type": "function", "function": call} for call in calls]} if calls else {})})
            for call in calls:
                result = str(run_call(episode, names, call, disabled))
                episode.transcript.append({"call": call["name"], "arguments": call["arguments"], "result": result[:300]})
                if hasattr(episode, "frames"):
                    episode.frames.append({"call": call["name"], "arguments": call["arguments"], "observation": result})
                chats[i].append({"role": "tool", "name": call["name"], "content": result})
            if calls:
                still.append(i)
        live = still
    return episodes


def vllm_engine(model, adapters, max_lora_rank, max_new_tokens):
    """One vLLM engine with every LoRA adapter; respond(arm) gives a play_all callable for that policy."""
    from transformers import AutoTokenizer
    from vllm import LLM, SamplingParams
    from vllm.lora.request import LoRARequest
    tokenizer = AutoTokenizer.from_pretrained(model)
    llm = LLM(model=model, enable_lora=bool(adapters), max_lora_rank=max_lora_rank, gpu_memory_utilization=0.85,
              max_model_len=16384)
    params = SamplingParams(temperature=0.0, max_tokens=max_new_tokens)
    requests = {name: LoRARequest(name, number, str(path)) for number, (name, path) in enumerate(adapters.items(), 1)}

    def respond(arm):
        def turn(conversations, tools):
            prompts = [tokenizer.apply_chat_template(chat, tools=tools, add_generation_prompt=True, tokenize=False,
                                                     enable_thinking=False) for chat in conversations]
            outputs = llm.generate(prompts, params, lora_request=requests.get(arm), use_tqdm=False)
            return [output.outputs[0].text for output in outputs]
        return turn
    return respond


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


LABELS = {"env_reward": "Env reward", "true_score": "True score", "guard_match_rate": "Guard match",
          "unavailable_calls": "Disabled-tool calls"}


def markdown_results(result):
    """Env reward and true score first, then every metric the environment reports."""
    first = next(iter(result["models"].values()))
    keys = ["env_reward", "true_score"] + sorted(key for key, value in first.items() if isinstance(value, float)
                                                 and key not in ("env_reward", "true_score", "seconds"))
    lines = [f"# Held-out evaluation: {result['environment']}", "",
             f"{result['positions']} held-out tasks ({result.get('split', 'eval')} split"
             f"{', as deployed' if result.get('deployment') else ''}); greedy decoding.", "",
             "| Policy | " + " | ".join(LABELS.get(key, key.replace("_", " ").capitalize()) for key in keys) + " |",
             "|---|" + "---:|" * len(keys)]
    for name, metrics in result["models"].items():
        cells = ["-" if metrics.get(key) is None else f"{metrics[key]:.1%}" if key.endswith(("_rate", "accuracy"))
                 else f"{metrics[key]:.2f}" for key in keys]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "Env reward is the environment's own grader; true score is its trusted reference check.",
              "Results describe this environment and these runs only.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", required=True)
    parser.add_argument("--baseline", required=True, help="baseline run directory")
    parser.add_argument("--verity", required=True, help="verity run directory")
    parser.add_argument("--train-data", help="default: the environment's training split")
    parser.add_argument("--eval-data", help="default: the environment's held-out split (DEPLOYMENT's split with --deploy)")
    parser.add_argument("--signatures", help="marker file used in training (default markers/<env>.json)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--tasks", "--positions", dest="count", type=int, default=200)
    parser.add_argument("--disable", default="", help="tools environments: comma-separated tools that answer 'not available'")
    parser.add_argument("--deploy", action="store_true",
                        help="tools environments: only the environment's deployment tools, prompt and split (DEPLOYMENT)")
    parser.add_argument("--backend", choices=("hf", "vllm"), default="hf",
                        help="tools environments: transformers (any device) or vLLM (CUDA) for batched turns")
    parser.add_argument("--batch", type=int, default=8, help="hf backend: conversations per generate call")
    parser.add_argument("--record", type=int, default=0, help="tools environments: keep every observation of the "
                        "first N episodes per model, for replays")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args(argv)
    try:
        env = environment.load(args.env)
        if args.count < (20 if env.KIND == "tools" else 200):
            raise ValueError("Use at least 200 held-out tasks (20 episodes for a tools environment)")
        if args.deploy and not hasattr(env, "DEPLOYMENT"):
            raise ValueError(f"{env.NAME} defines no DEPLOYMENT")
        split = env.DEPLOYMENT.get("split", "eval") if args.deploy else "eval"
        train_data = args.train_data or env.data_path("train")
        eval_data = args.eval_data or env.data_path(split)
        tasks = env.tasks(split, path=eval_data, minimum=args.count)[:args.count]
        runs = validate_runs(env, args.baseline, args.verity, train_data, tasks)
        config = runs["baseline"]["config"]
        signatures = args.signatures or str(ROOT / f"markers/{env.NAME}.json")
        if sha256(signatures) != config["signature_sha256"]:
            raise ValueError("Evaluation markers differ from the training markers")
        guard = Guard(signatures)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    adapters = {arm: str(Path(directory) / "adapter") for arm, directory in (("baseline", args.baseline),
                                                                          ("verity", args.verity))}
    revision = runs["baseline"].get("model_commit") or config["revision"]
    vllm = env.KIND == "tools" and args.backend == "vllm"
    if vllm:
        respond = vllm_engine(config["model"], adapters, config.get("lora_r", 16), 512)
    else:
        policy = Policy(config["model"], adapters, device=args.device, revision=revision,
                        max_new_tokens=512 if env.KIND == "tools" else config["max_completion_length"])
        respond = lambda arm: lambda conversations, tools: policy.chats(conversations, arm, tools, args.batch)  # noqa: E731
    result = {"schema": "verity-evaluation-v1", "created_at": utc_now(), "status": "running",
              "environment": environment.identity(env), "positions": len(tasks), "split": split,
              "eval_dataset_sha256": sha256(eval_data), "config": config, "decoding": "greedy",
              "backend": "vllm" if vllm else "hf", "device": "cuda" if vllm else policy.device,
              "warmup_seconds": None if vllm or env.KIND == "tools" else policy.warmup(tasks[0]["prompt"]),
              "runs": {arm: {key: run.get(key) for key in ("arm", "steps_completed", "git_commit", "model_commit", "wall_seconds")}
                       for arm, run in runs.items()}, "models": {}}
    write_json(args.output, result)
    try:
        disabled = tuple(name for name in args.disable.split(",") if name)
        result["disabled_tools"] = list(disabled)
        result["deployment"] = bool(args.deploy)
        for arm in ("base", "baseline", "verity"):
            started = time.monotonic()
            if env.KIND == "tools":
                episodes = play_all(env, tasks, respond(arm), args.deploy, disabled, keep=args.record)
                result["models"][arm] = summary(env, [episode_sample(env, task, episode) for task, episode in zip(tasks, episodes)])
                replays = [{"task": index, "goal": tasks[index]["prompt"].split("\n")[0], "frames": episode.frames,
                            "env_reward": float(env.grade(episode)), "true_score": float(env.check(episode)),
                            "record": environment.record(env, episode)}
                           for index, episode in enumerate(episodes[:args.record])]
                if replays:
                    result["models"][arm]["replays"] = replays
            else:
                result["models"][arm] = evaluate_policy(policy, env, tasks, arm, guard)
            result["models"][arm]["seconds"] = round(time.monotonic() - started, 1)
            print(f"{arm}: " + ", ".join(f"{k} {v:.3f}" for k, v in result["models"][arm].items()
                                         if isinstance(v, float)), flush=True)
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
