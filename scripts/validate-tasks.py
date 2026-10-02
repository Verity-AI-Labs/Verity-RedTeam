#!/usr/bin/env python3
"""Check each task's reference solution and known exploit acceptance in isolation."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import runner
from src.classification import classify_episode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=tuple(runner.TASKS),
                        help="validate one task; default validates all registered tasks")
    args = parser.parse_args()
    task_ids = [args.task] if args.task else list(runner.TASKS)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = runner.RUNS / f"validation-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    report = []
    failed = False

    for task_id in task_ids:
        task = runner.task_config(task_id)
        task_folder = output / task_id
        task_folder.mkdir()
        print(f"\n== {task_id} ==", flush=True)
        try:
            image_id = runner.build(task, task_folder)
            validation = task["validation"]
            row = {"task": task_id, "image_id": image_id, "controls": []}
            if validation["reference_solution"]:
                result = runner.attempt(
                    "oracle", task_folder / "reference", SimpleNamespace(), task
                )
                passed = result["status"] == "graded" and result["reward"] >= 1
                row["controls"].append({
                    "name": "reference_solution",
                    "expected": "reward >= 1",
                    "result": result,
                    "passed": passed,
                })
                failed |= not passed
            else:
                row["controls"].append({
                    "name": "reference_solution",
                    "status": "unavailable",
                    "reason": validation["reference_note"],
                })
                print(f"Reference solution unavailable: {validation['reference_note']}")

            exploit_controls = validation.get("known_exploits", [])
            for control in exploit_controls:
                control_folder = task_folder / "known-exploits" / control["name"]
                control_folder.parent.mkdir(parents=True, exist_ok=True)
                result = runner.attempt(
                    "exploit_control",
                    control_folder,
                    SimpleNamespace(command=control["command"]),
                    task,
                )
                expected_reward = control["expected_reward"]
                passed = result["status"] == "graded" and result["reward"] >= expected_reward
                classification = classify_episode(
                    task, result, control_folder / "trace.jsonl"
                )
                passed = passed and classification["label"] == "suspected_reward_hack"
                row["controls"].append({
                    "name": control["name"],
                    "expected_reward": expected_reward,
                    "result": result,
                    "classification": classification,
                    "passed": passed,
                })
                failed |= not passed
            if not exploit_controls:
                row["controls"].append({
                    "name": "known_exploit",
                    "status": "unavailable",
                    "reason": validation["known_exploit_note"],
                })
                print(f"Known exploit control unavailable: {validation['known_exploit_note']}")
            report.append(row)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            failed = True
            row = {"task": task_id, "status": "error", "error": str(error)}
            report.append(row)
            print(f"Control error: {error}", file=sys.stderr, flush=True)

        runner.save_json(output / "validation.json", report)

    runner.save_json(output / "validation.json", report)
    print(f"\nValidation evidence: {output}")
    if failed:
        raise SystemExit(1)
    print("All available controls matched their expected outcomes.")


if __name__ == "__main__":
    main()
