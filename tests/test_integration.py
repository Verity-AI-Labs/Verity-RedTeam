"""Live vLLM integration scaffold. Skipped by default.

Run later with::

    VERITY_INTEGRATION=1 uv run pytest -m integration
"""

from __future__ import annotations

import os

import pytest
from tests.conftest import FakeEnv, make_spec

from verity_redteam.probe import RedTeamProbe
from verity_redteam.strategies.freeform import FreeformHackStrategy

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("VERITY_INTEGRATION") != "1",
    reason="requires a live vLLM server; run with VERITY_INTEGRATION=1",
)
def test_freeform_probe_against_live_vllm() -> None:
    from verity_core.models import ModelClient

    from verity_redteam.config import load_redteam_config

    config = load_redteam_config(env={})
    spec = make_spec(has_gold=False)
    env = FakeEnv(spec=spec, gold=None, passing=frozenset())
    strategy = FreeformHackStrategy(model=config.model_name, n_trials=1, temperature=0.7)
    with ModelClient.from_config(config.core) as client:
        result = RedTeamProbe(strategy, n_trials=1).run(env, spec, client)
    assert len(result.trials) == 1
    trial = result.trials[0]
    assert trial.strategy == "freeform"
    assert trial.model_response is not None
    assert trial.error is None
    assert env.verify_calls, "the live completion should have been submitted"
