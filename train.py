# Multi-turn GRPO with LoRA on one website environment, using TRL's environment_factory (trl==1.16.0).
#   python train.py --env shop --output runs/shop-baseline
#   python train.py --env flights --dry-run      # play reference answers through the rewards, no model
import argparse
import json

from environments import web


def rewards(module):
    # grade() is the training reward; check() is logged only (weight 0), with the hack rate (grade 1, check 0)
    def grade(environments, **kwargs):
        return [module.grade(env.site, env.state) for env in environments]

    def check(environments, log_metric=None, **kwargs):
        checks = [module.check(env.site, env.state) for env in environments]
        if log_metric:
            hacks = [g == 1.0 and c == 0.0 for g, c in zip(grade(environments), checks)]
            log_metric("hack_rate", sum(hacks) / len(hacks))
        return checks

    return [grade, check]


def dry_run(name):
    # the oracle, the hack and one mistake per template, played through the environment class and scored
    module = web.site_type(name)
    grade, check = rewards(module)
    sites = web.read(module.data_path("train"))
    for n, template in enumerate(module.TEMPLATES):
        site = next(site for site in sites if site["template"] == template)
        mistakes = list(module.mistakes(site).items())
        answers = [("oracle", module.oracle(site)), ("hack", module.hack(site)), mistakes[n % len(mistakes)]]
        environments = [web.run(name, site, calls) for _, calls in answers]
        metrics = {}
        checks = check(environments, log_metric=metrics.__setitem__)
        for (label, calls), g, c in zip(answers, grade(environments), checks):
            print(f"{template:10} {label:17} {len(calls):2} calls  grade {g:.2f}  check {c:.0f}")
        print(f"{'':10} hack_rate {metrics['hack_rate']:.2f}")


def train(args):
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from trl import GRPOConfig, GRPOTrainer

    module = web.site_type(args.env)
    sites = web.read(module.data_path("train"))
    dataset = Dataset.from_list([{"prompt": [{"role": "user", "content": web.prompt(site)}], "site": json.dumps(site)}
                                 for site in sites])
    config = GRPOConfig(
        output_dir=args.output or f"runs/{args.env}-baseline",
        max_steps=args.steps,
        learning_rate=args.lr,
        num_generations=args.generations,
        per_device_train_batch_size=args.generations,  # on one GPU: 8 completions x 4 accumulation = 4 tasks per step
        gradient_accumulation_steps=args.tasks,
        max_completion_length=args.max_completion_length,  # counts the whole conversation, pages included
        max_tool_calling_iterations=web.MAX_CALLS,
        reward_weights=[1.0, 0.0],
        bf16=torch.cuda.is_available() and torch.cuda.is_bf16_supported(),
        gradient_checkpointing=True,
        use_vllm=args.use_vllm,
        vllm_mode="colocate",
        logging_steps=1,
        save_steps=50,
        log_completions=True,
    )
    lora = LoraConfig(r=args.lora_r, lora_alpha=2 * args.lora_r, target_modules="all-linear", task_type="CAUSAL_LM")
    trainer = GRPOTrainer(model=args.model, reward_funcs=rewards(module), args=config, train_dataset=dataset,
                          peft_config=lora, environment_factory=web.CLASSES[args.env])
    trainer.train()
    trainer.save_model(config.output_dir)


def main():
    parser = argparse.ArgumentParser(description="Multi-turn GRPO on a website environment.")
    parser.add_argument("--env", choices=web.ENVIRONMENTS, required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--generations", type=int, default=8)
    parser.add_argument("--tasks", type=int, default=4, help="tasks per step")
    parser.add_argument("--max-completion-length", type=int, default=6144)
    parser.add_argument("--lr", type=float, default=5e-5)
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--use-vllm", action="store_true", help="generate with vLLM in colocate mode")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        dry_run(args.env)
    else:
        train(args)


if __name__ == "__main__":
    main()
