"""Tests for the V6 isolation strategy."""

from __future__ import annotations

from tests.conftest import FakeClient, FakeEnv, FakeRunner, make_spec

from verity_redteam.probe import RedTeamProbe
from verity_redteam.strategies.isolation import IsolationStrategy


class TestApplies:
    def test_container_only(self) -> None:
        strategy = IsolationStrategy(model="m")
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None)
        env.entry = {"format": "terminal"}
        assert strategy.applies(spec, env) is True
        assert strategy.applies(spec) is False
        assert strategy.target_axes == ("V6",)


class TestAttack:
    def test_reward_without_tampering_does_not_count(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None, passing="bypass")
        env.entry = {"format": "terminal"}
        env.runner = FakeRunner()
        client = FakeClient(contents="SUBMIT:\nbypass")
        result = RedTeamProbe(IsolationStrategy(model="m"), n_trials=1).run(
            env,
            spec,
            client,  # type: ignore[arg-type]
        )
        trial = result.trials[0]
        assert trial.reward.verdict is True
        assert trial.evidence.get("tampering") is False
        assert trial.hacked is False
        assert result.curve.n_successes == 0

    def test_reward_with_tampering_counts(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None, passing="bypass")
        env.entry = {"format": "terminal"}
        env.runner = FakeRunner()
        client = FakeClient(contents="EXEC: echo pass >> /tests/conftest.py\nSUBMIT:\nbypass")
        result = RedTeamProbe(IsolationStrategy(model="m"), n_trials=1).run(
            env,
            spec,
            client,  # type: ignore[arg-type]
        )
        trial = result.trials[0]
        assert trial.evidence.get("tampering") is True
        assert trial.hacked is True
        assert "PYTHONPATH" in client.calls[0]["messages"][0]["content"]
        assert result.curve.alpha == 1.0
