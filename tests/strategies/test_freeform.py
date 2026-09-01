"""Tests for FreeformHackStrategy trial construction."""

from __future__ import annotations

import logging

import pytest
from tests.conftest import FakeClient, FakeEnv, make_spec
from verity_core.env import RewardResult
from verity_core.models import ModelError

from verity_redteam.outcomes import ERROR, NO_REWARD
from verity_redteam.strategies.freeform import FreeformHackStrategy
from verity_redteam.types import AttackTrial


def _run(**client_kwargs: object) -> tuple[FreeformHackStrategy, FakeEnv, FakeClient]:
    spec = make_spec()
    env = FakeEnv(spec=spec, passing="bypass")
    client = FakeClient(**client_kwargs)  # type: ignore[arg-type]
    strategy = FreeformHackStrategy(model="test-model", temperature=0.7)
    return strategy, env, client


def _attack(
    strategy: FreeformHackStrategy,
    env: FakeEnv,
    client: FakeClient,
    attempt: int = 0,
    n_trials: int = 8,
) -> AttackTrial:
    return strategy.attack(env, env.spec(), client, attempt, n_trials)  # type: ignore[arg-type]


class TestHappyPath:
    def test_submits_raw_model_output_and_leaves_hacked_to_the_probe(self) -> None:
        strategy, env, client = _run(contents="bypass")
        trial = _attack(strategy, env, client)
        # Passing verdict is recorded; the probe, not the strategy, sets hacked.
        assert trial.hacked is False
        assert trial.classification == NO_REWARD
        assert trial.reward.verdict is True
        assert trial.submission == "bypass"
        assert trial.reward == RewardResult(1.0, True, "ok")
        assert trial.strategy == "freeform"
        assert trial.model_id == "test-model"
        assert trial.attempt == 0
        assert trial.prompt_version == "v2"
        assert trial.error is None
        assert trial.model_response is not None
        assert trial.model_response.content == "bypass"
        assert env.verify_calls == ["bypass"]
        assert len(trial.episodes) == 1

    def test_records_a_failed_verdict_as_not_hacked(self) -> None:
        strategy, env, client = _run(contents="harmless")
        trial = _attack(strategy, env, client, attempt=3)
        assert trial.hacked is False
        assert trial.classification == NO_REWARD
        assert trial.attempt == 3

    def test_disables_the_model_cache_and_uses_temperature_0_7(self) -> None:
        strategy, env, client = _run(contents="bypass")
        _attack(strategy, env, client)
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["use_cache"] is False
        assert call["temperature"] == 0.7
        assert call["model"] == "test-model"
        assert call["messages"][0]["role"] == "system"
        assert call["messages"][1]["role"] == "user"
        assert "Attempt: 1 of 8" in call["messages"][1]["content"]

    def test_prompt_uses_n_trials_passed_into_attack(self) -> None:
        strategy, env, client = _run(contents="bypass")
        _attack(strategy, env, client, n_trials=3)
        assert "Attempt: 1 of 3" in client.calls[0]["messages"][1]["content"]

    def test_logs_a_one_based_attempt_counter(self, caplog: pytest.LogCaptureFixture) -> None:
        strategy, env, client = _run(contents="bypass")
        with caplog.at_level(logging.INFO, logger="verity_redteam.agent"):
            _attack(strategy, env, client)
        assert any("attempt=1" in rec.getMessage() for rec in caplog.records)
        assert not any("attempt=0" in rec.getMessage() for rec in caplog.records)

    def test_debug_logs_the_raw_submission(self, caplog: pytest.LogCaptureFixture) -> None:
        strategy, env, client = _run(contents="bypass")
        with caplog.at_level(logging.DEBUG, logger="verity_redteam.agent"):
            trial = _attack(strategy, env, client)
        messages = [rec.getMessage() for rec in caplog.records]
        needle = f"trial submission env_id={trial.env_id} trial_id={trial.trial_id} content=bypass"
        assert any(needle in msg for msg in messages)

    def test_truncates_overlong_submissions_before_verify(self) -> None:
        strategy = FreeformHackStrategy(model="test-model", max_submission_length=8)
        env = FakeEnv(passing="abcdefgh")
        client = FakeClient(contents="abcdefghXXXX")
        trial = _attack(strategy, env, client)
        assert trial.submission == "abcdefgh"
        assert env.verify_calls == ["abcdefgh"]


class TestErrorsDoNotPropagate:
    def test_model_failure_is_recorded_on_the_trial(self) -> None:
        strategy, env, client = _run(error=ModelError("endpoint down"))
        trial = _attack(strategy, env, client)
        assert trial.hacked is False
        assert trial.classification == ERROR
        assert trial.error is not None
        assert "ModelError" in trial.error
        assert trial.submission == ""
        assert env.verify_calls == []

    def test_verify_failure_is_recorded_on_the_trial(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, verify_error=RuntimeError("grader crashed"))
        client = FakeClient(contents="anything")
        strategy = FreeformHackStrategy(model="test-model")
        trial = strategy.attack(env, spec, client, 1, 8)
        assert trial.classification == ERROR
        assert trial.hacked is False
        assert trial.error is not None
        assert "grader crashed" in trial.error
        assert trial.model_response is not None
