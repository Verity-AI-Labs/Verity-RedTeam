"""Tests for AttackTrial and ProbeResult records."""

from __future__ import annotations

from uuid import UUID

from verity_core.env import RewardResult
from verity_core.models import ModelResponse, TokenUsage

from verity_redteam.analysis.hackability import HackabilityCurve
from verity_redteam.types import AttackTrial, ProbeResult


def _trial(**overrides: object) -> AttackTrial:
    fields: dict = {
        "env_id": "corpus/task-1",
        "strategy": "freeform",
        "attempt": 0,
    }
    fields.update(overrides)
    return AttackTrial(**fields)


class TestAttackTrial:
    def test_defaults_are_an_unsuccessful_trial(self) -> None:
        trial = _trial()
        assert trial.submission == ""
        assert trial.hacked is False
        assert trial.classification == "failed"
        assert trial.error is None
        assert trial.reward == RewardResult(0.0, False)
        assert trial.messages == []
        assert trial.model_response is None

    def test_trial_id_is_a_uuid4(self) -> None:
        parsed = UUID(_trial().trial_id)
        assert parsed.version == 4

    def test_each_trial_gets_a_distinct_id(self) -> None:
        assert _trial().trial_id != _trial().trial_id

    def test_round_trips_through_a_dict(self) -> None:
        trial = _trial(
            submission="print(1)",
            reward=RewardResult(1.0, True, "ok"),
            hacked=True,
            classification="hacked",
            messages=[{"role": "user", "content": "hack"}],
            model_id="Qwen/Qwen2.5-7B-Instruct",
            model_response=ModelResponse(
                content="print(1)",
                model="Qwen/Qwen2.5-7B-Instruct",
                usage=TokenUsage(3, 2, 5),
            ),
            duration=0.5,
        )
        payload = trial.to_dict()
        assert payload["trial_id"] == trial.trial_id
        assert payload["hacked"] is True
        assert payload["reward"]["verdict"] is True
        assert payload["model_response"]["usage"]["total_tokens"] == 5
        assert payload["messages"][0]["role"] == "user"

    def test_to_dict_serializes_a_missing_model_response_as_none(self) -> None:
        assert _trial().to_dict()["model_response"] is None


class TestProbeResult:
    def test_holds_trials_and_a_curve(self) -> None:
        trial = _trial(hacked=True, classification="hacked")
        curve = HackabilityCurve(
            alpha=1.0,
            alpha_ci_lower=0.025,
            alpha_ci_upper=1.0,
            n_trials=1,
            n_successes=1,
            curve={1: 1.0, 2: 1.0, 4: 1.0, 8: 1.0},
            strategy="freeform",
            trial_ids=(trial.trial_id,),
        )
        result = ProbeResult(
            strategy="freeform",
            env_id="corpus/task-1",
            trials=[trial],
            curve=curve,
            total_tokens=12,
            duration=1.25,
            metadata={"reset_broken": False},
        )
        payload = result.to_dict()
        assert payload["total_tokens"] == 12
        assert payload["curve"]["alpha"] == 1.0
        assert payload["metadata"]["reset_broken"] is False
        assert len(payload["trials"]) == 1
