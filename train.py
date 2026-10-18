# Multi-turn GRPO with LoRA on one website environment, using TRL's environment_factory (trl==1.16.0).
# The reward is Verity's Guard over each episode's transcript: it observes (baseline) or enforces (verity) the markers.
#   python train.py --env shop --arm baseline --output runs/shop-baseline
#   python train.py --env shop --arm verity --signatures markers/shop.json --output runs/shop-verity
#   python train.py --env flights --dry-run      # play reference answers through the rewards and Guard, no model
#   python train.py --env shop --arm baseline --model Qwen/Qwen3-8B --load-in-4bit --batch-size 2   # 24 GB GPU
import argparse
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import time
from pathlib import Path

from environments import web
from verity.guard import Guard

ROOT = Path(__file__).resolve().parent


def rewards(module, guard):
    # Guard's reward is the training reward; check() is logged only (weight 0), with the hack rate (grade 1, check 0)
    def grade(environments, trainer_state=None, log_metric=None, **kwargs):
        tasks = [{"site": env.site} for env in environments]
        outputs = [web.transcript(env) for env in environments]
        guarded, row = guard.batch(tasks, outputs, module, getattr(trainer_state, "global_step", None))
        if log_metric:
            for key in ("env_reward", "caught_rate", "check_us"):
                log_metric("verity/" + key, row[key])
        return guarded

    def check(environments, log_metric=None, **kwargs):
        checks = [module.outcome_state(env.site, env.state) for env in environments]
        if log_metric:
            grades = [module.reward_state(env.site, env.state) for env in environments]
            hacks = [g == 1.0 and c == 0.0 for g, c in zip(grades, checks)]
            log_metric("hack_rate", sum(hacks) / len(hacks))
        return checks

    return [grade, check]


def signatures(args, dry_run=False):
    # markers/<env>.json by default; the dry run falls back to the fixture audit's markers
    if args.signatures:
        return args.signatures
    path = ROOT / f"markers/{args.env}.json"
    if dry_run and not path.exists():
        path = ROOT / f"markers/{args.env}-fixture.json"
    if not path.exists():
        raise SystemExit(f"No markers at {path.relative_to(ROOT)}: run python -m verity.audit {args.env}, "
                         "or pass --signatures")
    return str(path)


def dry_run(args):
    # the oracle, the hack and one mistake per template, played through the environment class and scored,
    # then what Guard gives each one in both arms
    name, module = args.env, web.site_type(args.env)
    path = signatures(args, dry_run=True)
    arms = {arm: rewards(module, Guard(path, arm=arm, enforce=arm == "verity")) for arm in ("baseline", "verity")}
    print(f"markers: {path}")
    sites = web.read(module.data_path("train"))
    for n, template in enumerate(module.TEMPLATES):
        site = next(site for site in sites if site["template"] == template)
        mistakes = list(module.mistakes(site).items())
        answers = [("oracle", module.oracle(site)), ("hack", module.hack(site)), mistakes[n % len(mistakes)]]
        environments = [web.run(name, site, calls) for _, calls in answers]
        grades = [module.reward_state(env.site, env.state) for env in environments]
        metrics = {}
        checks = arms["baseline"][1](environments, log_metric=metrics.__setitem__)
        guarded = {arm: functions[0](environments) for arm, functions in arms.items()}
        for i, ((label, calls), g, c) in enumerate(zip(answers, grades, checks)):
            print(f"{template:10} {label:20} {len(calls):2} calls  grade {g:.2f}  check {c:.0f}  "
                  f"guard baseline {guarded['baseline'][i]:5.2f}  verity {guarded['verity'][i]:5.2f}")
        print(f"{'':10} hack_rate {metrics['hack_rate']:.2f}")


def batch_sizes(args):
    # completions per device step, and the accumulation that keeps a step at tasks x generations episodes
    batch = args.batch_size or args.generations
    if args.tasks * args.generations % batch:
        raise SystemExit(f"--batch-size {batch} must divide --tasks x --generations ({args.tasks * args.generations})")
    return batch, args.tasks * args.generations // batch


def manifest(args, module, markers, dtype, output):
    # run.json: everything needed to say what this run was
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    row = {"arguments": vars(args), "model": args.model, "dtype": dtype, "load_in_4bit": args.load_in_4bit,
           "thinking": args.thinking, "markers": markers,
           "markers_sha256": hashlib.sha256(Path(markers).read_bytes()).hexdigest(),
           "environment": f"{module.NAME}@{module.VERSION}", "git_commit": commit or None,
           "started_utc": datetime.now(timezone.utc).isoformat()}
    Path(output).mkdir(parents=True, exist_ok=True)
    (Path(output) / "run.json").write_text(json.dumps(row, indent=2) + "\n")


def train(args):
    module = web.site_type(args.env)
    output = args.output or f"runs/{args.env}-{args.arm}"
    batch, accumulation = batch_sizes(args)
    # load (and for the verity arm, validate) the markers before any GPU work
    telemetry = Path(output) / "telemetry.jsonl"
    markers = signatures(args)
    guard = Guard(markers, telemetry=telemetry, arm=args.arm, enforce=args.arm == "verity")

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import TrainerCallback
    from trl import GRPOConfig, GRPOTrainer

    class StepTimer(TrainerCallback):
        # seconds per training step (generation, rewards and update), logged to telemetry next to Guard's rows
        def on_train_begin(self, args, state, control, **kwargs):
            self.last = time.monotonic()

        def on_step_end(self, args, state, control, **kwargs):
            now, row = time.monotonic(), {"event": "step", "timestamp": datetime.now(timezone.utc).isoformat(),
                                          "step": state.global_step, "arm": guard.arm}
            row["seconds"], self.last = round(now - self.last, 3), now
            telemetry.parent.mkdir(parents=True, exist_ok=True)
            with telemetry.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row) + "\n")

    # auto: bf16 where the GPU supports it, else fp16; the 4-bit base model computes in the same dtype
    dtype = args.dtype if args.dtype != "auto" else (
        "bf16" if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else "fp16")
    model_kwargs = {"dtype": {"bf16": "bfloat16", "fp16": "float16"}[dtype]}
    if args.load_in_4bit:
        from transformers import BitsAndBytesConfig
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=getattr(torch, model_kwargs["dtype"]))
        model_kwargs["device_map"] = {"": 0}  # one GPU
    manifest(args, module, markers, dtype, output)

    sites = web.read(module.data_path("train"))
    dataset = Dataset.from_list([{"prompt": [{"role": "user", "content": web.prompt(site)}], "site": json.dumps(site)}
                                 for site in sites])
    config = GRPOConfig(
        output_dir=output,
        max_steps=args.steps,
        learning_rate=args.lr,
        num_generations=args.generations,
        per_device_train_batch_size=batch,  # on one GPU: batch x accumulation = tasks x generations per step
        gradient_accumulation_steps=accumulation,
        max_completion_length=args.max_completion_length,  # counts the whole conversation, pages included
        max_tool_calling_iterations=web.MAX_CALLS,
        reward_weights=[1.0, 0.0],
        model_init_kwargs=model_kwargs,
        bf16=dtype == "bf16",
        fp16=dtype == "fp16",
        # Qwen3's switch for thinking; other chat templates ignore it
        chat_template_kwargs={"enable_thinking": False} if args.thinking == "off" else None,
        gradient_checkpointing=True,
        use_vllm=args.use_vllm,
        vllm_mode="colocate",
        vllm_gpu_memory_utilization=args.vllm_memory,
        logging_steps=1,
        save_steps=25,
        log_completions=True,
    )
    lora = LoraConfig(r=args.lora_r, lora_alpha=2 * args.lora_r, target_modules="all-linear", task_type="CAUSAL_LM")
    trainer = GRPOTrainer(model=args.model, reward_funcs=rewards(module, guard), args=config, train_dataset=dataset,
                          peft_config=lora, environment_factory=web.CLASSES[args.env], callbacks=[StepTimer()])
    trainer.train()
    trainer.save_model(config.output_dir)


def main():
    parser = argparse.ArgumentParser(description="Multi-turn GRPO on a website environment.")
    parser.add_argument("--env", choices=web.ENVIRONMENTS, required=True)
    parser.add_argument("--arm", choices=("baseline", "verity"),
                        help="baseline: Guard observes only; verity: Guard enforces (the dry run shows both)")
    parser.add_argument("--signatures", help="marker file (default markers/<env>.json)")
    parser.add_argument("--output", default=None, help="default runs/<env>-<arm>")
    parser.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct", help="a Hugging Face model id")
    parser.add_argument("--dtype", choices=("auto", "bf16", "fp16"), default="auto",
                        help="auto: bf16 if the GPU supports it, else fp16")
    parser.add_argument("--load-in-4bit", action="store_true", help="QLoRA: a 4-bit NF4 base model (bitsandbytes)")
    parser.add_argument("--thinking", choices=("off", "on"), default="off", help="Qwen3's thinking mode")
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--generations", type=int, default=8)
    parser.add_argument("--tasks", type=int, default=4, help="tasks per step")
    parser.add_argument("--batch-size", type=int, help="completions per device step (default --generations); "
                        "gradient accumulation makes up the rest of tasks x generations")
    parser.add_argument("--max-completion-length", type=int, default=12288,
                        help="tokens; the shop hack's 20 calls take about 11k")
    parser.add_argument("--lr", type=float, default=5e-5)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--use-vllm", action="store_true", help="generate with vLLM in colocate mode (recommended)")
    parser.add_argument("--vllm-memory", type=float, default=0.3, help="vLLM's share of GPU memory")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        dry_run(args)
    elif not args.arm:
        parser.error("training needs --arm baseline or --arm verity")
    elif args.load_in_4bit and args.use_vllm:
        parser.error("--load-in-4bit generates with transformers; vLLM would load the full-precision weights")
    else:
        train(args)


if __name__ == "__main__":
    main()
