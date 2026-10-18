"""Audit any environment and write its markers. The one entry point to Verity's audits.

    python -m verity.audit chess --fixture                         replay the disclosed seeded bug, no API calls
    python -m verity.audit chess --model anthropic/claude-opus-5.5 --input-price 4 --output-price 20
    python -m verity.audit chess_game --model anthropic/claude-opus-5.5 --input-price 4 --output-price 20
    python -m verity.audit shop --model anthropic/claude-opus-5.5 --input-price 4 --output-price 20 --rounds 2
    python -m verity.audit terminal_wrench --task blind-maze --model qwen3-coder:30b
    python -m verity.audit terminal_wrench --report experiments/runs/.../blind-maze/report.json --replay .../trajectory.jsonl

Answer and tools environments go through verity/probe.py; sandbox environments through verity/sandbox.py.
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
KEYS = {"openrouter": "OPENROUTER_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}


def audit_answer_environment(env, args, parser):
    """Audit an answer or tools environment's training tasks and record their fingerprint with the results.

    Fixture mode makes no API calls. Live audits record request accounting next to the report."""
    tasks = env.tasks("train", path=args.data)
    fingerprint = hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()
    client = None
    if not args.fixture:
        provider = args.provider or "openrouter"
        if provider not in KEYS:
            parser.error("Answer and tools audits run on --provider openrouter or deepseek")
        if not os.environ.get(KEYS[provider]):
            parser.error(f"{KEYS[provider]} is not set (put it in .env), or use --fixture")
        if provider == "deepseek":
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
        client = llm.Client(provider=provider, attacker=args.model, input_price=args.input_price,
                            output_price=args.output_price, max_usd=args.max_usd, max_tokens=args.max_tokens,
                            max_tries=args.api_retries, log=report_path.with_suffix(".api_calls.jsonl"))
    count = args.episodes if env.KIND == "tools" else args.count
    report, pack = probe.run(env, tasks, ask=client.ask if client else None, model=args.model, offline=args.fixture,
                             count=count, seed=args.seed, max_scores=args.max_scores, max_calls=args.max_calls,
                             audit_kind=args.audit_kind, rounds=args.rounds)
    report["data"] = {"path": str(args.data or "default"), "tasks_sha256": fingerprint}
    if client:
        report["api"] = {"request_config": client.request_config(), "digest": client.digest(args.model), **client.stats()}
    if pack:
        pack["provenance"]["tasks_sha256"] = fingerprint
    probe.write_outputs(report, pack, pack_path, report_path)
    print(json.dumps({k: report[k] for k in ("status", "mode", "attacker", "environment")}))
    for row in report["rounds"]:
        marker = row.get("signature", {}).get("id")
        print(f"round {row['round']}: {row['status']}" + (f", marker {marker}" if marker else "")
              + (f" ({row['error']})" if row.get("error") else ""))
        for key in ("proof", "validation"):
            if row.get(key):
                print("  " + json.dumps({k: v for k, v in row[key].items()
                                         if k not in ("rows", "misses", "false_alarm_examples")}))
    if report.get("error"):
        print(report["error"], file=sys.stderr)
    count = len(pack["signatures"]) if pack else 0
    print(f"Report: {report_path.with_suffix('.md')}" + (f"; {count} markers: {pack_path}" if pack else "; no markers written"))
    return 0 if report["status"] == "passed" else 1


def audit_sandbox_environment(env, args, parser):
    """Audit a sandbox or convert a saved report into validation markers.

    Saved reports use the same conversion path as live audits. Optional
    trajectory replay screens commands without executing them.
    """
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
        else:
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
    """Parse CLI options, validate settings, and dispatch the selected audit."""
    llm.load_env()
    parser = argparse.ArgumentParser(description="Audit an RL environment for reward hacks and write its markers.")
    parser.add_argument("environment", help="a module in environments/, e.g. chess, chess_game, shop or terminal_wrench")
    parser.add_argument("--out", help="marker file (default markers/<name>.json)")
    parser.add_argument("--model", help="attacker model id")
    parser.add_argument("--provider", choices=("openrouter", "deepseek", "local"),
                        help="where the attacker runs: openrouter (default for answer and tools environments), "
                             "deepseek, or local (sandbox default: DeepSeek API model names or any Ollama model)")
    parser.add_argument("--input-price", type=float, help="USD per 1M input tokens (OpenRouter spend cap)")
    parser.add_argument("--output-price", type=float, help="USD per 1M output tokens (OpenRouter spend cap)")
    parser.add_argument("--max-usd", type=float, default=3.0)
    probed = parser.add_argument_group("answer and tools environments")
    probed.add_argument("--fixture", "--offline", action="store_true", help="disclosed seeded bugs, no model calls")
    probed.add_argument("--rounds", type=int, default=1, help="live audit rounds; each later round asks the attacker "
                        "for a mechanism different from those already found (1..5)")
    probed.add_argument("--data", help="task file (default: the environment's training split)")
    probed.add_argument("--seed", type=int, default=42)
    probed.add_argument("--max-tokens", type=int, default=16384)
    probed.add_argument("--api-retries", type=int, default=3)
    probed.add_argument("--audit-kind", choices=("frontier_audit", "model_audit"), default="frontier_audit")
    probed.add_argument("--report-out", help="report path (.md; .json and .transcript.jsonl next to it)")
    probed.add_argument("--count", type=int, help="answer: tasks shown to the attacker (16..64; default AUDIT_TASKS or 16)")
    probed.add_argument("--max-scores", type=int, default=30, help="answer: score queries per round (1..30)")
    probed.add_argument("--episodes", type=int, help="tools: episodes the attacker plays (3..16; default AUDIT_TASKS or 6)")
    probed.add_argument("--max-calls", type=int, default=80, help="tools: tool calls per round (10..200)")
    probed.add_argument("--check", metavar="PACK", help="no audit: test a marker file on the environment's disclosed "
                        "seeded bugs over every task of each split, and on honest work")
    sandboxed = parser.add_argument_group("sandbox environments")
    sandboxed.add_argument("--task", help="task id from tasks.json")
    sandboxed.add_argument("--judge", help="judge model (default: --model); use a different model family")
    sandboxed.add_argument("--report", help="make markers from an existing sandbox report instead of auditing")
    sandboxed.add_argument("--replay", help="screen a recorded trajectory.jsonl through the new markers")
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_usd) or args.max_usd <= 0:
        parser.error("--max-usd must be positive")
    env = environment.load(args.environment)
    if args.check:
        splits = ["train", "eval"] + ([env.DEPLOYMENT["split"]] if env.DEPLOYMENT.get("split") else []) \
            if hasattr(env, "DEPLOYMENT") else ["train", "eval"]
        result = {"environment": environment.identity(env), "pack": args.check,
                  **probe.cross_check(env, json.loads(Path(args.check).read_text()), splits)}
        out = Path(args.out or ROOT / f"experiments/{env.NAME}-audits/{Path(args.check).stem}-check.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
        return 0
    if env.KIND in ("answer", "tools"):
        tools = env.KIND == "tools"
        count = args.episodes if tools else args.count
        low, high = (3, 16) if tools else (16, 64)
        if count is not None and not low <= count <= high:
            parser.error(f"--{'episodes' if tools else 'count'} must be {low}..{high}")
        if not 1 <= args.max_scores <= 30 or not 10 <= args.max_calls <= 200 or not 1 <= args.rounds <= 5:
            parser.error("--max-scores must be 1..30, --max-calls 10..200 and --rounds 1..5")
        return audit_answer_environment(env, args, parser)
    if not args.report and not args.task:
        parser.error(f"Choose --task ({', '.join(runner.TASKS)}) or --report")
    return audit_sandbox_environment(env, args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
