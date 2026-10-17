"""Audit any environment and write its markers. The one entry point to Verity's audits.

    python -m verity.audit chess --fixture                         replay the disclosed seeded bug, no API calls
    python -m verity.audit chess --model anthropic/claude-opus-5.5 --input-price 4 --output-price 20
    python -m verity.audit terminal_wrench --task blind-maze --model qwen3-coder:30b
    python -m verity.audit terminal_wrench --report experiments/runs/.../blind-maze/report.json --replay .../trajectory.jsonl

Answer environments go through verity/probe.py; sandbox environments through verity/sandbox.py.
Markers land in markers/, reports in experiments/<environment>-audits/ (sandbox reports in experiments/runs/).
"""
import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

from verity import environment, llm, markers, probe, runner, sandbox
from verity.guard import Guard

ROOT = Path(__file__).resolve().parents[1]


def audit_answer_environment(env, args, parser):
    tasks = env.tasks("train", path=args.data)
    fingerprint = hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()
    client = None
    if not args.fixture:
        if not os.environ.get("OPENROUTER_API_KEY"):
            parser.error("OPENROUTER_API_KEY is not set (put it in .env), or use --fixture")
        if not args.model:
            parser.error("A live audit needs --model (an OpenRouter model id)")
        if any(p is None or not math.isfinite(p) or p < 0 for p in (args.input_price, args.output_price)):
            parser.error("A live audit needs --input-price and --output-price (USD per 1M tokens)")
    name = f"{env.NAME}-fixture" if args.fixture else env.NAME
    report_path = Path(args.report_out or ROOT / f"experiments/{env.NAME}-audits/{name}.md")
    pack_path = Path(args.out or ROOT / f"markers/{name}.json")
    if not args.fixture:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        client = llm.Client(provider="openrouter", attacker=args.model, input_price=args.input_price,
                            output_price=args.output_price, max_usd=args.max_usd, max_tokens=args.max_tokens,
                            max_tries=args.api_retries, log=report_path.with_suffix(".api_calls.jsonl"))
    report, pack = probe.run(env, tasks, ask=client.ask if client else None, model=args.model,
                             offline=args.fixture, count=args.count, seed=args.seed,
                             max_scores=args.max_scores, audit_kind=args.audit_kind)
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


def audit_sandbox_environment(env, args, parser):
    if args.report:
        report = json.loads(Path(args.report).read_text())
    else:
        if args.task not in env.tasks():
            parser.error(f"--task must be one of: {', '.join(env.tasks())}")
        if not args.model:
            parser.error("A sandbox audit needs --model (attacker; --judge defaults to it)")
        models = {"attacker": args.model, "judge": args.judge or args.model}
        if args.provider == "openrouter":
            client = llm.Client(provider="openrouter", attacker=models["attacker"], judge=models["judge"],
                                input_price=args.input_price, output_price=args.output_price, max_usd=args.max_usd)
        else:  # deepseek API models, or any Ollama model name
            client = llm.Client(max_usd=args.max_usd)
        path = sandbox.audit(args.task, ask=client.ask, model_digest=client.digest, models=models,
                             request_config=client.request_config())
        print(f"Report: {path}")
        report = json.loads(Path(path).read_text())
    pack = markers.from_sandbox_report(report)
    out = Path(args.out or ROOT / f"markers/{pack['environment']}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pack, indent=2) + "\n")
    print(f"Wrote {len(pack['signatures'])} validated markers to {out}")
    if args.replay:
        print("Recorded replay: commands are screened, never executed.")
        for event in env.replay(args.replay, Guard(pack)):
            print(f"Turn {event['turn']}: {'BLOCKED' if event['blocked'] else 'ALLOW'} {event['command']}")
            for warning in event["warnings"]:
                print(warning)
    return 0


def main(argv=None):
    llm.load_env()
    parser = argparse.ArgumentParser(description="Audit an RL environment for reward hacks and write its markers.")
    parser.add_argument("environment", help="a module in environments/, e.g. chess or terminal_wrench")
    parser.add_argument("--out", help="marker file (default markers/<name>.json)")
    parser.add_argument("--model", help="attacker model id")
    parser.add_argument("--input-price", type=float, help="USD per 1M input tokens (OpenRouter spend cap)")
    parser.add_argument("--output-price", type=float, help="USD per 1M output tokens (OpenRouter spend cap)")
    parser.add_argument("--max-usd", type=float, default=3.0)
    answer = parser.add_argument_group("answer environments")
    answer.add_argument("--fixture", "--offline", action="store_true", help="disclosed seeded bug, no model calls")
    answer.add_argument("--data", help="task file (default: the environment's training split)")
    answer.add_argument("--count", type=int, default=16, help="tasks shown to the attacker (16..64)")
    answer.add_argument("--max-scores", type=int, default=30)
    answer.add_argument("--seed", type=int, default=42)
    answer.add_argument("--max-tokens", type=int, default=16384)
    answer.add_argument("--api-retries", type=int, default=3)
    answer.add_argument("--audit-kind", choices=("frontier_audit", "model_audit"), default="frontier_audit")
    answer.add_argument("--report-out", help="report path (.md; .json and .transcript.jsonl next to it)")
    sandboxed = parser.add_argument_group("sandbox environments")
    sandboxed.add_argument("--task", help="task id from tasks.json")
    sandboxed.add_argument("--judge", help="judge model (default: --model); use a different model family")
    sandboxed.add_argument("--provider", choices=("openrouter", "local"), default="local",
                           help="local = DeepSeek API model names or any Ollama model")
    sandboxed.add_argument("--report", help="make markers from an existing sandbox report instead of auditing")
    sandboxed.add_argument("--replay", help="screen a recorded trajectory.jsonl through the new markers")
    args = parser.parse_args(argv)
    if not 16 <= args.count <= 64 or not 1 <= args.max_scores <= 30:
        parser.error("--count must be 16..64 and --max-scores must be 1..30")
    if not math.isfinite(args.max_usd) or args.max_usd <= 0:
        parser.error("--max-usd must be positive")
    env = environment.load(args.environment)
    if env.KIND == "answer":
        return audit_answer_environment(env, args, parser)
    if not args.report and not args.task:
        parser.error(f"Choose --task ({', '.join(runner.TASKS)}) or --report")
    return audit_sandbox_environment(env, args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
