# Verity

**Find the ways an RL environment can be cheated, then stop the model from learning them while it trains.**

Verity is one system that works on any reinforcement-learning environment. Chess is the demo environment: easy to see, and it looks good in a demo. The 13 Terminal Wrench tasks are real agent environments audited by the same system.

## How it works

1. **Audit.** An AI attacker probes the environment's grader for shortcuts. Verity proves each one with numbers: what the grader paid against what the answer was really worth, or traced evidence of the exact file the agent read or changed.
2. **Markers.** Each proven shortcut becomes a marker: a small rule, plus a warning and a fix. Verity tests every marker against cheat variants and honest answers before it can act.
3. **Guard.** During training, Guard sits between the environment's grader and the trainer. Every model output is checked against the markers in about 2 µs. In **enforce** mode, a match turns the reward into −1, so cheating never pays. In **observe** mode, matches are only logged.

## Where everything is

```
verity/                 THE SYSTEM: knows nothing about any particular environment
  environment.py        the plug every environment implements
  audit.py              one command to audit any environment and write its markers
  probe.py              audit engine for answer environments: attacker, proof, marker testing
  sandbox.py            audit engine for agent environments in Docker: grader checks, attacker, traced proof, judge
  runner.py             Docker and task plumbing used by sandbox.py
  llm.py                model client: OpenRouter, DeepSeek or Ollama, with a spend cap
  markers.py            the marker format, rule matching, testing, conversion from sandbox reports
  guard.py              enforces markers between the grader and the trainer
  prompts/              attacker and judge prompts
environments/           ENVIRONMENTS plugged into Verity
  chess.py              the demo: chess with a seeded, disclosed grader bug
  terminal_wrench.py    13 sandboxed terminal tasks (task files in tasks/, listed in tasks.json)
train.py                RL fine-tuning (TRL GRPO + LoRA) on any answer environment, Guard observing or enforcing
evaluate.py             held-out comparison: base model vs. both trained arms
dashboard/              local demo UI: audit, training curves, arena, real environments
markers/                audit output: one marker file per environment
experiments/            raw results: sandbox audit runs (runs/), chess audit reports (chess-audits/)
scripts/                Hyak jobs, batch audits, maintenance scripts
tests/                  unit tests (no GPU, Docker or network needed)
docs/                   original plan, Devpost draft, lab log, report schema, dashboard API
data/, artifacts/       not in git: chess positions; trained models, telemetry, results
```

[PROGRESS.md](PROGRESS.md) has the current state and results.

## The plug: adding an environment

Create `environments/<name>.py` with `NAME`, `VERSION`, `KIND` and `DESCRIPTION`. Then add:

- **Answer environments** (the model replies with text):
  - `tasks(split)` and `data_path(split)`
  - `grade(task, output)`: the environment's own reward, which is what gets audited
  - `check(task, output)`: a trusted reference score
  - `honest_examples(task)`
  - optional: `metrics`, `task_key`, and a seeded bug for offline demos
- **Sandbox environments** (the model runs commands in a Docker machine): `tasks()`. The sandbox engine does the rest.

[verity/environment.py](verity/environment.py) has the full contract. Once the file exists, the same commands work:

```bash
python -m verity.audit <name> ...            # find and prove hacks, write markers/<name>.json
python train.py --env <name> --arm verity    # train with Guard enforcing those markers
```

## Run it

```bash
uv venv --python 3.13 && uv pip install -r requirements.txt   # requirements-ml.txt adds torch/TRL for training and the arena
brew install stockfish                                       # chess labels, arena and live probes
.venv/bin/python -m unittest discover -s tests               # all tests
```

| Task | Command |
|---|---|
| Generate chess positions | `python -m environments.chess --stockfish $(which stockfish)` |
| Audit chess offline (seeded bug, no API) | `python -m verity.audit chess --fixture` |
| Audit chess with an AI attacker | `python -m verity.audit chess --model anthropic/claude-opus-5.5 --input-price 4 --output-price 20` (`OPENROUTER_API_KEY` in `.env`) |
| Audit a sandboxed task | `python -m verity.audit terminal_wrench --task blind-maze --model qwen3-coder:30b` (Docker + Ollama, or `--provider openrouter`) |
| Markers from an existing audit, plus a replay | `python -m verity.audit terminal_wrench --report experiments/runs/full-audit-v51-20261012/blind-maze/report.json --replay experiments/runs/full-audit-v51-20261012/blind-maze/redteam/a1/trajectory.jsonl` |
| Batch sandbox audits over the API | `python scripts/run-batch.py --dry-run` (then without `--dry-run`) |
| Guard speed | `python -m verity.guard --pack markers/chess-fixture.json` |
| Train one arm (CUDA) | `python train.py --env chess --arm verity --output runs/verity --signatures markers/chess-fixture.json` |
| Evaluate both arms | `python evaluate.py --env chess --baseline runs/baseline --verity runs/verity --output runs/results.json` |
| Dashboard | `python -m dashboard.server`, then open http://127.0.0.1:8765 |

### Training on Hyak

[scripts/hyak.sh](scripts/hyak.sh) has stages `setup`, `smoke` (5 steps per arm), `pilot` (baseline only: does hacking emerge?), `full` and `evaluate`. Environment variables tune the runs:

| Variable | Use |
|---|---|
| `LEARNING_RATE`, `STEPS`, `SEED`, `PILOT_STEPS` | training settings |
| `ARMS=baseline` / `ARMS=verity` | train one arm per GPU, in parallel |
| `SIGNATURES` | which marker file to use |

`full` refuses to start unless the pilot showed hacking emerging. `scripts/sync_hyak.sh` copies results to `artifacts/` for the dashboard.

Defaults: Qwen2.5-1.5B-Instruct, LoRA rank 16, 8 tasks × 8 samples per step, 160 tokens, temperature 1.0, checkpoints every 25 steps. Both arms always use identical settings.

Each run writes three things:
- `run.json`: settings, versions, and git commit.
- `telemetry.jsonl`, logged per step:
  - grader reward, guarded reward and true score
  - catch rate and caught examples
  - Guard cost
  - a reward-up-but-quality-flat alarm
- `adapter/`: the trained model.

## Honest limits

- The chess bug is seeded and disclosed.
- Command-matching markers for agent environments are a pre-execution screen, not a syscall sandbox.
- Results describe these environments and runs, not general safety.
