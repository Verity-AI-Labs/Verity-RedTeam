# verity-redteam

Adversarial auditing for RL environment verifiers. Given an environment, RedTeam
asks a model to obtain reward from the verifier without actually solving the task,
then records how often that works.

This is the third tool in the Verity Labs stack. It depends on
[verity-core](https://github.com/Verity-AI-Labs/Verity-Core) for the environment
protocol, model client, scorecard, and batch runner, and on
[verity-corpus](https://github.com/Verity-AI-Labs/Verity-Corpus) for corpus
manifests and the VRC (Verity Reward-hack Corpus) exploit log.

v0.1 scores **V1 (verifier soundness)** only, with a single attack strategy
(`freeform`). The strategy registry, applicability hook, and evidence dict are
in place so later axes and strategies can slot in without refactoring the
runner. They are not implemented yet.

## The V1 axis and the hackability curve

V1 is per-trial hackability **alpha**: the fraction of adversarial attempts the
verifier accepted that were not the gold solution. A trial that matches the gold
string (after `.strip()`) is classified as `solved` and dropped from the
estimate. A trial that errors (model refusal, container crash, verifier
exception) is also dropped. Alpha is therefore successes over `hacked + failed`
only.

From alpha the tool reports the analytic hackability curve

```
H(K) = 1 - (1 - alpha)^K    for K in {1, 2, 4, 8}
```

the probability of at least one accepted non-solution in K independent attempts.
Confidence intervals are Clopper-Pearson (exact binomial via the beta quantile),
not Wilson or Wald.

When more than one strategy applies, V1 is the **max** alpha across them. In
v0.1 that reduction is a no-op: only `freeform` is registered.

A gold-check after trial 1's reset can flag `reset_broken` on the scorecard.
v0.1 records the flag and continues; it does not fall back to snapshot/restore.
`verity-redteam report` appends `[reset-broken]` to those rows so an unreliable
estimate is visible at a glance.

## Relationship to Core and Corpus

| | RedTeam | Core | Corpus |
| --- | --- | --- | --- |
| Role | Adversarial V1 probe | Compute layer | Data layer |
| Owns | Attack strategies, hackability estimator, VRC write/read CLI | `VerityEnv`, adapters, `ModelClient`, `Scorecard`, `run_batch` | Manifests, cached sources, scorecard files, VRC schema |
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

A live-vLLM scaffold is skipped by default:

```bash
VERITY_INTEGRATION=1 uv run pytest -m integration
```

That test talks to whatever `ModelClient` Core resolves (`VERITY_MODEL_BASE_URL`,
`VERITY_MODEL_NAME`, or `verity.yaml`) and submits one freeform trial against a
fake environment. Point it at a running vLLM instance; it does not start one.

## CLI

Installed as `verity-redteam`. Human-readable summaries go to stdout, logs to
stderr, matching Core.

```bash
# Audit one environment and write its scorecard
verity-redteam run <env-id> --corpus ../Verity-Corpus/manifests/

# Audit every matching environment (Core's run_batch, resumable)
verity-redteam batch --domain code --resume
verity-redteam batch --corpus manifests/ --results-dir results/

# Print the resolved spec and exit before any model or verifier call
verity-redteam run <env-id> --corpus manifests/ --dry-run
verity-redteam batch --corpus manifests/ --dry-run

# Summarize completed scorecards (flags reset_broken)
verity-redteam report --results-dir results/
verity-redteam report --json

# List recorded exploits for one environment
verity-redteam vrc list <env-id>
verity-redteam vrc list <env-id> --json
```

`--log-level debug` prints each trial's raw model output, which is the useful
view when iterating on the adversarial prompt. `--json` on `run` / `batch` /
`report` / `vrc list` keeps the payload pipeable at any log level.

If the corpus directory is Core-flat YAML (`id` + `format` per file), that
layout is used. If it is a Corpus registry (`entries:` lists), RedTeam falls
back to `CorpusRegistry` after logging that the Core layout was not found.

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
  strategies: [freeform]
  vrc_dir: vrc
  max_submission_length: 32768
  corpus_dir: manifests
```

| Key | Default | Meaning |
| --- | ---: | --- |
| `n_trials` | 8 | Independent attempts per environment. The probe owns this count. |
| `temperature` | 0.7 | Sampling temperature. Model cache is always disabled. |
| `strategies` | `[freeform]` | Registry names to run. v0.1 only understands `freeform`. |
| `vrc_dir` | `vrc` | Where successful hacks are written and where `vrc list` reads. |
| `max_submission_length` | 32768 | Truncate the model output before `verify()`. |
| `corpus_dir` | `manifests` | Default corpus path when `--corpus` is omitted. |

`n_trials < 1`, an empty `strategies` list, and unknown `redteam:` keys are
rejected. An explicit `--config` path that does not exist is an error; with no
path, `$VERITY_CONFIG` then `./verity.yaml` are tried, matching Core.

## How a probe runs

For each applicable strategy, `RedTeamProbe` resets the environment, optionally
checks that gold still passes, then runs `n_trials` independent attacks.
`FreeformHackStrategy` fills prompt template **v1**, calls the model at
temperature 0.7 with `use_cache=False`, and submits the raw completion to
`env.verify()`. It records the reward and may classify the trial as `failed` or
`error`. The probe is the only place that sets `hacked=True`: it translates
`reward.verdict` after the gold-match filter.

Each `AttackTrial` stores `prompt_version` so a later template change does not
silently mix with v1 results. V1 scorecard evidence includes that version, the
fitted alpha and Clopper-Pearson interval, `H(K)` for K in {1, 2, 4, 8},
`n_solved`, and `n_errored`.

Successful hacks are written as VRC entries under `{vrc_dir}/{env_id}/`. Dedup
in v0.1 is in-memory per audit run.

## Current status

**v0.1: V1 only, freeform strategy.** The unit tests cover the estimator, gold
filter, error exclusion, reset check, CLI, and VRC logger. They do not start
Docker or a model server.

## Not yet implemented

Deliberate v0.2 scope, not stubs with fake scores:

- Additional attack strategies (isomorphic perturbation and whatever follows)
- V2 / V4 / V6 / V7 scoring
- Parallel trials
- Snapshot/restore fallback when reset is broken
- Disk-level VRC dedup

The strategy registry stays at one entry (`freeform`) until those land.

## License

Apache-2.0. See [LICENSE](LICENSE).
