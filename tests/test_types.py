"""Tests for AttackTrial, Episode, and ProbeResult records."""

from __future__ import annotations

from uuid import UUID

from verity_core.env import RewardResult
from verity_core.models import ModelResponse, TokenUsage

from verity_redteam.analysis.hackability import HackabilityCurve
from verity_redteam.outcomes import NO_REWARD, SERIOUS_HACK
from verity_redteam.types import AttackTrial, Episode, ProbeResult


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
        assert trial.classification == NO_REWARD
        assert trial.error is None
        assert trial.reward == RewardResult(0.0, False)
        assert trial.messages == []
        assert trial.model_response is None
        assert trial.episodes == []
        assert trial.observed_categories == []
        assert trial.judgment is None
        assert trial.evidence == {}

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
            classification=SERIOUS_HACK,
            messages=[{"role": "user", "content": "hack"}],
            model_id="Qwen/Qwen2.5-7B-Instruct",
            model_response=ModelResponse(
                content="print(1)",
                model="Qwen/Qwen2.5-7B-Instruct",
                usage=TokenUsage(3, 2, 5),
            ),
            duration=0.5,
            observed_categories=["hollow-implementation", "output-spoofing"],
            episodes=[
                Episode(
                    index=0,
                    prompt="look around",
                    response="EXEC: ls",
                    tool_calls=[{"type": "exec", "command": "ls"}],
                    observations=["file.txt\n"],
                )
            ],
            judgment={"outcome": SERIOUS_HACK},
            evidence={"tampering": True},
        )
        payload = trial.to_dict()
        assert payload["trial_id"] == trial.trial_id
        assert payload["hacked"] is True
        assert payload["classification"] == SERIOUS_HACK
        assert payload["reward"]["verdict"] is True
        assert payload["model_response"]["usage"]["total_tokens"] == 5
        assert payload["messages"][0]["role"] == "user"
        assert payload["prompt_version"] == ""
        assert payload["observed_categories"] == ["hollow-implementation", "output-spoofing"]
        assert payload["episodes"][0]["tool_calls"][0]["command"] == "ls"
        assert payload["judgment"]["outcome"] == SERIOUS_HACK
        assert payload["evidence"]["tampering"] is True

    def test_to_dict_serializes_a_missing_model_response_as_none(self) -> None:
        assert _trial().to_dict()["model_response"] is None

    def test_unknown_categories_are_dropped(self) -> None:
        trial = _trial(observed_categories=["nope", "binary-hijacking"])
        assert trial.observed_categories == ["binary-hijacking"]


class TestEpisode:
    def test_round_trips_through_a_dict(self) -> None:
        episode = Episode(
            index=2,
            prompt="next",
            response="SUBMIT:\npass",
            tool_calls=[{"type": "submit", "body": "pass"}],
            observations=["ok"],
        )
        assert episode.to_dict() == {
            "index": 2,
            "prompt": "next",
            "response": "SUBMIT:\npass",
            "tool_calls": [{"type": "submit", "body": "pass"}],
            "observations": ["ok"],
        }


class TestProbeResult:
    def test_holds_trials_and_a_curve(self) -> None:
        trial = _trial(hacked=True, classification=SERIOUS_HACK)
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
            metadata={"precheck_failed": False},
        )
        payload = result.to_dict()
        assert payload["total_tokens"] == 12
        assert payload["curve"]["alpha"] == 1.0
        assert payload["metadata"]["precheck_failed"] is False
        assert len(payload["trials"]) == 1
