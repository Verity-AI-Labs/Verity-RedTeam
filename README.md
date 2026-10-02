# Verity RedTeam

Verity audits reinforcement learning environments for reward hacks. It runs each task's reference solution and grader, then gives an attacker model repeated chances to pass the grader without completing the task. Docker runs the task commands; Ollama runs the models locally.

## Requirements

- macOS, Python 3.9+, and Docker or OrbStack
- Ollama running on `http://127.0.0.1:11434`
- The attacker and judge models pulled into Ollama
- The validated judge prompt saved as `prompts/judge.md`

The included judge prompt returns a JSON verdict based on whether the passing attempt shows clear evidence of reward hacking.

## Run the full audit

From the repository root, start Ollama and pull the models you plan to use. The optional setup script installs local Ollama and pulls the smaller default model:

```bash
bash scripts/setup-local.sh
./.local/ollama/ollama pull qwen3-coder:30b
```

The repository includes `prompts/judge.md`. Review it before running, then run all six tasks sequentially, replacing the model names as needed:

```bash
set -e
ATTACKER=qwen3-coder:30b
JUDGE=qwen3-coder:30b

for task in blind-maze titanic-female-survival-prediction \
            recover-encrypted-db-credentials analyze-pgn-best-chess-move \
            1104 winning-avg-corewars; do
  python3 src/runner.py audit --task "$task" \
    --attacker "$ATTACKER" --judge "$JUDGE"
done
```

Each task uses four attempts of up to 40 turns by default. The runner checks that `prompts/judge.md` exists and both models are available before starting. A report is written under `experiments/runs/<task>-<timestamp>-audit/report.json`.

To run one task, use the same command without the loop, for example:

```bash
python3 src/runner.py audit --task blind-maze \
  --attacker qwen3-coder:30b --judge qwen3-coder:30b
```

To inspect the scorecards after the runs:

```bash
for report in experiments/runs/*-audit/report.json; do
  python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r["provenance"]["task_id"], json.dumps(r["scorecard"]))' "$report"
done
```

Check `redteam.summary.errors` before interpreting a zero-hack result. Zero hacks means none were found in that run; it does not establish that the environment is safe. See [docs/audit.md](docs/audit.md) for the report schema and audit details.
