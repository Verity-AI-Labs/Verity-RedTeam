# RL rollout telemetry: first integration target

**Research checked:** 2026-10-01

**Scope:** Observe Verity audit rollouts now and leave a portable path to training rollouts. This note covers telemetry only; it does not change the runner or choose an RL training stack.

## Recommendation

Instrument the runner with **OpenTelemetry (OTel) spans**, and use **local Arize Phoenix as the first viewer**. Keep the existing per-attempt `trace.jsonl`, `verifier.txt`, and `result.json` as the canonical evidence; derive telemetry from the same events and never make an exporter or dashboard a prerequisite for grading. The current runner already records model responses, executed/blocked commands, observations, and verifier reward; `attempt()` also measures whole-attempt seconds. OTel adds correlation and operation-level latency without replacing that evidence.

This is a practical first target rather than a claim that OTel is an RL data model. The GenAI conventions are maintained separately from core OTel; reward, verifier outcome, and rollout state should initially use namespaced `verity.*` attributes. OpenTelemetry's Python API supports spans, events, attributes, and batch span processing ([Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/); [GenAI semantic conventions repository](https://github.com/open-telemetry/semantic-conventions-genai), accessed 2026-10-01). Phoenix accepts OTel traces and is self-hostable, so the first deployment can remain local ([Phoenix OTel setup](https://arize.com/docs/phoenix/tracing/how-to-tracing/setup-tracing/setup-using-phoenix-otel); [self-hosting](https://arize.com/docs/phoenix/self-hosting), accessed 2026-10-01).

### Options compared

Effort below is an estimate for one engineer, not a measured benchmark; it assumes retaining current JSONL output.

| Option | Fit and useful API | Estimated effort | Trade-offs |
|---|---|---:|---|
| **OpenTelemetry Python SDK/API** — recommended instrumentation contract | Manual `tracer.start_as_current_span(...)`, span attributes/events, and `BatchSpanProcessor`; export via OTLP. Generic and reusable by a later Python training runner. | 1–2 days for runner spans; add backend setup separately. | No UI, storage, or RL reward schema by itself. GenAI conventions are maintained separately from core OTel; use `verity.*` fields for audit-specific data rather than implying standard attributes exist. |
| **Arize Phoenix** — recommended first local viewer | `phoenix.otel.register(...)` plus manual OTel spans; Phoenix supports trace inspection, annotations/evaluation, and local or self-hosted deployment. Its setup docs expose a `batch` option (documented default is `False`), so explicitly configure batching and measure it rather than assuming the default is asynchronous. | 1–3 days for local UI plus integration, subject to local Python/dependency setup. | Adds a service and its data-retention/storage operations. OpenInference is Phoenix's AI-specific convention layer; keep Verity's custom reward/verifier schema separate and portable. |
| **Langfuse Python SDK** | `start_as_current_observation(...)` for nested spans/generations and scores attached to traces; Python SDK supports self-hosted Langfuse. | 1–2 days for a basic SDK integration; self-hosting adds operational setup. | More direct tracing/scores workflow, but creates an SDK-specific surface. Its SDK batches in the background and short-lived programs must explicitly `flush()`; exporter lifecycle and content redaction still need tests. |

**Why not start with a platform-specific trace API?** Verity is currently a small Python runner using Ollama and Docker, not an agent framework with auto-instrumentation. Manual instrumentation at the runner's operation boundaries is the smallest honest fit. Phoenix supplies a local visualization path while the OTel API keeps a later training-harness adapter from depending on Phoenix. Langfuse remains a credible alternative if trace annotation/score workflows become the immediate priority. Langfuse's current docs describe its observation model, score attachment, background batching, and short-lived-app flush requirement ([instrumentation](https://langfuse.com/docs/observability/sdk/instrumentation); [data model](https://langfuse.com/docs/observability/data-model); [scores](https://langfuse.com/docs/evaluation/scores/overview); [self-hosting](https://langfuse.com/self-hosting), accessed 2026-10-01).

## Smallest useful integration

Add one optional telemetry adapter at the runner's event/operation boundary; do not rewrite `record()`'s JSONL format or send telemetry synchronously from the agent loop.

1. Start one `verity.rollout` root span per attempt, with `run_id`, `task_id`, `attempt`, `kind`, model name, and code/task version identifiers.
2. Add child spans around each Ollama `chat()` request, actual shell/tool execution, and `grade()` call. Emit blocked commands as events, not successful tool calls. Attach `turn`, operation duration, exit/status, and a bounded/redacted observation excerpt. Preserve the full local trace in JSONL.
3. On the root/verifier span, record the exact numeric reward (or null on grading/infrastructure error), verifier exit status, stop reason, and existing conservative classification. Keep grading failure distinct from reward zero.
4. Normalize Ollama's response usage/timing fields when present: `prompt_eval_count`, `eval_count`, `total_duration`, `load_duration`, `prompt_eval_duration`, and `eval_duration`. The official `/api/chat` response schema documents these fields ([Ollama chat API](https://docs.ollama.com/api/chat), accessed 2026-10-01). Derive tokens/second only when counts and duration are present. Other providers may omit them; represent missing values as unavailable, not zero.
5. Treat measured model latency and token counts as available telemetry, **not** complete compute accounting. Record Docker CPU/memory limits as configured bounds; actual CPU/GPU utilization, energy, and per-rollout accelerator attribution need a separate host-side measurement decision and must not be inferred from Ollama token statistics.

The smallest useful API surface is therefore OTel's tracer/span API plus Phoenix's OTLP receiver—not a general agent SDK or a new rollout database. Configure a batch processor (or a bounded local queue), a short flush timeout at attempt end, and opt-in export. Exporter failure must be visible in runner diagnostics/telemetry status but must not become a successful export or change reward; the local JSONL remains the recovery source. Benchmark synchronous instrumentation overhead and check for dropped spans.

**Privacy boundary:** record structured actions/tool-call metadata and only the minimum sanitized state needed to interpret outcomes. Do not export prompts, full message histories, the model's free-form `explanation`, any `thinking`/reasoning fields, or hidden/private chain-of-thought. Limit/redact tool output and verifier text before export; retain any unredacted evidence only in the existing local run artifacts with their current access controls. Avoid high-cardinality or sensitive attribute values. Phoenix documents annotation APIs for attaching feedback/evaluation outcomes to spans, should later human review be needed ([annotations](https://arize.com/docs/phoenix/tracing/how-to-tracing/feedback-and-annotations/capture-feedback), accessed 2026-10-01).

## Prototype plan (1–2 weeks)

**Week 1 — schema and audit-run integration**

- **Days 1–2:** agree a versioned minimal event map (`rollout`, `model_call`, `tool_call`, `verifier`); identify allowed/redacted fields; capture baseline wall time over a fixed small replay set. Add OTel/Phoenix behind an opt-in setting.
- **Days 3–4:** instrument the root attempt and the three operation boundaries above. Export batched spans to a local Phoenix instance; keep JSONL/result/verifier writes unchanged. Add tests for success, reward zero, grader error, blocked/rejected action, missing usage metrics, and exporter unavailable.
- **Day 5:** inspect representative traces for action/state/reward/verifier joins, verify no prompt or reasoning leakage, and check that raw evidence still classifies exactly as before.

**Week 2 — performance and training-shaped validation**

- Replay at least 20 fixed episodes with telemetry off/on. Compare p50/p95 end-to-end duration and per-operation duration; target **under 5 ms p95 added time per recorded operation**, no reward/classification changes, no missing terminal spans, and no unreported drops. If the overhead target fails, keep export opt-in and reduce payload/batch more aggressively rather than sampling away rare audit evidence.
- Exercise an in-memory synthetic training rollout through the same adapter (multiple turns, tool events, scalar and categorical verifier outcomes, optional tokens/latency). Confirm it can map without runner-specific fields beyond stable `verity.*` identifiers.
- Document schema/version, retention/redaction defaults, and operational instructions in the eventual implementation PR. Defer a live training-framework hook until the actual trainer and rollout callback boundary are selected; do not couple this first prototype to a guessed RL stack.

## Source references

All web references below were checked on **2026-10-01**. Documentation and default versions can change; pin dependency/server versions when implementing.

- [OpenTelemetry Python manual instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/) — spans, attributes, events, and batch processor example.
- [OpenTelemetry GenAI semantic conventions repository](https://github.com/open-telemetry/semantic-conventions-genai) — separate GenAI conventions project; do not assume it defines Verity's reward/verifier schema.
- [Phoenix OTel setup](https://arize.com/docs/phoenix/tracing/how-to-tracing/setup-tracing/setup-using-phoenix-otel), [Phoenix self-hosting](https://arize.com/docs/phoenix/self-hosting), and [Phoenix annotations](https://arize.com/docs/phoenix/tracing/how-to-tracing/feedback-and-annotations/capture-feedback).
- [Langfuse instrumentation](https://langfuse.com/docs/observability/sdk/instrumentation), [data model and background processing](https://langfuse.com/docs/observability/data-model), [scores](https://langfuse.com/docs/evaluation/scores/overview), and [self-hosting](https://langfuse.com/self-hosting).
- [Ollama `/api/chat`](https://docs.ollama.com/api/chat) — response schema and example token/duration statistics.
