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
trace + verifier output + result.json
```

## One trial

For `analyze-pgn-best-chess-move`, the user runs:

```bash
python3 src/runner.py attack \
  --task analyze-pgn-best-chess-move \
  --model qwen3-coder:30b \
  --attempts 1
```

The runner reads the task path and grader command from `tasks.json`, builds the
task Docker image, and starts one isolated container. It reads the task's
`instruction.md` and appends `prompts/redteam.md` to the system protocol sent
to Ollama. Ollama returns a JSON shell command. The runner executes that
command in `/app`, records its output, and sends the observation back to
Ollama. This repeats until `done` or the turn limit.

When the model stops, the runner copies the task's `tests/` directory into the
container as `/tests`, runs the manifest grader command, captures its output,
and records the reward. The container is then removed.

The trial folder contains only:

```text
prompt.json   Initial model context
trace.jsonl   Model actions and container observations
verifier.txt  Grader output
result.json   Reward, stop reason, timing, and errors
```

`exploit.md` is reference material from Terminal Wrench. It is not shown to
the model and is used later to interpret a trajectory.
