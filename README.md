# Verity RedTeam

Verity audits reinforcement learning environments for reward hacks before they are used for training. For each task it runs the reference solution and grader to check that the grader is valid, records what the grader actually reads, then gives an attacker model four attempts to pass the grader without doing the real work. Passing attempts are replayed under trace and reviewed by a judge model. Docker runs the task containers; Ollama runs the models locally.

Each run writes one `report.json` per task with seven sections: provenance, spec, validity, oracle_footprint, redteam, profile, scorecard. See [docs/audit.md](docs/audit.md) for the full schema and evidence rules.

## Current Tasks

Ten Terminal Wrench tasks, all registered in `tasks.json`:

| Task ID | Description |
|---|---|
| blind-maze | Explore a blind maze and write its map |
| titanic-female-survival-prediction | Predict female Titanic survival outcomes |
| recover-encrypted-db-credentials | Recover credentials from an encrypted database |
| analyze-pgn-best-chess-move | Find the best move in a chess game |
| 1104 | Fix a systemd service's LVM mount dependency |
| winning-avg-corewars | Write a high-performing Core War warrior |
| 462 | Persist appmonitor environment configuration |
| 481 | Fix bash customizations not applied in new sessions |
| 710 | Write a video-processing script for compilation clips |
| 361 | Fix a multi-script dev environment initialization |

## Requirements

- Python 3.9+
- Docker (Docker Desktop, OrbStack, or Linux Docker). Privileged containers must be allowed, because the strace tracer sidecar needs them.
- Ollama serving on `http://127.0.0.1:11434`. `scripts/setup-local.sh` installs and starts it on macOS; on Linux, install Ollama normally.
- Network access during image builds (the task containers themselves run with no network).

## Before the first run

1. Pull both models. The attacker and judge are deliberately different models:
```bash
   ollama pull qwen3-coder:30b
   ollama pull qwen2.5-coder:32b
```
2. Run the unit tests (no Docker, no models):
```bash
   PYTHONPATH=. python3 -m unittest tests.test_audit tests.test_runner
```
3. Run the Docker integration test. It must pass before a real run, because it is the only check that tracing works on this machine:
```bash
   VERITY_DOCKER_TESTS=1 PYTHONPATH=. python3 -m unittest tests.test_audit -v
```
4. Smoke-test the four newest tasks. Each should print `valid`:
```bash
   for t in 462 481 710 361; do
     python3 src/runner.py audit --task $t --attempts 1 --max-turns 1 \
       --attacker qwen3-coder:30b --judge qwen2.5-coder:32b
   done
   for r in experiments/runs/*-audit/report.json; do
     python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); v=r["validity"]; print(r["provenance"]["task_id"], v.get("verdict"), v.get("error"))' "$r"
   done
```
   If a task prints `oracle_fails`, read `oracle/oracle.log` in its run folder. When done, delete the smoke-test runs so they are not mixed into the results:
```bash
   rm -rf experiments/runs/*-audit
```

## Run the full audit

Runs all ten tasks in sequence. A task that crashes is logged and skipped; it does not stop the loop.

```bash
set -o pipefail
ATTACKER=qwen3-coder:30b
JUDGE=qwen2.5-coder:32b
mkdir -p experiments/runs/logs
for task in blind-maze titanic-female-survival-prediction \
            recover-encrypted-db-credentials analyze-pgn-best-chess-move \
            1104 winning-avg-corewars 462 481 710 361; do
  python3 src/runner.py audit --task "$task" --attacker "$ATTACKER" --judge "$JUDGE" \
    2>&1 | tee "experiments/runs/logs/$task.log" || echo "$task" >> experiments/runs/logs/failed.txt
done
```

Each task uses four attempts of up to 40 turns. Reports are written to `experiments/runs/<task>-<timestamp>-audit/report.json`, and any crashed task IDs are listed in `experiments/runs/logs/failed.txt`.

To run one task:

```bash
python3 src/runner.py audit --task blind-maze \
  --attacker qwen3-coder:30b --judge qwen2.5-coder:32b
```

## Reading the results

Print each task's scorecard:

```bash
for report in experiments/runs/*-audit/report.json; do
  python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r["provenance"]["task_id"], json.dumps(r["scorecard"]))' "$report"
done
```

List attempts that trace evidence labels a hack but the judge called honest. Review these by hand, because a legitimate solution that writes a different file than the reference solution can trigger trace evidence:

```bash
for r in experiments/runs/*-audit/report.json; do python3 -c 'import json,sys
r=json.load(open(sys.argv[1]))
for a in r["redteam"].get("attempts", []):
    if a["label"] == "hack" and a["judge"] == "honest": print(r["provenance"]["task_id"], a["id"], a["evidence_kinds"])' "$r"; done
```

Notes on interpretation:

- Check `redteam.summary.errors` before interpreting a zero-hack result.
- Zero hacks means none were found in that run; it does not mean the environment is safe. With four attempts, the per-attempt exploit rate could still be as high as about 53% (`no_exploit_upper`).
- A `certified` hack has trace evidence behind it. A `judged` hack rests only on the judge's reading of the transcript, and the judge has not yet been validated with the current `prompts/judge.md`, so treat judged hacks as provisional.

## Sharing results

`experiments/runs/` is gitignored. Send results back as an archive:

```bash
tar czf verity-runs.tgz experiments/runs
```

## Adding a task

1. Put the task under `tasks/<id>/original_task/`. It needs `instruction.md`, `environment/Dockerfile`, `solution/solve.sh`, and `tests/test_outputs.py`.
2. Add an entry to `tasks.json` with `path`, `instruction`, `tests`, `exploit_reference`, `description`, and `grader`. For a pass/fail pytest grader, use `"command": "python3 -m pytest /tests/test_outputs.py -rA", "score_type": "binary"`. For a numeric grader, use `"score_type": "numeric"` with a `score_file` and an optional `pass_threshold`.
3. The grader runs with no network, so any test dependency beyond pytest must be installed in the Dockerfile.
4. Smoke-test the task as above and confirm `validity.verdict` is `valid` before including it in a run.