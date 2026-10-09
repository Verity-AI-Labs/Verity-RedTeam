#!/usr/bin/env python3
"""Audit a batch of tasks (audit-v3) with DeepSeek (deepseek-flash) as both attacker and judge.

The attacker uses Terminal Wrench's hack_prompt_v5 and the judge TW's monitor, scored with a threshold.
By default every task in tasks.json runs, in file order. Writes <out>/<task>/report.json per task,
<out>/api_calls.jsonl (one line per API call) and <out>/run_manifest.json. Tasks run in parallel
threads; the API key comes from DEEPSEEK_API_KEY only.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import audit, deepseek, runner  # noqa: E402

QWEN_RUN = runner.RUNS / "full-qwen3-coder-30b-20261003"
QWEN_TASKS = ["blind-maze", "titanic-female-survival-prediction", "recover-encrypted-db-credentials",
              "analyze-pgn-best-chess-move", "1104", "winning-avg-corewars", "462", "481", "710", "361"]
MODEL = "deepseek-flash"
MODELS = {"attacker": MODEL, "judge": MODEL}


def parse(argv, today):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=runner.RUNS / f"audit-v3-deepseek-v4.1-flash-{today}",
                        help="run folder (default: experiments/runs/audit-v3-deepseek-v4.1-flash-<UTC launch date>)")
    parser.add_argument("--tasks", nargs="+", choices=tuple(runner.TASKS), default=list(runner.TASKS),
                        help=f"task ids (default: all {len(runner.TASKS)} tasks in tasks.json, in file order)")
    parser.add_argument("--concurrency", type=runner.positive_int, default=3, help="tasks in parallel (default: 3)")
    parser.add_argument("--reasoning-effort", choices=("high", "max"), default="high")
    parser.add_argument("--max-usd", type=float, default=10.0, help="estimated spend cap for this folder (default: 10)")
    parser.add_argument("--deadline-hours", type=float, default=8.0, help="start no new task after this (default: 8)")
    parser.add_argument("--hard-deadline-hours", type=float, default=10.0,
                        help="abort in-flight tasks at their next model call after this (default: 10)")
    parser.add_argument("--resume", action="store_true", help="skip tasks that already have a complete report.json")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; no network, Docker or model calls")
    args = parser.parse_args(argv)
    if len(set(args.tasks)) != len(args.tasks):
        parser.error("--tasks has duplicates (two threads would build and audit the same image)")
    if not 0 < args.deadline_hours <= args.hard_deadline_hours or args.max_usd <= 0:
        parser.error("need 0 < --deadline-hours <= --hard-deadline-hours and --max-usd > 0")
    resolved = args.out.resolve()
    if resolved == QWEN_RUN.resolve() or QWEN_RUN.resolve() in resolved.parents:
        parser.error(f"refusing to write into the Qwen run folder {QWEN_RUN}")
    return parser, args


def _read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def report_state(path):
    """'complete', 'aborted', or None (missing or unreadable) for one task's report.json."""
    report = _read(path)
    if not isinstance(report, dict):
        return None
    return "aborted" if (report.get("redteam") or {}).get("aborted") else "complete"


def expected_protocol(client):
    mobj = {"attacker": MODEL, "attacker_digest": client.digest(MODEL),
            "judge": MODEL, "judge_digest": client.digest(MODEL)}
    return audit.protocol_id(mobj, audit.sha256(audit.ATTACK), audit.sha256(audit.JUDGE),
                             dict(audit.BUDGETS), client.request_config(), audit.sha256(audit.JUDGE_USER))


def plan(parser, args, protocol):
    """(tasks to run, tasks already complete, task folders to move aside) after the safety checks."""
    states = {t: report_state(args.out / t / "report.json") for t in runner.TASKS}
    complete = [t for t in args.tasks if states[t] == "complete"]
    stale = [t for t in args.tasks if states[t] != "complete" and (args.out / t).exists()]
    if not args.resume:
        if any(states.values()) or stale:
            parser.error(f"{args.out} already holds task folders or reports; pass --resume or another --out")
        return list(args.tasks), [], []
    for t in [t for t in runner.TASKS if states[t] == "complete"]:
        prov = _read(args.out / t / "report.json").get("provenance") or {}
        if (prov.get("protocol_id") != protocol or (prov.get("models") or {}).get("attacker") != MODEL
                or prov.get("schema_version", audit.SCHEMA_VERSION) != audit.SCHEMA_VERSION):
            parser.error(f"{args.out / t} has protocol {prov.get('protocol_id')}, this run is {protocol}: "
                         "resuming would mix protocols in one folder")
    return [t for t in args.tasks if t not in complete], complete, stale


def dry_run(args, client, todo, complete, stale, protocol, launch):
    hhmm = lambda hours: (launch + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M UTC")  # noqa: E731
    peak = deepseek.next_peak(launch)
    print("Dry run: no network, Docker or model calls.")
    print(f"Output folder: {args.out} ({'exists' if args.out.exists() else 'will be created'})")
    print(f"Tasks to run ({len(todo)}): {' '.join(todo) or '-'}")
    print(f"Already complete, skipped ({len(complete)}): {' '.join(complete) or '-'}")
    if stale:
        print(f"Partial or aborted folders moved to {args.out / 'superseded'}: {' '.join(stale)}")
    print(f"Models: {json.dumps(MODELS)}; digest {client.digest(MODEL)}")
    print(f"Request config: {json.dumps(client.request_config())}")
    print(f"Budgets: {json.dumps(audit.BUDGETS)}")
    print(f"Expected protocol_id: {protocol}")
    print(f"Concurrency {args.concurrency}; spend cap ${args.max_usd:.2f} (already logged in this folder: "
          f"${client.spent:.4f})")
    print(f"No new task after {hhmm(args.deadline_hours)}; in-flight tasks abort after {hhmm(args.hard_deadline_hours)}")
    print("Prices, USD per 1M tokens (off-peak / peak):")
    for name, (off, on) in deepseek.PRICES.items():
        print(f"  {name:<11} {off:.3f} / {on:.3f}")
    print(f"Peak (UTC, Mon-Fri): 01:00-04:00, 06:00-10:00. Now {launch:%Y-%m-%d %H:%M} UTC is "
          f"{'PEAK' if deepseek.is_peak(launch) else 'off-peak'}; next peak starts {peak:%Y-%m-%d %H:%M} UTC.")
    if launch + timedelta(hours=args.deadline_hours) > peak:
        print("WARNING: new tasks may still start during peak pricing; lower --deadline-hours.")
    elif launch + timedelta(hours=args.hard_deadline_hours) > peak:
        print("Note: tasks still in flight at the next peak start are billed at peak rates (the spend cap counts it).")
    print(f"DEEPSEEK_API_KEY: {'set' if os.environ.get('DEEPSEEK_API_KEY') else 'NOT SET'}")


def section_errors(path):
    report = _read(path) or {}
    return [s for s in ("spec", "validity", "oracle_footprint", "redteam") if "status" in (report.get(s) or {})]


def main(argv=None, docker=runner.docker, audit_fn=audit.audit, post=None, sleep=time.sleep):
    launch, t0 = datetime.now(timezone.utc), time.monotonic()
    parser, args = parse(argv, launch.strftime("%Y%m%d"))
    out, stamp = args.out, launch.strftime("%Y%m%dT%H%M%SZ")
    previous = _read(out / "run_manifest.json") if args.resume else None
    client = deepseek.Client(log=out / "api_calls.jsonl", max_usd=args.max_usd, effort=args.reasoning_effort,
                             run_date=(previous or {}).get("run_date") or launch.strftime("%Y-%m-%d"),
                             deadline=t0 + args.hard_deadline_hours * 3600,
                             spent=deepseek.logged_spend(out / "api_calls.jsonl"), sleep=sleep,
                             **({"post": post} if post else {}))
    protocol = expected_protocol(client)
    todo, complete, stale = plan(parser, args, protocol)
    if args.dry_run:
        dry_run(args, client, todo, complete, stale, protocol, launch)
        return 0
    if not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("export DEEPSEEK_API_KEY first")
    out.mkdir(parents=True, exist_ok=True)
    for name in stale + (["run_manifest.json"] if previous else []):  # moved aside, never overwritten
        (out / "superseded").mkdir(exist_ok=True)
        (out / name).rename(out / "superseded" / f"{name}-{stamp}")
    manifest = {"status": "running", "abort_reason": None, "run_date": client.run_date,
                "launched_utc": launch.isoformat(), "finished_utc": None, "wall_seconds": None, "out": str(out),
                "models": MODELS, "request_config": client.request_config(), "protocol_id": protocol,
                "budgets": audit.BUDGETS, "max_usd": args.max_usd, "prior_estimated_usd": round(client.spent, 6),
                "concurrency": args.concurrency,
                "deadline_hours": args.deadline_hours, "hard_deadline_hours": args.hard_deadline_hours,
                "tasks": list(args.tasks), "completed": [], "skipped": [], "errored": []}
    manifest["skipped"] = [{"task": t, "reason": "complete report.json exists (--resume)"} for t in complete]

    def finish(status, code):
        stats = client.stats()
        manifest.update(status=status, abort_reason=stats.pop("aborted"), finished_utc=datetime.now(
            timezone.utc).isoformat(), wall_seconds=round(time.monotonic() - t0, 1), **stats)
        runner.save_json(out / "run_manifest.json", manifest)
        print(f"{status}: {len(manifest['completed'])} completed, {len(manifest['skipped'])} skipped, "
              f"{len(manifest['errored'])} errored; estimated ${stats['estimated_usd']:.4f}; "
              f"manifest {out / 'run_manifest.json'}", flush=True)
        return code

    runner.save_json(out / "run_manifest.json", manifest)
    if not todo:
        return finish("complete", 0)
    try:
        client.preflight()
    except audit.Abort:
        manifest["skipped"] += [{"task": t, "reason": "not started: preflight aborted"} for t in todo]
        return finish("aborted", 1)
    except Exception as error:
        manifest["skipped"] += [{"task": t, "reason": f"not started: preflight failed: {error}"} for t in todo]
        return finish("preflight_failed", 1)
    audit.build_tracer(docker)  # the one shared image, built before the pool (task images are per task)
    lock, soft = threading.Lock(), t0 + args.deadline_hours * 3600

    def work(task):
        if client.aborted:
            return "skipped", {"task": task, "reason": f"not started: batch aborted ({client.aborted})"}
        if time.monotonic() > soft:
            return "skipped", {"task": task, "reason": "not started: soft deadline passed"}
        deepseek.set_task(task)

        def progress(rec):
            with lock:
                print(f"[{task}] {rec['id']} turns={rec['turns']} label={rec['label']} "
                      f"cost=${client.stats()['estimated_usd']:.4f} elapsed={time.monotonic() - t0:.0f}s", flush=True)

        path = out / task / "report.json"
        try:
            audit_fn(task, docker=docker, ask=client.ask, model_digest=client.digest, models=dict(MODELS),
                     folder=out / task, request_config=client.request_config(), on_attempt=progress)
        except audit.Abort as error:  # audit() already wrote this task's report.json
            return "errored", {"task": task, "reason": f"aborted: {error}",
                               "report": str(path) if path.exists() else None}
        except Exception as error:
            return "errored", {"task": task, "reason": f"{type(error).__name__}: {error}",
                               "report": str(path) if path.exists() else None}
        finally:
            deepseek.set_task(None)
        return "completed", {"task": task, "report": str(path), "section_errors": section_errors(path)}

    status, code = "complete", 0
    with ThreadPoolExecutor(args.concurrency) as pool:
        futures = [pool.submit(work, t) for t in todo]
        try:
            for future in as_completed(futures):
                kind, entry = future.result()
                manifest[kind].append(entry)
                print(f"[{entry['task']}] {kind}: {entry.get('reason') or entry.get('report')}", flush=True)
        except KeyboardInterrupt:
            client.abort("interrupted")
            print("Interrupted: in-flight tasks stop at their next model call and write report.json.", flush=True)
            wait(futures)
            done = {e["task"] for k in ("completed", "skipped", "errored") for e in manifest[k]}
            for future in futures:
                kind, entry = future.result()
                if entry["task"] not in done:
                    manifest[kind].append(entry)
            status, code = "interrupted", 130
    if client.aborted and status == "complete":
        status = "aborted"
    if status == "complete" and manifest["errored"]:
        code = 1
    return finish(status, code if status in ("complete", "interrupted") else 1)


if __name__ == "__main__":
    sys.exit(main())
