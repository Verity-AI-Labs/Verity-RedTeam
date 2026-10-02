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
Events are placed on a bounded in-process queue; a background thread formats
and appends JSONL. Enqueue never waits for disk I/O. If the queue fills, events
are dropped rather than blocking the runner, and final `result.json` marks
telemetry `incomplete` with submitted, accepted, written, dropped, and error
counts. Callers must not treat an incomplete sidecar as a complete timeline.
The writer drains and flushes before the attempt result is saved.

Each event includes schema version, run/episode IDs, task ID, attempt number,
turn (null for grading), a unique `event_id`, UTC `event_time`, and a 1-based
per-episode sequence. Model and command events point to the canonical
`trace.jsonl` line when one exists. Command status distinguishes `executed`,
`blocked`, and `error`. Token fields are provider-neutral; Ollama's
`prompt_eval_count` and `eval_count` map to request and response tokens.
Unavailable counts are null, not zero. Telemetry contains no prompts,
commands, explanations, command output, or grader output. Grading duration,
status, and reward remain distinct from stop reason and heuristic
classification; the raw trace and verifier artifact remain canonical evidence.

### Latency status and limits

The bounded queue removes JSON serialization and sidecar file I/O from the
event producer path; it does not make the whole audit runner a real-time RL
hook. The current offline runner still writes full `trace.jsonl` evidence
synchronously, and no trainer adapter exists yet. A synthetic 20,000-event
microbenchmark on Python 3.13.13/macOS arm64 measured asynchronous producer
enqueue p50/p95/p99 of 0.54/0.67/1.42 microseconds versus 4.75/6.92/13.17
microseconds for a synchronous JSONL writer, with no drops at queue capacity
65,536. This isolates the event-sink producer cost; it excludes trace writes,
environment stepping, model inference, end-to-end rollout impact, and sustained
multi-producer load. Reproduce with
`python3 scripts/benchmark-telemetry.py --events 20000`.

Do not claim low-overhead training integration from that microbenchmark alone.
Before adopting a real RL harness, measure paired telemetry-off/on runs under
representative single- and multi-worker load; report p50/p95/p99 step overhead,
throughput, queue high-water mark, drops, blocked time, and incomplete episodes.
Keep telemetry opt-in until the agreed overhead budget and completeness
criteria pass. A bounded producer cannot guarantee both zero blocking and zero
loss under sustained overload.

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

## Product architecture beyond the prototype

Verity's product loop is **Discover → Diagnose → Patch → Re-audit**. The
Terminal Wrench runner is the current offline audit rig, not the full product.
Keep the following responsibilities separate:

1. **Environment/task registry:** immutable environment and verifier versions,
   task contracts, seeds, and known controls.
2. **Audit orchestrator and adapters:** schedule interchangeable attacker
   models and attempts; isolate environments; normalize action/observation,
   grading, and harness lifecycle interfaces.
3. **Low-latency telemetry SDK:** bounded, language-neutral events for
   observable actions, state-transition references, reward/verifier status,
   timing, usage counters, and failures. Start with Python; add Rust hooks when
   profiling or a customer's harness boundary justifies them. Never require
   private chain-of-thought.
4. **Evidence store and offline analysis:** join telemetry with protected raw
   traces and verifier artifacts; produce redacted, trace-backed findings,
   confidence, and limitations. Telemetry alone is not proof of an exploit.
5. **Patch and re-audit pipeline:** produce a separately versioned minimal
   environment/verifier candidate, replay legitimate controls, rerun known and
   novel attacks, and retain before/after provenance.
6. **Training-outcome evaluation:** compare baseline-selected environments
   with Verity-audited/repaired environments under matched starting models,
   compute, and held-out capability evaluations. Build an Environment Quality
   Index only after enough outcomes exist to validate its predictive value.

The first gate remains repeatable, independently evidenced exploit
rediscovery. Subsequent gates are demonstrated diagnosis and safe patching,
measured low-overhead integration into an actual rollout loop, and finally
genuine capability gain per GPU-hour. See
[`rl-harness-integration.md`](rl-harness-integration.md) for telemetry
interfaces and measurement recommendations.
