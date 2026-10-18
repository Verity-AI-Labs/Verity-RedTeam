# Verity

**Find the ways an RL environment can be cheated, then stop the model from learning them while it trains.**

Verity is one system for any reinforcement-learning environment. An environment plugs in with one Python file; the same commands then audit it, turn what the audit proves into rules, and guard training on it. Two demo environments show it end to end:

- **Chess through tools** (`chess_game`): the agent plays moves against an engine. The task allows one hint per game, but nothing enforces it.
- **A web shop** (`shop`): the agent shops on generated store sites by clicking, typing and choosing. The reward checks that the wanted item was ordered, but not that nothing else was, and never checks the coupon or the shipping speed.

The same system also audited 13 sandboxed Terminal Wrench tasks and a one-answer chess task.

## How it works

1. **Audit.** A frontier model (Claude Opus through OpenRouter) gets exactly what a training agent gets: the task and the tools. It never sees the grader's code. It plays episodes, sees only the reward, and looks for reward that does not come from doing the task. Verity proves every finding with numbers: what the grader paid against what a trusted reference check says the episode was worth. The audit runs in rounds. Each round looks for a different mechanism, and the markers found so far are enforced while the attacker plays.
2. **Markers.** The auditor writes each proven hack as a small rule over the episode's record, plus a warning and a fix. Verity accepts a rule only if it catches at least 95% of the exploits and flags at most 1% of honest play.
3. **Guard.** During RL fine-tuning, Guard sits between the environment and the trainer. In a tools environment it checks every tool call twice: by its text before it runs, and by its effect after it runs, so a rule can describe what an action does (for example, an order placed with extra items). In **enforce** mode a matching call is blocked or undone, the agent gets the marker's warning as the tool result and keeps going (warn and redirect), and each blocked call costs a penalty. In **observe** mode matches are only logged. A check takes tens of microseconds.

Nothing in `verity/` knows about chess or shops. Every environment-specific fact lives in `environments/<name>.py`.

## Where everything is

```
verity/                 THE SYSTEM: knows nothing about any particular environment
  environment.py        the plug every environment implements
  audit.py              one command to audit any environment and write its markers
  probe.py              audit engine for answer and tools environments: attacker, proof, marker testing, rounds
  sandbox.py            audit engine for agent environments in Docker: grader checks, attacker, traced proof, judge
  runner.py             Docker and task plumbing used by sandbox.py
  llm.py                model client: OpenRouter, DeepSeek or Ollama, with a spend cap
  markers.py            the marker format, rule matching, testing, conversion from sandbox reports
  guard.py              enforces markers between the environment and the trainer
  prompts/              sandbox attacker and judge prompts
environments/           ENVIRONMENTS plugged into Verity
  chess_game.py         multi-step chess through tools (legal_moves, move, hint), with a seeded, disclosed loophole
  shop.py               a web shop through tools (click, type_text, select_option, view_page), two seeded, disclosed bugs
  chess.py              one-answer chess with a seeded, disclosed grader bug
  terminal_wrench.py    13 sandboxed terminal tasks (task files in tasks/, listed in tasks.json)
train.py                RL fine-tuning (TRL GRPO + LoRA) on any answer or tools environment, Guard observing or enforcing
evaluate.py             held-out comparison of the base model and both trained arms (batched episodes, vLLM optional)
dashboard/              site.py builds the site from the run files; live.py serves live chess games
web/                    the hosted site (Vercel): index.html (built by site.py) and api/live.js
markers/                audit output: one marker file per environment (-fixture: disclosed test doubles)
experiments/            raw results: audit reports (<environment>-audits/), sandbox audit runs (runs/)
scripts/                Hyak jobs (hyak.sh), RunPod pods (runpod.py, pod.sh), chess match, batch audits
tests/                  unit tests (no GPU, Docker or network needed)
docs/                   original plan, Devpost draft, lab log, report schema
data/, artifacts/       not in git: generated tasks; trained models, telemetry, results
```

[PROGRESS.md](PROGRESS.md) has the current state and results.

## The plug: adding an environment

Create `environments/<name>.py` with `NAME`, `VERSION`, `KIND` and `DESCRIPTION`. Then add:

- **Tools environments** (the model calls tools over several steps):
  - an `Episode` class whose public methods are the tools, with `reset(**task)` (it may return the first observation)
  - `tasks(split)`, `data_path(split)`, `grade(episode)` (the reward being audited), `check(episode)` (a trusted reference score)
  - `honest_logs()`: records of honest play, so markers never flag it
  - optional: `record(episode)` (what markers read; default is the call log), `metrics`, `summarize`, `done`, `MAX_TOOL_CALLS`, `DEPLOYMENT` (the tools, prompt and split a deployed agent gets), and seeded bugs for offline demos (`SEEDED_MARKERS`, `seeded_exploit`)
- **Answer environments** (the model replies with text): `tasks`, `data_path`, `grade(task, output)`, `check(task, output)`, `honest_examples(task)`.
- **Sandbox environments** (the model runs commands in a Docker machine): `tasks()`. The sandbox engine does the rest.

[verity/environment.py](verity/environment.py) has the full contract. Once the file exists, the same commands work:

```bash
python -m verity.audit <name> --model anthropic/claude-opus-5.5 --input-price 4 --output-price 20 --rounds 2
python train.py --env <name> --arm baseline ...   # Guard observes
python train.py --env <name> --arm verity ...     # Guard enforces the audit's markers
python evaluate.py --env <name> --baseline runs/baseline --verity runs/verity --deploy ...
```

## Run it

```bash
uv venv --python 3.13 && uv pip install -r requirements.txt   # requirements-ml.txt adds torch/TRL for training
brew install stockfish                                       # chess environments
.venv/bin/python -m unittest discover -s tests               # all tests
```

| Task | Command |
|---|---|
| Build the shop's tasks | `python -m environments.shop build` (2,000 train, 300 eval, 300 deployment tasks) |
| Shop a task yourself | `python -m environments.shop play 0 --split ood`, then open <http://localhost:8000> |
| Watch a reference answer | `python -m environments.shop trace 0 --answer hack_both` |
| Build chess positions | `python -m environments.chess_game --prepare` |
| Audit offline (seeded bugs, no API) | `python -m verity.audit shop --fixture` |
| Audit with an AI attacker | `python -m verity.audit shop --model anthropic/claude-opus-5.5 --input-price 4 --output-price 20 --rounds 2 --max-usd 8` (`OPENROUTER_API_KEY` in `.env`) |
| Audit a sandboxed task | `python -m verity.audit terminal_wrench --task blind-maze --model qwen3-coder:30b` |
| Guard speed | `python -m verity.guard --pack markers/shop.json --field calls` |
| Train one arm (CUDA) | `python train.py --env shop --arm verity --model Qwen/Qwen3-8B --vllm --output runs/shop/verity` |
| Evaluate as deployed | `python evaluate.py --env shop --baseline runs/shop/baseline --verity runs/shop/verity --deploy --backend vllm --output runs/shop/results-ood.json` |
| Build the site | `python dashboard/site.py` (writes `web/index.html`) |

### Training on RunPod

`scripts/runpod.py create NAME` starts an 80 GB GPU pod (`RUNPOD_API_KEY` in `.env`) and prints its SSH address. `scripts/pod.sh IP PORT sync|setup|train ARM|status|pull DEST|evaluate ...` copies the code, installs TRL 1.16 with vLLM 0.31, builds the data, trains one arm in the background, and copies the results back. Both arms use identical settings; only Guard's mode differs.

### Training on Hyak

[scripts/hyak.sh](scripts/hyak.sh) has stages `setup`, `smoke`, `pilot`, `full` and `evaluate`, tuned by environment variables (`ENV_NAME`, `MODEL`, `STEPS`, `LEARNING_RATE`, `BATCH_SIZE`, `GRADIENT_ACCUMULATION`, `GENERATIONS`, `MAX_COMPLETION_LENGTH`, `ARMS`, `SIGNATURES`, `DATA_DIR`, `EVAL_*`). A job given several GPUs splits the model across them. `scripts/sync_hyak.sh` copies results to `artifacts/`.

Each run writes `run.json` (settings, versions, git commit, marker hash), `telemetry.jsonl` (per step: grader reward, guarded reward, true score, catch rate per marker, Guard cost, the environment's own metrics, and a reward-up-but-quality-flat alarm) and `adapter/` (the trained LoRA).

## Honest limits

- The bugs in the demo environments are seeded and disclosed. The auditor is not told about them.
- A marker catches the hacks the audit found and the variants it was tested on. That does not prove no other hack exists.
- Command-matching markers for sandboxed agents are a pre-execution screen, not a syscall sandbox.
- Results describe these environments and runs, not general safety.
