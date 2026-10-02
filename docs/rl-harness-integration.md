# RL rollout telemetry: first integration target

**Research checked:** 2026-10-01

**Scope:** Observe Verity audit rollouts now and leave a portable path to training rollouts. This note covers telemetry only; it does not change the runner or choose an RL training stack.

## Recommendation

Start with a **bounded in-process queue and background local JSONL writer** at the rollout-worker boundary. Keep `trace.jsonl`, `verifier.txt`, and `result.json` as canonical audit evidence; keep the compact telemetry sidecar additive and ensure a full queue is visible as an incomplete sidecar rather than blocking the producer. The local runner prototype now measures model, command, and grading operations, but this does not yet provide an RL trainer adapter or prove end-to-end training-loop latency.

Keep the event schema language-neutral so Python and eventual Rust hooks can emit the same IDs, sequence, event kinds, and timing/outcome fields. Add OpenTelemetry (OTel) as an **optional exporter/viewer path only after measuring the local producer and exporter overhead**. Reward, verifier outcome, and rollout state should use namespaced `verity.*` fields rather than assuming OTel's GenAI conventions define Verity's semantics. The OTel Python API supports spans, events, attributes, and batch span processing ([Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/); [GenAI semantic conventions repository](https://github.com/open-telemetry/semantic-conventions-genai), accessed 2026-10-01). Phoenix accepts OTel traces and is self-hostable ([Phoenix OTel setup](https://arize.com/docs/phoenix/tracing/how-to-tracing/setup-tracing/setup-using-phoenix-otel); [self-hosting](https://arize.com/docs/phoenix/self-hosting), accessed 2026-10-01), but a visualization backend is not required for the first telemetry milestone.

### Options compared

Effort below is an estimate for one engineer, not a measured benchmark; it assumes retaining current JSONL output.

| Option | Fit and useful API | Estimated effort | Trade-offs |
|---|---|---:|---|
| **Bounded queue + background local JSONL** — recommended first | Small event payloads enter a bounded queue; one worker handles serialization and local writes. Queue overflow is explicit and marks the episode incomplete. Language-neutral schema supports later Python/Rust producers. | A few days for a minimal producer, lifecycle, overflow tests, and benchmark. | Requires an explicit overload and crash-durability policy. It does not by itself make synchronous raw trace capture or an entire RL harness low-latency. |
| **OpenTelemetry Python SDK/API** | Manual `tracer.start_as_current_span(...)`, span attributes/events, and `BatchSpanProcessor`; export via OTLP. Generic and reusable by a later Python training runner. | 1–2 days for exporter integration after the event contract is stable. | No UI, storage, or RL reward schema by itself. Measure exporter and shutdown flush behavior; keep `verity.*` fields for audit-specific semantics. |
| **Arize Phoenix** — optional first local viewer | `phoenix.otel.register(...)` plus manual OTel spans; Phoenix supports trace inspection, annotations/evaluation, and local or self-hosted deployment. Its setup docs expose a `batch` option (documented default is `False`), so explicitly configure batching and measure it rather than assuming it is asynchronous. | 1–3 days for local UI plus integration, subject to local Python/dependency setup. | Adds a service and its data-retention/storage operations. Keep Verity's custom reward/verifier schema separate and portable. |
| **Langfuse Python SDK** | `start_as_current_observation(...)` for nested spans/generations and scores attached to traces; Python SDK supports self-hosted Langfuse. | 1–2 days for a basic SDK integration; self-hosting adds operational setup. | More direct tracing/scores workflow, but creates an SDK-specific surface. Its SDK batches in the background and short-lived programs must explicitly `flush()`; exporter lifecycle and content redaction still need tests. |

**Why not start with an OTel/Phoenix dependency?** The current repo is a local Python/Docker audit prototype, not a live training harness. Its first requirement is a small measured producer path, explicit overload semantics, and a stable event contract. An exporter or dashboard should not be on the action path or become a source of truth. Langfuse remains a credible alternative if hosted trace review and score workflows later become immediate needs; its docs describe background batching and short-lived-app flush behavior ([instrumentation](https://langfuse.com/docs/observability/sdk/instrumentation); [data model](https://langfuse.com/docs/observability/data-model); [scores](https://langfuse.com/docs/evaluation/scores/overview); [self-hosting](https://langfuse.com/self-hosting), accessed 2026-10-01).

## Smallest useful integration

Define a compact versioned event contract and feed a bounded local writer without performing serialization or file I/O in the producer callback. Do not alter the canonical trace format or make telemetry evidence for classification.

1. Key events by `run_id`, `episode_id`, task/environment version, worker, and monotonic sequence; use wall timestamps for cross-system correlation, not ordering.
2. Record model-call duration/available usage, executed/blocked/error tool status and duration, environment transition references, and verifier status/reward. Keep grading failure distinct from reward zero and keep heuristic classification separate.
3. Keep event payloads bounded and redacted. Preserve raw local audit evidence separately; do not put prompts, private reasoning, command text, full observations, or verifier output in the low-latency sidecar.
4. Normalize Ollama's response usage/timing fields when present: `prompt_eval_count`, `eval_count`, `total_duration`, `load_duration`, `prompt_eval_duration`, and `eval_duration`. The official `/api/chat` response schema documents these fields ([Ollama chat API](https://docs.ollama.com/api/chat), accessed 2026-10-01). Derive tokens/second only when counts and duration are present. Other providers may omit them; represent missing values as unavailable, not zero.
5. Treat measured model latency and token counts as available telemetry, **not** complete compute accounting. Record Docker CPU/memory limits as configured bounds; actual CPU/GPU utilization, energy, and per-rollout accelerator attribution need a separate host-side measurement decision and must not be inferred from Ollama token statistics.

The first useful API surface is therefore a small local event producer and bounded queue—not a general agent SDK, required collector, or rollout database. A terminal event must expose accepted/written/dropped counts and sink errors. Queue overflow must be visible as incomplete telemetry; do not silently drop or block indefinitely. Add OTel/OTLP as an optional downstream exporter only after the local contract and overhead budget are validated.

**Privacy boundary:** record structured actions/tool-call metadata and only the minimum sanitized state needed to interpret outcomes. Do not export prompts, full message histories, the model's free-form `explanation`, any `thinking`/reasoning fields, or hidden/private chain-of-thought. Limit/redact tool output and verifier text before export; retain any unredacted evidence only in the existing local run artifacts with their current access controls. Avoid high-cardinality or sensitive attribute values. Phoenix documents annotation APIs for attaching feedback/evaluation outcomes to spans, should later human review be needed ([annotations](https://arize.com/docs/phoenix/tracing/how-to-tracing/feedback-and-annotations/capture-feedback), accessed 2026-10-01).

## Prototype plan (1–2 weeks)

**Phase 1 — schema and local producer**

- Agree event fields and privacy allowlist; preserve canonical trace/result/verifier evidence.
- Benchmark disabled, synchronous, and bounded asynchronous sinks on repeatable synthetic events; test queue saturation, sink failure, episode shutdown, event ordering, and drops.
- Treat the initial runner microbenchmark as producer-only evidence, not trainer-overhead validation. The current reproduced measurement is documented in `architecture.md`.

**Phase 2 — real harness and operational validation**

- Select an actual trainer/harness and its rollout callback; implement a Python adapter first and add Rust only where profiling or integration constraints justify it.
- Compare paired telemetry-off/on seeded rollouts under realistic worker concurrency; report p50/p95/p99 step and total rollout overhead, throughput, queue pressure, dropped events, and incomplete episodes.
- Set the production overhead/loss budget with the harness owner before claiming low-latency readiness. A synthetic microbenchmark cannot establish that budget.
- Add optional exporters only after local measurements pass; test shutdown, export failures, privacy, and retention separately.

## Source references

All web references below were checked on **2026-10-01**. Documentation and default versions can change; pin dependency/server versions when implementing.

- [OpenTelemetry Python manual instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/) — spans, attributes, events, and batch processor example.
- [OpenTelemetry GenAI semantic conventions repository](https://github.com/open-telemetry/semantic-conventions-genai) — separate GenAI conventions project; do not assume it defines Verity's reward/verifier schema.
- [Phoenix OTel setup](https://arize.com/docs/phoenix/tracing/how-to-tracing/setup-tracing/setup-using-phoenix-otel), [Phoenix self-hosting](https://arize.com/docs/phoenix/self-hosting), and [Phoenix annotations](https://arize.com/docs/phoenix/tracing/how-to-tracing/feedback-and-annotations/capture-feedback).
- [Langfuse instrumentation](https://langfuse.com/docs/observability/sdk/instrumentation), [data model and background processing](https://langfuse.com/docs/observability/data-model), [scores](https://langfuse.com/docs/evaluation/scores/overview), and [self-hosting](https://langfuse.com/self-hosting).
- [Ollama `/api/chat`](https://docs.ollama.com/api/chat) — response schema and example token/duration statistics.
