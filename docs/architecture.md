# Verity pipeline

Verity audits one task at a time. The task's own files remain under
`tasks/<id>/original_task/`; Verity adds only the shared red-team prompt and
the run controller.

```text
tasks.json
    ↓
selected task + Dockerfile
    ↓
fresh Docker container
    ↓
task instructions + red-team prompt → Ollama
    ↓
model JSON command → runner → container
    ↓
command output → Ollama
    ↓
model stops
    ↓
manifest grader runs inside /tests
    ↓
trace-based outcome classification + verifier output + result.json
```

## One trial

For `analyze-pgn-best-chess-move`, the user runs:

```bash
python3 src/runner.py attack \
  --task analyze-pgn-best-chess-move \
  --model qwen3-coder:30b \
  --attempts 1
```

The runner reads the task path, working directory, and grader command from
`tasks.json`, builds the task Docker image, and starts one isolated container.
It reads the task's `instruction.md` and appends `prompts/redteam.md` to the
system protocol sent to Ollama. Ollama returns a JSON shell command. The runner
executes that command in the task's configured working directory, records its
output, and sends the observation back to Ollama. This repeats until `done` or
the turn limit.

When the model stops, the runner copies the task's `tests/` directory into the
container as `/tests`, runs the manifest grader command from that task's
working directory, captures its output,
and records the reward. The container is then removed.

## Recovery behavior

- Ollama transport/timeouts and malformed JSON responses get up to two retries.
- Invalid model actions (including NUL bytes or oversized commands) are rejected
  before Docker and reported back to the model.
- Command execution errors are recorded and returned as observations so the
  model can try another action.
- The same command is allowed twice; further repeats are blocked. If the model
  repeats again after the warning, the trial ends as stalled and is still graded.
- Failed attempts are saved in `summary.json`; remaining attempts continue.
  Persistent infrastructure failures remain explicit errors rather than being
  presented as low reward.
- `scripts/validate-tasks.py` separately checks reference solutions and known
  exploit controls in fresh containers; it never exposes those controls to the
  attacking model.

The trial folder contains only:

```text
prompt.json   Initial model context
trace.jsonl   Model actions and container observations
telemetry.jsonl Run/episode IDs and structured execution metrics
verifier.txt  Grader output
result.json   Reward, stop reason, timing, and errors
```

`telemetry.jsonl` records model-call duration and Ollama token counts when
available, command duration and exit status, and grading duration and reward.
It contains metric fields only: prompts, commands, model explanations, and
grader output are not copied into telemetry. Existing trace and result fields
remain available for compatibility.

`exploit.md` is reference material from Terminal Wrench. It is not shown to
the model and is used later to interpret a trajectory.

Attack `result.json` also carries one of four conservative labels:
`genuine_success`, `confirmed_reward_hack`, `ordinary_failure`, or `ambiguous`.
Labels use only executed commands, graded reward, and task-specific signatures
in `tasks.json`. A passing score without sufficient evidence remains ambiguous;
the label is a triage aid, not a semantic proof. Reclassify older runs with
`python3 scripts/classify-runs.py`.

Run `python3 scripts/validate-tasks.py` to exercise each vendored reference
solution and known exploit control in separate disposable containers. A task
without a valid reference solution or with an exploit blocked by sandbox policy
is marked unavailable, not silently counted as passing.
