"""RL fine-tuning (GRPO + LoRA) of an open model on an answer or tools environment, with Verity's Guard.

    python train.py --env chess --arm baseline --output runs/baseline   # Guard only watches and logs
    python train.py --env chess --arm verity   --output runs/verity     # Guard enforces the markers
    python train.py --env shop --arm verity --model Qwen/Qwen3-8B --vllm ...   # see scripts/runpod.sh

The two arms are identical except for that one switch: same model, data, seed and settings. Every
answer (or, in a tools environment, every tool call) goes through Guard; the trainer only ever sees
Guard's reward. Needs a CUDA GPU (see scripts/hyak.sh); --dry-run checks inputs anywhere.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from verity import environment
from verity.guard import Guard

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def dataset_rows(tasks):
    """TRL rows: the chat prompt, plus every other task field (the grader needs them)."""
    return [{"prompt": [{"role": "user", "content": task["prompt"]}],
             **{key: value for key, value in task.items() if key != "prompt"}} for task in tasks]


def make_reward(env, guard):
    """The only TRL-specific glue: TRL's reward call -> Guard, for answer or tools environments."""
    def verity_reward(prompts, completions, environments=None, **columns):
        step = getattr(columns.get("trainer_state"), "global_step", None)
        if env.KIND == "tools":
            rewards, row = guard.episodes(environments, env, step=step)
        else:
            fields = {key: value for key, value in columns.items()
                      if key != "completion_ids" and isinstance(value, list) and len(value) == len(completions)}
            tasks = [{key: value[i] for key, value in fields.items()} for i in range(len(completions))]
            rewards, row = guard.batch(tasks, completions, env, step=step)
        log = columns.get("log_metric")
        if callable(log):  # Guard's batch means, its catch rate per marker, and every metric the environment reports
            for key, value in row.items():
                if isinstance(value, float):
                    log("verity/" + key, value)
            for marker, rate in row.get("caught_by", {}).items():
                log(f"verity/caught/{marker}", rate)
        return rewards
    return verity_reward


def training_config(args, env, train_data):
    if args.steps < 1 or args.learning_rate <= 0 or args.temperature <= 0 or args.max_completion_length < 1:
        raise ValueError("Steps, learning rate, temperature, and completion length must be positive")
    if args.batch_size < 1 or args.gradient_accumulation < 1 or args.generations < 2 or args.lora_r < 1:
        raise ValueError("Batch/accumulation/LoRA rank must be positive, generations must be at least 2")
    if args.beta < 0:
        raise ValueError("--beta must be at least 0")
    if not 0 < args.vllm_memory < 1:
        raise ValueError("--vllm-memory must be between 0 and 1")
    effective_batch = args.batch_size * args.gradient_accumulation
    if effective_batch % args.generations:
        raise ValueError("batch-size × gradient-accumulation must be divisible by generations")
    init = None
    if getattr(args, "init_adapter", None):
        adapter = Path(args.init_adapter)
        if not (adapter / "adapter_config.json").is_file() or not (adapter / "adapter_model.safetensors").is_file():
            raise ValueError(f"--init-adapter: no LoRA adapter in {adapter}")
        if json.loads((adapter / "adapter_config.json").read_text()).get("r") != args.lora_r:
            raise ValueError("--init-adapter: the adapter's rank differs from --lora-r")
        init = {"init_adapter": str(adapter.resolve()), "init_adapter_sha256": sha256(adapter / "adapter_model.safetensors")}
    return {
        "model": args.model, "revision": args.revision,
        "max_steps": args.steps, "seed": args.seed, "data_seed": args.seed,
        "per_device_train_batch_size": args.batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation,
        "num_generations": args.generations, "generation_batch_size": effective_batch,
        "max_completion_length": args.max_completion_length,
        "temperature": args.temperature, "learning_rate": args.learning_rate,
        "beta": args.beta, "loss_type": "dapo", "scale_rewards": "group",
        "lora_r": args.lora_r, "lora_alpha": 2 * args.lora_r, "lora_dropout": 0.0,
        "train_dataset_sha256": sha256(train_data),
        "signature_sha256": sha256(args.signatures),
        "environment": environment.identity(env),
        **({"max_tool_calls": env.MAX_TOOL_CALLS} if env.KIND == "tools" else {}),
        **({"use_vllm": True, "vllm_gpu_memory_utilization": args.vllm_memory} if args.vllm else {}),
        **(init or {}),  # continued training: LoRA starts from this adapter; optimizer and schedule start fresh
    }


def grpo_settings(config, output, bf16, save_steps=25, keep=3):
    keys = ("max_steps", "seed", "data_seed", "per_device_train_batch_size",
            "gradient_accumulation_steps", "num_generations", "generation_batch_size",
            "max_completion_length", "temperature", "learning_rate", "beta", "loss_type", "scale_rewards")
    return {**{key: config[key] for key in keys},
            "output_dir": str(output), "logging_steps": 1,
            "save_strategy": "steps", "save_steps": save_steps, "save_total_limit": keep,
            "bf16": bf16, "fp16": not bf16, "gradient_checkpointing": True,
            "remove_unused_columns": False, "report_to": "none", "use_vllm": config.get("use_vllm", False),
            "shuffle_dataset": True, "chat_template_kwargs": {"enable_thinking": False},
            **({"vllm_mode": "colocate", "vllm_gpu_memory_utilization": config["vllm_gpu_memory_utilization"]}
               if config.get("use_vllm") else {}),
            **({"max_tool_calling_iterations": config["max_tool_calls"]} if "max_tool_calls" in config else {})}


def split_layers(model, revision, gpus):
    """A device map for one model on several GPUs: embeddings, final norm and LM head stay on GPU 0, where the
    trainer keeps its own tensors (logits must land there); the transformer layers are spread over all GPUs."""
    from transformers import AutoConfig
    layers = AutoConfig.from_pretrained(model, revision=revision).num_hidden_layers
    first = max(1, layers // gpus - 3)  # GPU 0 also holds the embeddings and LM head
    device_map = {"model.embed_tokens": 0, "model.rotary_emb": 0, "model.norm": 0, "lm_head": 0}
    for index in range(layers):
        device_map[f"model.layers.{index}"] = 0 if index < first else 1 + (index - first) * (gpus - 1) // (layers - first)
    return device_map


def resolve_resume(output, resume, config, arm):
    manifest = output / "run.json"
    if not manifest.exists():
        if output.exists() and any(output.iterdir()):
            raise ValueError(f"Output {output} is not empty and has no run.json; choose a new directory")
        if resume and resume != "auto":
            raise ValueError("Cannot resume without the original run.json")
        return None, None
    previous = json.loads(manifest.read_text())
    if not resume:
        raise ValueError(f"Run exists in {output}; pass --resume auto or choose a new output")
    if previous.get("config") != config or previous.get("arm") != arm:
        raise ValueError("Cannot resume: configuration or arm differs from run.json")
    if previous.get("status") == "completed":
        if not (output / "adapter" / "adapter_config.json").is_file():
            raise ValueError("Completed run is missing its adapter")
        return previous, None
    if resume == "auto":
        candidates = [p for p in output.glob("checkpoint-*")
                      if p.name.split("-")[-1].isdigit() and (p / "trainer_state.json").is_file()]
        if not candidates:
            raise ValueError("No saved checkpoint is available; choose a fresh output to restart")
        checkpoint = max(candidates, key=lambda p: int(p.name.split("-")[-1]))
    else:
        checkpoint = Path(resume).resolve()
        if checkpoint.parent != output.resolve() or not (checkpoint / "trainer_state.json").is_file():
            raise ValueError("Resume checkpoint must belong to this output directory")
    return previous, str(checkpoint)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", required=True, help="an answer or tools environment in environments/")
    parser.add_argument("--arm", choices=("baseline", "verity"), required=True,
                        help="baseline: Guard observes only; verity: Guard enforces")
    parser.add_argument("--train-data", help="task file (default: the environment's training split)")
    parser.add_argument("--signatures", help="marker file (default markers/<env>.json)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default="main")
    parser.add_argument("--output", required=True)
    parser.add_argument("--telemetry")
    parser.add_argument("--steps", type=int, default=250)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--gradient-accumulation", type=int, default=8)
    parser.add_argument("--generations", type=int, default=8)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--max-completion-length", type=int, default=160)
    parser.add_argument("--learning-rate", type=float, default=5e-6)
    parser.add_argument("--beta", type=float, default=0.0, help="KL penalty toward the base model (0: none)")
    parser.add_argument("--lora-r", type=int, default=16, help="LoRA rank (alpha is twice the rank)")
    parser.add_argument("--vllm", action="store_true", help="generate with vLLM colocated on the training GPU")
    parser.add_argument("--vllm-memory", type=float, default=0.35, help="vLLM's share of GPU memory")
    parser.add_argument("--resume", nargs="?", const="auto")
    parser.add_argument("--init-adapter", help="continue training from this LoRA adapter (a run's adapter/ directory); "
                                               "the optimizer and learning-rate schedule start fresh")
    parser.add_argument("--save-steps", type=int, default=25, help="checkpoint interval (preemptible GPUs: lower)")
    parser.add_argument("--keep-checkpoints", type=int, default=3, help="how many recent checkpoints to keep")
    parser.add_argument("--dry-run", action="store_true", help="validate inputs and print the config without a GPU")
    args = parser.parse_args(argv)
    try:
        env = environment.load(args.env)
        if env.KIND not in ("answer", "tools"):
            raise ValueError(f"{args.env} is a {env.KIND} environment; train.py trains on answer and tools environments")
        args.signatures = args.signatures or str(ROOT / f"markers/{env.NAME}.json")
        train_data = Path(args.train_data or env.data_path("train"))
        config = training_config(args, env, train_data)
        tasks = env.tasks("train", path=train_data, minimum=64)
        output = Path(args.output)
        telemetry = args.telemetry or str(output / "telemetry.jsonl")
        # Load (and for the verity arm, validate) the markers before any GPU work.
        guard = Guard(args.signatures, telemetry=telemetry, arm=args.arm, enforce=args.arm == "verity")
        reward = make_reward(env, guard)
        if int(os.environ.get("WORLD_SIZE", "1")) != 1:
            raise ValueError("This paired experiment uses one GPU; launch with python, not distributed accelerate")
        if args.dry_run:
            print(json.dumps({"arm": args.arm, "environment": config["environment"], "tasks": len(tasks),
                              "config": config, "tasks_per_step": config["generation_batch_size"] // args.generations},
                             indent=2))
            return
        previous, checkpoint = resolve_resume(output, args.resume, config, args.arm)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if previous and previous.get("status") == "completed":
        print(f"Already completed {args.steps} steps: {output}")
        return
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        deployment = ROOT / "deployment.json"
        provenance = json.loads(deployment.read_text()) if deployment.is_file() else {}
        commit, dirty = provenance.get("git_commit"), provenance.get("git_dirty")
    run = {"schema": "verity-training-v1", "arm": args.arm, "status": "starting", "config": config,
           "git_commit": commit, "git_dirty": dirty, "started_at": utc_now(),
           "train_data": str(train_data.resolve()), "positions": len(tasks),
           "telemetry": str(Path(telemetry).resolve()), "resume_from": checkpoint,
           "steps_completed": 0, "wall_seconds": 0, "signature_provenance": guard.pack.get("provenance", {}),
           "source_sha256": {name: sha256(ROOT / name) for name in
                             (f"environments/{env.NAME}.py", "verity/guard.py", "verity/markers.py",
                              "verity/environment.py", "train.py")}}
    if previous:
        run["previous_sessions"] = previous.get("previous_sessions", []) + [{
            key: previous.get(key) for key in ("started_at", "finished_at", "wall_seconds", "status", "steps_completed")}]
    write_json(output / "run.json", run)
    trainer = None

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Received signal {signum}; resume from the latest checkpoint")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback, set_seed
        from trl import GRPOConfig, GRPOTrainer
        if not torch.cuda.is_available():
            raise RuntimeError("GRPO training requires a CUDA GPU. Use --dry-run locally, then scripts/hyak.sh on Hyak.")
        set_seed(args.seed)  # before loading the model and initializing LoRA
        bf16 = torch.cuda.is_bf16_supported(including_emulation=False)
        tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        tokenizer.padding_side = "left"
        gpus = torch.cuda.device_count()  # more than one: split the model across them (one process, no DDP)
        model = AutoModelForCausalLM.from_pretrained(args.model, revision=args.revision,
                                                     dtype=torch.bfloat16 if bf16 else torch.float16,
                                                     **({"device_map": split_layers(args.model, args.revision, gpus)}
                                                        if gpus > 1 else {}))
        model.config.use_cache = False
        run.update({"status": "running", "gpu": torch.cuda.get_device_name(0), "gpus": gpus,
                    "model_commit": getattr(model.config, "_commit_hash", None),
                    "packages": {name: importlib.metadata.version(name)
                                 for name in ("torch", "transformers", "trl", "peft", "accelerate", "datasets",
                                              *(("vllm",) if config.get("use_vllm") else ()))}})
        settings = GRPOConfig(**grpo_settings(config, output, bf16, args.save_steps, args.keep_checkpoints))
        run["precision"] = "bfloat16" if bf16 else "float16"
        write_json(output / "run.json", run)

        class Progress(TrainerCallback):
            def on_step_end(self, args, state, control, **kwargs):
                run["steps_completed"] = state.global_step
                run["wall_seconds"] = round(time.monotonic() - started, 3)
                write_json(output / "run.json", run)

        if config.get("init_adapter"):
            from peft import PeftModel
            model = PeftModel.from_pretrained(model, config["init_adapter"], is_trainable=True)
        trainer = GRPOTrainer(
            model=model, processing_class=tokenizer, reward_funcs=[reward],
            environment_factory=guard.episode_class(env) if env.KIND == "tools" else None,
            train_dataset=Dataset.from_list(dataset_rows(tasks)), args=settings,
            peft_config=None if config.get("init_adapter") else LoraConfig(
                r=config["lora_r"], lora_alpha=config["lora_alpha"], lora_dropout=0.0,
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
                task_type="CAUSAL_LM"), callbacks=[Progress()],
        )
        trainer.train(resume_from_checkpoint=checkpoint)
        trainer.save_model(str(output / "adapter"))
        tokenizer.save_pretrained(str(output / "adapter"))
        run["steps_completed"] = trainer.state.global_step
        if trainer.state.global_step != args.steps:
            raise RuntimeError(f"Training stopped at {trainer.state.global_step}, expected {args.steps}")
        run["status"] = "completed"
    except BaseException as error:
        run["status"] = "interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed"
        run["error"] = f"{type(error).__name__}: {error}"
        if trainer is not None:
            run["steps_completed"] = trainer.state.global_step
        raise
    finally:
        run["finished_at"] = utc_now()
        run["wall_seconds"] = round(time.monotonic() - started, 3)
        write_json(output / "run.json", run)
    print(f"Completed {run['steps_completed']} steps: {output / 'adapter'}")


if __name__ == "__main__":
    main()
