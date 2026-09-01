"""Tests for the V4 leakage strategy."""

from __future__ import annotations

from tests.conftest import FakeClient, FakeEnv, FakeRunner, make_spec

from verity_redteam.probe import RedTeamProbe
from verity_redteam.strategies.leakage import LeakageStrategy


class TestApplies:
    def test_container_only(self) -> None:
        strategy = LeakageStrategy(model="m")
        spec = make_spec()
        assert strategy.applies(spec) is False
        env = FakeEnv(spec=spec, gold=None)
        env.entry = {"format": "terminal"}
        assert strategy.applies(spec, env) is True
        env.entry = {"format": "verifiers"}
        assert strategy.applies(spec, env) is False
        assert strategy.target_axes == ("V4",)


class TestAttack:
    def test_uses_the_leakage_prompt_and_counts_a_passing_bypass(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None, passing="KEY")
        env.entry = {"format": "terminal"}
        env.runner = FakeRunner()
        client = FakeClient(contents="EXEC: cat /tests/secret\nSUBMIT:\nKEY")
        strategy = LeakageStrategy(model="m")
        result = RedTeamProbe(strategy, n_trials=1).run(env, spec, client)  # type: ignore[arg-type]
        trial = result.trials[0]
        assert trial.hacked is True
        assert "non-causal" in client.calls[0]["messages"][0]["content"]
        assert env.runner.seen == ["cat /tests/secret"]
        assert result.curve.alpha == 1.0
