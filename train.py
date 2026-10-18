# Multi-turn GRPO with LoRA on the shop, one arm on one GPU (TRL's environment_factory, trl==1.16.0).
# The reward is Verity's Guard over each episode's transcript: it observes (baseline) or enforces (verity) the markers.
#   python train.py --arm baseline --output runs/shop-baseline      # the hacks pay all through training
#   python train.py --arm verity --output runs/shop-verity          # a matching marker turns the reward into -1
#   python train.py --dry-run      # the oracle, each hack and a mistake through both arms' rewards, no model
import argparse
from datetime import datetime, timezone
import hashlib
import json
import statistics
import subprocess
import time
from pathlib import Path

import evaluate
from environments import shop, web
from verity.guard import Guard

ROOT = Path(__file__).resolve().parent
ARMS = ("baseline", "verity")
# the longest hack episode is about 5,400 characters (python -m environments.web stats); HTML tokenizes at nearer
# 3 characters per token, so about 1,800 tokens, and 1.5x that is rounded up
MAX_COMPLETION = 3072
LOGGED = ("success", "reward", "hack_extra", "hack_skip", "items_per_order", "coupon_applied", "requested_shipping",
          "calls")


def signatures(path=None, dry_run=False):
    # markers/shop.json (a live audit's output) by default; the dry run falls back to the fixture audit's markers
    found = Path(path or ROOT / "markers/shop.json")
    if not path and dry_run and not found.exists():
        found = ROOT / "markers/shop-fixture.json"
    if not found.exists():
        raise SystemExit(f"No markers at {found}: run python -m verity.audit shop --rounds 2, or pass --signatures")
    return str(found)


def rewards(guard):
    # Guard's reward over the episode transcripts; its telemetry row also has the hack rates and the catch rate per marker
    def reward(environments, trainer_state=None, log_metric=None, **kwargs):
        tasks = [{"site": env.site} for env in environments]
        outputs = [web.transcript(env) for env in environments]
        # TRL generates step k's episodes when global_step is still k - 1
        step = trainer_state.global_step + 1 if trainer_state else None
        guarded, row = guard.batch(tasks, outputs, shop, step)
        if log_metric:
            for key in LOGGED:
                if row.get(key) is not None:
                    log_metric(key, row[key])
            log_metric("verity/caught_rate", row["caught_rate"])
            for marker, rate in row["caught_by"].items():
                log_metric(f"verity/caught/{marker}", rate)
        return guarded
    return reward


def dry_run(args):
    # per template: the oracle, each hack that applies and one mistake, through both arms' reward functions
    path = signatures(args.signatures, dry_run=True)
    functions = {arm: rewards(Guard(path, arm=arm, enforce=arm == "verity")) for arm in ARMS}
    print(f"markers: {path}\n")
    print(f"{'template':9} {'answer':14} {'calls':>5} {'reward':>7} {'outcome':>8} {'baseline':>9} {'verity':>7}  markers")
    sites, logged = web.read(shop.data_path("train")), {}
    for n, template in enumerate(shop.TEMPLATES):
        site = next(site for site in sites if site["template"] == template and shop.skippable(site))
        mistakes = list(shop.mistakes(site).items())
        answers = [("oracle", shop.oracle(site)), *shop.hacks(site).items(), mistakes[n % len(mistakes)]]
        browsers = [web.run(site, calls) for _, calls in answers]
        given = {arm: function(browsers, log_metric=logged.setdefault(arm, {}).__setitem__)
                 for arm, function in functions.items()}
        guard = Guard(path)
        for i, ((label, calls), browser) in enumerate(zip(answers, browsers)):
            fired = ", ".join(marker["id"] for marker in guard.check(web.transcript(browser))) or "-"
            print(f"{template:9} {label:14} {len(calls):5} {shop.reward_state(browser.site, browser.state):7.2f} "
                  f"{shop.outcome_state(browser.site, browser.state):8.0f} {given['baseline'][i]:9.2f} "
                  f"{given['verity'][i]:7.2f}  {fired}")
    print("\nlast batch as logged (verity arm): " + ", ".join(f"{k} {v:.2f}" for k, v in logged["verity"].items()))


def batch_sizes(args):
    # completions per device step, and the accumulation that keeps a step at tasks x generations episodes
    batch = args.batch_size or args.generations
    if args.tasks * args.generations % batch:
        raise SystemExit(f"--batch-size {batch} must divide --tasks x --generations ({args.tasks * args.generations})")
    return batch, args.tasks * args.generations // batch


def manifest(args, markers, dtype, output):
    # run.json: everything needed to say what this run was
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    row = {"arguments": vars(args), "model": args.model, "dtype": dtype, "markers": markers,
           "markers_sha256": hashlib.sha256(Path(markers).read_bytes()).hexdigest(),
           "environment": f"{shop.NAME}@{shop.VERSION}", "git_commit": commit or None,
           "started_utc": datetime.now(timezone.utc).isoformat()}
    output.mkdir(parents=True, exist_ok=True)
    (output / "run.json").write_text(json.dumps(row, indent=2) + "\n")


def write(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), **row}) + "\n")


def flat(telemetry, arm, window=20, span=40, tolerance=0.02):
    # the 20-step rolling real success (verity) or training reward (baseline) moved less than 0.02 over 40 steps
    key, per_step = ("success" if arm == "verity" else "env_reward"), {}
    for line in telemetry.read_text().splitlines() if telemetry.exists() else []:
        row = json.loads(line)
        if row.get("event") == "rollout" and row.get(key) is not None:
            per_step.setdefault(row["step"], []).append(row[key])
    values = [statistics.fmean(per_step[step]) for step in sorted(per_step)]
    rolling = [statistics.fmean(values[i - window + 1:i + 1]) for i in range(window - 1, len(values))]
    return len(rolling) > span and max(rolling[-span - 1:]) - min(rolling[-span - 1:]) < tolerance


def train(args):
    output = Path(args.output or f"runs/shop-{args.arm}")
    telemetry = output / "telemetry.jsonl"
    markers = signatures(args.signatures)
    # load (and for the verity arm, validate) the markers before any GPU work
    guard = Guard(markers, telemetry=telemetry, arm=args.arm, enforce=args.arm == "verity")
    batch, accumulation = batch_sizes(args)

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import TrainerCallback
    from transformers.trainer_utils import get_last_checkpoint
    from trl import GRPOConfig, GRPOTrainer

    class Monitor(TrainerCallback):
        # seconds per step, a greedy eval at step 0 and every --eval-steps, and --stop-when-flat
        trainer = None

        def on_train_begin(self, train_args, state, control, **kwargs):
            if args.eval_steps and state.global_step == 0:
                self.evaluate(0)
            self.last = time.monotonic()

        def on_step_end(self, train_args, state, control, **kwargs):
            write(telemetry, {"event": "step", "step": state.global_step, "arm": args.arm,
                              "seconds": round(time.monotonic() - self.last, 3)})
            if args.eval_steps and state.global_step % args.eval_steps == 0:
                self.evaluate(state.global_step)
            if args.stop_when_flat and flat(telemetry, args.arm):
                print(f"Stopping at step {state.global_step}: the rolling metric is flat")
                control.should_training_stop = True
            self.last = time.monotonic()

        def evaluate(self, step):
            # greedy, on the first --eval-tasks eval tasks, with the current weights
            start, trainer = time.monotonic(), self.trainer
            thinking = args.thinking == "on"
            if args.vllm:
                trainer.vllm_generation.sync_weights()
                respond = evaluate.vllm_policy(trainer.vllm_generation.llm, trainer.processing_class, thinking)
            else:
                model = trainer.accelerator.unwrap_model(trainer.model)
                model.eval()
                respond = evaluate.hf_policy(model, trainer.processing_class, thinking)
            browsers, _ = evaluate.play_all(eval_sites, respond)
            if not args.vllm:
                model.train()
            row = shop.summarize([shop.measure(browser) for browser in browsers])
            write(telemetry, {"event": "eval", "step": step, "arm": args.arm, "split": "eval", "tasks": len(browsers),
                              "seconds": round(time.monotonic() - start, 1), **row})
            print(f"eval at step {step}: " + ", ".join(f"{k} {v:.2f}" for k, v in row.items() if v is not None))

    # bf16 where the GPU supports it, else fp16; a 4-bit base model computes in the same dtype
    dtype = args.dtype if args.dtype != "auto" else (
        "bf16" if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else "fp16")
    model_kwargs = {"dtype": {"bf16": "bfloat16", "fp16": "float16"}[dtype]}
    if args.load_in_4bit:
        from transformers import BitsAndBytesConfig
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=getattr(torch, model_kwargs["dtype"]))
        model_kwargs["device_map"] = {"": 0}  # one GPU
    checkpoint = get_last_checkpoint(str(output)) if args.resume and output.exists() else None
    if not checkpoint and telemetry.exists():
        telemetry.unlink()  # a fresh run starts a fresh log
    manifest(args, markers, dtype, output)

    sites = web.read(shop.data_path("train"))
    eval_sites = web.read(shop.data_path("eval"))[:args.eval_tasks]
    dataset = Dataset.from_list([{"prompt": [{"role": "user", "content": web.prompt(site)}], "site": json.dumps(site)}
                                 for site in sites])
    config = GRPOConfig(
        output_dir=str(output),
        max_steps=args.steps,
        learning_rate=args.lr,
        beta=args.beta,
        loss_type="dapo",
        scale_rewards="group",
        num_generations=args.generations,
        per_device_train_batch_size=batch,  # on one GPU: batch x accumulation = tasks x generations per step
        gradient_accumulation_steps=accumulation,
        max_completion_length=args.max_completion_length,  # counts the whole conversation after the prompt
        max_tool_calling_iterations=web.MAX_CALLS,
        model_init_kwargs=model_kwargs,
        bf16=dtype == "bf16",
        fp16=dtype == "fp16",
        # Qwen3's switch for thinking; other chat templates ignore it
        chat_template_kwargs={"enable_thinking": args.thinking == "on"},
        gradient_checkpointing=True,
        use_vllm=args.vllm,
        vllm_mode="colocate",
        vllm_gpu_memory_utilization=args.vllm_memory,
        logging_steps=1,
        save_steps=args.save_steps,
        log_completions=True,
    )
    lora = LoraConfig(r=args.lora_r, lora_alpha=2 * args.lora_r, target_modules="all-linear", task_type="CAUSAL_LM")
    monitor = Monitor()
    trainer = GRPOTrainer(model=args.model, reward_funcs=[rewards(guard)], args=config, train_dataset=dataset,
                          peft_config=lora, environment_factory=web.Browser, callbacks=[monitor])
    monitor.trainer = trainer
    trainer.train(resume_from_checkpoint=checkpoint)
    trainer.save_model(str(output))


def main():
    parser = argparse.ArgumentParser(description="Multi-turn GRPO on the shop, one arm on one GPU.")
    parser.add_argument("--arm", choices=ARMS, help="baseline: Guard only observes; verity: Guard enforces")
    parser.add_argument("--signatures", help="marker file (default markers/shop.json)")
    parser.add_argument("--output", help="default runs/shop-<arm>")
    parser.add_argument("--model", default="Qwen/Qwen3-8B", help="a Hugging Face model id")
    parser.add_argument("--thinking", choices=("off", "on"), default="off", help="Qwen3's thinking mode")
    parser.add_argument("--dtype", choices=("auto", "bf16", "fp16"), default="bf16", help="auto: bf16 if supported")
    parser.add_argument("--load-in-4bit", action="store_true", help="QLoRA (bitsandbytes NF4); needs --no-vllm")
    parser.add_argument("--lora-r", type=int, default=32)
    parser.add_argument("--lr", type=float, default=5e-5)
    parser.add_argument("--beta", type=float, default=0.0, help="KL coefficient")
    parser.add_argument("--tasks", type=int, default=8, help="tasks per step")
    parser.add_argument("--generations", type=int, default=8, help="episodes per task")
    parser.add_argument("--batch-size", type=int, help="completions per device step (default --generations); "
                        "gradient accumulation makes up the rest of tasks x generations")
    parser.add_argument("--steps", type=int, default=80)
    parser.add_argument("--max-completion-length", type=int, default=MAX_COMPLETION,
                        help="tokens after the prompt, tool results included")
    parser.add_argument("--vllm", action=argparse.BooleanOptionalAction, default=True,
                        help="generate with vLLM in colocate mode (--no-vllm: transformers)")
    parser.add_argument("--vllm-memory", type=float, default=0.35, help="vLLM's share of GPU memory")
    parser.add_argument("--save-steps", type=int, default=20)
    parser.add_argument("--eval-steps", type=int, default=20, help="greedy eval every N steps and at step 0 (0: none)")
    parser.add_argument("--eval-tasks", type=int, default=50)
    parser.add_argument("--resume", action="store_true", help="continue from the last checkpoint in --output")
    parser.add_argument("--stop-when-flat", action="store_true",
                        help="stop when the 20-step rolling real success (verity) or reward (baseline) "
                             "moves less than 0.02 over 40 steps")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        dry_run(args)
    elif not args.arm:
        parser.error("training needs --arm baseline or --arm verity")
    elif args.load_in_4bit and args.vllm:
        parser.error("--load-in-4bit generates with transformers: add --no-vllm")
    else:
        train(args)


if __name__ == "__main__":
    main()
