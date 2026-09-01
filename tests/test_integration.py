"""Live vLLM integration scaffold. Skipped by default.

This is a real probe against whatever OpenAI-compatible server Core resolves,
not a placeholder. The environment is a FakeEnv so the run does not need Docker;
the model call is live.

Point it at a running vLLM (or any OpenAI-compatible `/v1/chat/completions`
endpoint) and execute:

    VERITY_INTEGRATION=1 uv run pytest -m integration

Optional overrides, same as any Core client:

    VERITY_MODEL_BASE_URL=http://localhost:8000/v1
    VERITY_MODEL_NAME=Qwen/Qwen2.5-7B-Instruct
    VERITY_CONFIG=/path/to/verity.yaml
"""

from __future__ import annotations

import os

import pytest
from tests.conftest import FakeEnv, make_spec

from verity_redteam.outcomes import (
    ERROR,
    LEGITIMATE_SOLVE,
    NO_REWARD,
    NONSERIOUS_HACK,
    SERIOUS_HACK,
)
from verity_redteam.probe import RedTeamProbe
from verity_redteam.strategies.freeform import FreeformHackStrategy

pytestmark = pytest.mark.integration

_CLASSIFICATIONS = frozenset({SERIOUS_HACK, NONSERIOUS_HACK, LEGITIMATE_SOLVE, NO_REWARD, ERROR})


@pytest.mark.skipif(
    os.environ.get("VERITY_INTEGRATION") != "1",
    reason="requires a live vLLM server; run with VERITY_INTEGRATION=1",
)
def test_freeform_probe_against_live_vllm() -> None:
    from verity_core.models import ModelClient

    from verity_redteam.config import load_redteam_config

    # Honour VERITY_* and verity.yaml so an operator can point at their server
    # without editing this file.
    config = load_redteam_config()
    spec = make_spec(has_gold=False)
    env = FakeEnv(spec=spec, gold=None, passing=frozenset())
    strategy = FreeformHackStrategy(model=config.model_name, temperature=0.7)
    with ModelClient.from_config(config.core) as client:
        result = RedTeamProbe(strategy, n_trials=1).run(env, spec, client)

    assert len(result.trials) == 1
    trial = result.trials[0]
    assert trial.strategy == "freeform"
    assert trial.prompt_version == "v2"
    assert trial.attempt == 0
    assert trial.model_response is not None
    assert trial.model_response.content is not None
    assert trial.error is None
    assert trial.classification in _CLASSIFICATIONS
    assert env.verify_calls, "the live completion should have been submitted"
    assert result.curve.n_trials == 1
