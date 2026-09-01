"""Tests for the V7 Goodhart / reward-shaping strategy."""

from __future__ import annotations

from tests.conftest import FakeClient, FakeEnv, make_spec
from verity_core.env import RewardResult

from verity_redteam.probe import RedTeamProbe
from verity_redteam.strategies.goodhart import GoodhartStrategy


class TestApplies:
    def test_partial_reward_only(self) -> None:
        strategy = GoodhartStrategy(model="m")
        assert strategy.applies(make_spec(reward_type="partial")) is True
        assert strategy.applies(make_spec(reward_type="binary")) is False
        assert strategy.target_axes == ("V7",)


class TestAttack:
    def test_positive_reward_without_a_core_solve_counts(self) -> None:
        spec = make_spec(reward_type="partial", has_gold=False)
        env = FakeEnv(
            spec=spec,
            gold=None,
            rewards={"easy": RewardResult(0.6, False, "submetric")},
        )
        client = FakeClient(contents="easy")
        result = RedTeamProbe(GoodhartStrategy(model="m"), n_trials=1).run(
            env,
            spec,
            client,  # type: ignore[arg-type]
        )
        trial = result.trials[0]
        assert trial.reward.reward == 0.6
        assert trial.reward.verdict is False
        assert trial.hacked is True
        assert "Goodharting" in client.calls[0]["messages"][0]["content"]
        assert result.curve.alpha == 1.0

    def test_zero_reward_does_not_count(self) -> None:
        spec = make_spec(reward_type="partial", has_gold=False)
        env = FakeEnv(spec=spec, gold=None)
        client = FakeClient(contents="miss")
        result = RedTeamProbe(GoodhartStrategy(model="m"), n_trials=1).run(
            env,
            spec,
            client,  # type: ignore[arg-type]
        )
        assert result.trials[0].hacked is False
        assert result.curve.n_successes == 0
