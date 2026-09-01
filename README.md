# verity-redteam

Adversarial auditing for RL environment verifiers. Given an environment, RedTeam
tests whether a model can obtain reward from the verifier without actually solving
the task.

This is the third tool in the Verity Labs stack. It depends on
[verity-core](https://github.com/Verity-AI-Labs/Verity-Core) for the environment
protocol, model client, scorecard, and batch runner, and on
[verity-corpus](https://github.com/Verity-AI-Labs/Verity-Corpus) for the VRC
exploit log.

v0.1 scores **V1 (verifier soundness)** only, with a single attack strategy
(`freeform`). The strategy registry, applicability hook, and evidence dict are
in place so later axes and strategies can slot in without refactoring.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Run the tests

```bash
uv run pytest
```

Lint and formatting, the same checks CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
```

## Usage

```bash
verity-redteam run <env-id> --corpus ../Verity-Corpus/manifests/
verity-redteam batch --domain code --resume
verity-redteam report --results-dir results/
```

A `redteam:` block in `verity.yaml` configures trial count, sampling temperature,
strategy names, VRC directory, and submission truncation:

```yaml
model_name: Qwen/Qwen2.5-7B-Instruct
results_dir: results
redteam:
  n_trials: 8
  temperature: 0.7
  strategies: [freeform]
  vrc_dir: vrc
  max_submission_length: 32768
```

## License

Apache-2.0. See [LICENSE](LICENSE).
