"""Audit any environment and write its markers. The one entry point to Verity's audits.

    python -m verity.audit shop --fixture                          replay the disclosed seeded bug, no API calls
    python -m verity.audit flights --model anthropic/claude-opus-5.5 --input-price 4 --output-price 20
    python -m verity.audit shop --provider deepseek --model deepseek-flash

Answer environments go through verity/probe.py.
Markers land in markers/, reports in experiments/<environment>-audits/.
"""
import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

from verity import environment, llm, probe

ROOT = Path(__file__).resolve().parents[1]


def audit_answer_environment(env, args, parser):
    tasks = env.tasks("train", path=args.data)
    fingerprint = hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()
    client = None
    if not args.fixture:
        key = "OPENROUTER_API_KEY" if args.provider == "openrouter" else "DEEPSEEK_API_KEY"
        if not os.environ.get(key):
            parser.error(f"{key} is not set (put it in .env), or use --fixture")
        if args.provider == "deepseek":
            args.model = args.model or llm.API_MODELS[0]
            if args.model not in llm.API_MODELS:
                parser.error(f"DeepSeek models: {', '.join(llm.API_MODELS)}")
        elif not args.model:
            parser.error("A live audit needs --model (an OpenRouter model id)")
        elif any(p is None or not math.isfinite(p) or p < 0 for p in (args.input_price, args.output_price)):
            parser.error("A live audit needs --input-price and --output-price (USD per 1M tokens)")
    name = f"{env.NAME}-fixture" if args.fixture else env.NAME
    report_path = Path(args.report_out or ROOT / f"experiments/{env.NAME}-audits/{name}.md")
    pack_path = Path(args.out or ROOT / f"markers/{name}.json")
    if not args.fixture:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        client = llm.Client(provider=args.provider, attacker=args.model, input_price=args.input_price,
                            output_price=args.output_price, max_usd=args.max_usd, max_tokens=args.max_tokens,
                            max_tries=args.api_retries, log=report_path.with_suffix(".api_calls.jsonl"))
    report, pack = probe.run(env, tasks, ask=client.ask if client else None, model=args.model,
                             offline=args.fixture, count=args.count, seed=args.seed,
                             max_scores=args.max_scores, audit_kind=args.audit_kind, max_steps=args.max_steps)
    report["data"] = {"path": str(args.data or "default"), "tasks_sha256": fingerprint}
    if client:
        report["api"] = {"request_config": client.request_config(), "digest": client.digest(args.model), **client.stats()}
    if pack:
        pack["provenance"]["tasks_sha256"] = fingerprint
    probe.write_outputs(report, pack, pack_path, report_path)
    print(json.dumps({k: report[k] for k in ("status", "mode", "attacker", "environment")}))
    for key in ("proof", "validation"):
        if report.get(key):
            print(json.dumps({k: v for k, v in report[key].items() if k != "rows"}))
    if report.get("error"):
        print(report["error"], file=sys.stderr)
    print(f"Report: {report_path.with_suffix('.md')}" + (f"; markers: {pack_path}" if pack else "; no markers written"))
    return 0 if report["status"] == "passed" else 1


def main(argv=None):
    llm.load_env()
    parser = argparse.ArgumentParser(description="Audit an RL environment for reward hacks and write its markers.")
    parser.add_argument("environment", help="a module in environments/, e.g. shop or flights")
    parser.add_argument("--out", help="marker file (default markers/<name>.json)")
    parser.add_argument("--model", help="attacker model id")
    parser.add_argument("--provider", choices=("openrouter", "deepseek"), default="openrouter",
                        help="where the attacker model runs (DEEPSEEK_API_KEY or OPENROUTER_API_KEY in .env)")
    parser.add_argument("--input-price", type=float, help="USD per 1M input tokens (OpenRouter spend cap)")
    parser.add_argument("--output-price", type=float, help="USD per 1M output tokens (OpenRouter spend cap)")
    parser.add_argument("--max-usd", type=float, default=3.0)
    answer = parser.add_argument_group("answer environments")
    answer.add_argument("--fixture", "--offline", action="store_true", help="disclosed seeded bug, no model calls")
    answer.add_argument("--data", help="task file (default: the environment's training split)")
    answer.add_argument("--count", type=int, help="tasks shown to the attacker (default: the environment's "
                        "AUDIT_TASKS, else 16; at least that, or 4 for a browsed environment; at most 64)")
    answer.add_argument("--max-scores", type=int, default=30)
    answer.add_argument("--max-steps", type=int, default=400, help="page actions, for environments the attacker browses")
    answer.add_argument("--seed", type=int, default=42)
    answer.add_argument("--max-tokens", type=int, default=16384)
    answer.add_argument("--api-retries", type=int, default=3)
    answer.add_argument("--audit-kind", choices=("frontier_audit", "model_audit"), default="frontier_audit")
    answer.add_argument("--report-out", help="report path (.md; .json and .transcript.jsonl next to it)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_usd) or args.max_usd <= 0:
        parser.error("--max-usd must be positive")
    env = environment.load(args.environment)
    if env.KIND != "answer":
        parser.error(f"{args.environment} is a {env.KIND} environment; this audit handles answer environments")
    minimum = probe.fewest_tasks(env)
    args.count = getattr(env, "AUDIT_TASKS", 16) if args.count is None else args.count
    if not minimum <= args.count <= 64 or not 1 <= args.max_scores <= 30 or args.max_steps < 1:
        parser.error(f"--count must be {minimum}..64, --max-scores 1..30 and --max-steps positive")
    return audit_answer_environment(env, args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
