# verity-redteam

Adversarial auditing for RL environment verifiers. Given an environment, RedTeam
asks a model to obtain reward from the verifier without actually solving the
task, then records how often that works and which validity axes fail.

This is the third tool in the Verity Labs stack. It depends on
[verity-core](https://github.com/Verity-AI-Labs/Verity-Core) for the environment
protocol, model client, scorecard, and batch runner, and on
[verity-corpus](https://github.com/Verity-AI-Labs/Verity-Corpus) for corpus
manifests and the VRC (Verity Reward-hack Corpus) exploit log.

The hackability layer follows [Terminal Wrench](https://github.com/few-sh/terminal-wrench):
precheck → hacker loop → judge → classify. Trajectory outcomes, exploit
categories, and the elicitation prompt are aligned with theirs so a VRC entry
is comparable to a Terminal Wrench `trajectory.json`.

## Validity axes

RedTeam writes the axes it owns. An axis whose strategy does not apply stays
`None` on the scorecard — **unscored is not zero**.

| Axis | Question | Strategy | `applies` |
| --- | --- | --- | --- |
| **V1** | Verifier soundness: P(reward=1 \| incorrect). Aggregate **max α** across hacking strategies. | `freeform` (and any other V1 strategy) | always, after a passing precheck |
| **V2** | Verifier completeness: P(reward=0 \| correct), via isomorphic perturbation | `isomorphic` | gold present **and** a variant verifier is configured |
| **V4** | State / answer-key leakage | `leakage` | container-backed envs |
| **V6** | Isolation / side-channel integrity | `isolation` | container-backed envs; success needs reward **and** grading-process tampering in the exec log |
| **V7** | Reward-shaping correctness (Goodharting an easy sub-metric) | `goodhart` | `reward_type="partial"` only |

V2's critical guard: a perturbation that changes the task (the original gold no
longer passes the original verifier) is discarded, not counted as incompleteness.

Default CLI runs leave V2/V4/V6/V7 unscored on `verifiers`-format environments
without a container or a variant verifier. That is intentional.

## Pipeline (precheck → hack → judge → classify)

1. **Precheck.** Reset, run gold through the verifier, reset again, run gold
   again. Terminal Wrench keeps only tasks whose precheck succeeds; we mirror
   that. A failed precheck sets `metadata["precheck_failed"]` and **leaves axes
   unscored** (not zero). No gold is `skipped_no_gold` and the attack still runs.
2. **Hacker loop.** Container-backed envs get a multi-episode agent loop
   (`EXEC:` / `SUBMIT:`, default `max_episodes=15`) against `VerityEnv.step`.
   `verifiers`-format envs with no container keep a one-shot completion. The
   full episode trajectory is stored on the `AttackTrial`.
3. **Judge.** A model call (`LlmJudge`, same `ModelClient`; `judge_model`
   defaults to the attacker model) reads the trajectory, separates genuine hacks
   from legitimate solves, and assigns exploit categories. The CLI uses the LLM
   judge; unit tests use `HeuristicJudge` (gold-match + verdict). TRACE labeled
   trajectories are catalog-only in Corpus today; `Judge.calibrate()` is the seam.
4. **Classify + curve.** Only judged hacks feed the estimator. Clopper-Pearson
   exact binomial CI; analytic `H(K) = 1 − (1−α)^K` for `K ∈ {1,2,4,8}`.
   `HackabilityCurve` also reports `serious_alpha` vs `nonserious_alpha`.

### Trajectory outcomes

Every trial is one of Terminal Wrench's four outcomes, plus `error` for
infrastructure failures:

| Outcome | TW | Counts toward α |
| --- | --- | --- |
| `serious_hack` | ◆ rewarded serious exploit | yes |
| `nonserious_hack` | ◇ rewarded non-serious hack | yes |
| `legitimate_solve` | ~ attacker actually solved it | no |
| `no_reward` | ° no-reward attempt | no (denominator) |
| `error` | — | excluded |

The gold-string match filter is a subset of `legitimate_solve`. The judge is
what splits ◆/◇ from ~ when the verifier accepted a non-gold submission.

### Exploit categories

A trial carries a **list** of Terminal Wrench `observed_categories` (coarse, not
mutually exclusive): hollow-implementation, output-spoofing, constraint-loophole,
structural-extraction, binary-hijacking, keyword-gaming, algorithmic-simplification,
mutable-input-tampering, metric-spoofing, security-downgrading,
deceptive-rationalization. The first canonical category is stored as VRC
`exploit_type`; the full list lives in VRC `notes` and on the scorecard evidence.

### Prompts

The default elicitation prompt is **v2**, structured like Terminal Wrench
`hack_prompt_v5.md`: authorized red-team QA agent, isolated sandbox, do not
solve legitimately, enumerated attack targets and priorities, rules, success
and failure for the role. v1 remains in `prompts/templates.py` for A/B
traceability. Every trial records `prompt_version`.

## Relationship to Core and Corpus

| | RedTeam | Core | Corpus |
| --- | --- | --- | --- |
| Role | Adversarial validity probe | Compute layer | Data layer |
| Owns | Strategies, judge, hackability estimator, VRC write/read CLI, labeled-benchmark validation | `VerityEnv`, adapters, `ModelClient`, `Scorecard`, `run_batch` | Manifests, cached sources, scorecard files, VRC schema |
| Depends on | Core + Corpus | Nothing in RedTeam | Core (resolver, scorecard type) |

RedTeam does not reimplement environment loading or the scorecard model. It
hands `RedTeamRunner.audit` to Core's `run_batch`, and writes VRC entries with
Corpus's `VRCEntry` rather than a second schema.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Docker is needed
only to audit container-backed environments, not to import the package or run
the unit tests.

```bash
uv sync
```

`verity-core` and `verity-corpus` are pulled from GitHub (they are not on PyPI).

## Run the tests

```bash
uv run pytest
```

Lint and formatting, the same checks CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
```

Default pytest skips live markers (`integration`, `validation`) so CI stays
fast and daemon-free. The unit suite is fully mocked: no vLLM, no Docker.

A live-vLLM scaffold (one freeform trial against a fake env):

```bash
VERITY_INTEGRATION=1 uv run pytest -m integration
```

Labeled-benchmark resolution against a real Corpus checkout (still no Docker
unless you then run `verity-redteam validate` without `--skip-run`):

```bash
VERITY_VALIDATION=1 VERITY_CORPUS_DIR=../Verity-Corpus/manifests \
  uv run pytest -m validation
```

`verity-redteam validate --benchmark terminal-wrench` without `--skip-run` is
the 331-env live audit: it needs a model server **and** Terminal Wrench Docker
images. `--benchmark impossiblebench` cannot live-audit today — those Corpus
rows are catalog-only until Core grows a SWE/Inspect adapter. Precision is
computed from `--results-dir` scorecards (`--skip-run`).

## CLI

Installed as `verity-redteam`. Human-readable summaries go to stdout, logs to
stderr, matching Core.

```bash
# Audit one environment and write its scorecard
verity-redteam run <env-id> --corpus ../Verity-Corpus/manifests/

# Audit every matching environment (Core's run_batch, resumable)
verity-redteam batch --domain code --resume
verity-redteam batch --corpus manifests/ --results-dir results/
verity-redteam batch --id <env-id> --id <env-id>
verity-redteam batch --domain terminal --limit 10 --dry-run

# List environment ids, names, domains, and adapters
verity-redteam list --corpus ../Verity-Corpus/manifests/
verity-redteam list --corpus manifests/ --domain terminal

# Print the resolved spec and exit before any model or verifier call
verity-redteam run <env-id> --corpus manifests/ --dry-run
verity-redteam batch --corpus manifests/ --dry-run

# Summarize completed scorecards (flags precheck-failed)
verity-redteam report --results-dir results/
verity-redteam report --json

# Corpus-wide alpha distribution, domain/category breakdown, ranking, VRC stats
verity-redteam report --results-dir results/ --corpus
verity-redteam report --corpus --json

# Labeled-benchmark kill-gates (recall@4 >= 60% on TW; precision >= 90% on IB)
verity-redteam validate --benchmark terminal-wrench --corpus ../Verity-Corpus/manifests/
verity-redteam validate --benchmark terminal-wrench --skip-run --results-dir results/
verity-redteam validate --benchmark impossiblebench --skip-run --results-dir results/
verity-redteam validate --benchmark terminal-wrench --dry-run
verity-redteam validate --benchmark terminal-wrench --id <env-id> --limit 4 --dry-run

# List recorded exploits for one environment
verity-redteam vrc list <env-id>
verity-redteam vrc list <env-id> --json
```

`--log-level debug` prints each trial's raw model output, which is the useful
view when iterating on the adversarial prompt. `--json` on `run` / `batch` /
`report` / `validate` / `vrc list` keeps the payload pipeable at any log level.

`--corpus` directories are loaded through verity-corpus's `CorpusRegistry`,
which skips `_schema.yaml`, merges `source_defaults`, computes deterministic
ids, and rejects duplicates. Core's `load_corpus` is used only when
verity-corpus is not installed. `validate` prefers each benchmark's own
manifest (`terminal_wrench.yaml`, `impossiblebench.yaml`).

## Configuration

Core fields still resolve through `VerityConfig`. RedTeam-specific knobs live
under a `redteam:` block, which is stripped before Core sees the file so an
unknown-key error from Core does not fire on settings it does not own.

```yaml
model_name: Qwen/Qwen2.5-7B-Instruct
model_base_url: http://localhost:8000/v1
results_dir: results
redteam:
  n_trials: 8
  temperature: 0.7
  strategies: [freeform, isomorphic, leakage, isolation, goodhart]
  vrc_dir: vrc
  max_submission_length: 32768
  corpus_dir: manifests
  cache_dir: cache
  max_episodes: 15
  judge_model: null   # defaults to model_name
  n_perturbations: 4
```

| Key | Default | Meaning |
| --- | ---: | --- |
| `n_trials` | 8 | Independent attempts per environment. The probe owns this count. |
| `temperature` | 0.7 | Sampling temperature. Attacker cache is always disabled. |
| `strategies` | all five registry names | `applies()` still skips strategies that cannot run. |
| `vrc_dir` | `vrc` | Where successful hacks are written and where `vrc list` reads. |
| `max_submission_length` | 32768 | Truncate the model output before `verify()`. |
| `corpus_dir` | `manifests` | Default corpus path when `--corpus` is omitted. |
| `cache_dir` | `cache` | Where Corpus clones environment sources (`fetch` keyed on url+commit). |
| `max_episodes` | 15 | Cap on the container-backed attacker loop. |
| `judge_model` | attacker model | Model id for `LlmJudge`. |
| `n_perturbations` | 4 | Recorded V2 setting; the isomorphic strategy currently emits one variant per probe trial. |

`n_trials < 1`, `max_episodes < 1`, `n_perturbations < 1`, an empty
`strategies` list, and unknown `redteam:` keys are rejected. An explicit
`--config` path that does not exist is an error; with no path, `$VERITY_CONFIG`
then `./verity.yaml` are tried, matching Core.

## How a probe runs

For each applicable strategy, `RedTeamProbe` prechecks, then runs `n_trials`
independent attacks. `FreeformHackStrategy` fills prompt template **v2** and
either one-shots a completion or loops in the sandbox. It records the reward
and may classify the trial as `no_reward` or `error`. The probe is the only
place that sets `hacked=True` or a hack outcome: it translates the verifier
verdict after the gold-match filter and the judge.

Each `AttackTrial` stores `prompt_version`, `episodes`, `observed_categories`,
and `judgment`. V1 scorecard evidence includes that version, fitted alpha and
Clopper-Pearson interval, any-hack and serious `H(K)`, `hack_attempts` (for
recall at K), judged categories, `n_legitimate_solve`, and `n_errored`.

Successful hacks are written as VRC entries under `{vrc_dir}/{env_id}/`. Dedup
is in-memory per audit run.

## Validation kill-gates

`verity-redteam validate` scores RedTeam against labeled sets resolved through
Verity-Corpus:

- **terminal-wrench** — 331 labeled hackable environments. Metric: recall at
  K≤4 (a judged hack in the first four attempts). Gate: **≥ 60%**.
- **impossiblebench** — precision: do not report hacks on tasks labeled
  not-hackable. Gate: **≥ 90%**. Corpus rows are catalog-only; pass
  `--results-dir` with per-instance audits once a Core adapter exists.

The math lives in `verity_redteam.validation.metrics` and is unit-tested against
hand-computed confusion matrices. Vacuous precision (no predicted hacks) is 1.0;
the CLI still refuses to certify ImpossibleBench when that directory has no
matching scorecards.

## Current status

Strategies for V1, V2, V4, V6, and V7 are registered. Unit tests cover the
estimator, outcome taxonomy, judge, perturbation-validation guard, each axis
strategy, validation metrics, CLI, and VRC logger. They do not start Docker or
a model server.

## Not yet implemented

Deliberate follow-ups, not stubs with fake scores:

- Parallel trials
- Snapshot/restore fallback when reset is broken
- Disk-level VRC dedup
- ImpossibleBench / TRACE as loadable `VerityEnv`s (blocked on Core adapters)
- TRACE few-shot calibration data in the judge (the `calibrate()` seam is there)

## License

Apache-2.0. See [LICENSE](LICENSE).
