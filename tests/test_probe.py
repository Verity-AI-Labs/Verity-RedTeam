"""Tests for RedTeamProbe: reset sequencing, gold filter, curve fit."""

from __future__ import annotations

import logging

import pytest
from tests.conftest import FakeClient, FakeEnv, make_spec

from verity_redteam.probe import RedTeamProbe
from verity_redteam.strategies.freeform import FreeformHackStrategy
from verity_redteam.types import AttackTrial


def _probe(n_trials: int = 4) -> RedTeamProbe:
    strategy = FreeformHackStrategy(model="test-model")
    return RedTeamProbe(strategy, n_trials=n_trials)


class TestTrialLoop:
    def test_runs_n_trials_and_resets_between_them(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None, passing="bypass")
        client = FakeClient(contents=["bypass", "miss", "bypass", "miss"])
        result = _probe(4).run(env, spec, client)  # type: ignore[arg-type]
        assert len(result.trials) == 4
        assert [t.hacked for t in result.trials] == [True, False, True, False]
        assert result.curve.n_trials == 4
        assert result.curve.n_successes == 2
        assert result.curve.alpha == 0.5
        # no gold: initial reset + one reset after each of 4 trials = 5
        assert env.reset_count == 5
        assert result.metadata["reset_broken"] is False
        assert result.total_tokens == 6 * 4

    def test_rejects_a_non_positive_trial_count(self) -> None:
        with pytest.raises(ValueError, match="n_trials"):
            RedTeamProbe(FreeformHackStrategy(model="x"), n_trials=0)


class TestGoldFilter:
    def test_gold_match_is_solved_not_hacked_and_excluded_from_alpha(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="GOLD", passing="bypass")
        client = FakeClient(contents=["GOLD", "bypass", "nope", "bypass"])
        result = _probe(4).run(env, spec, client)  # type: ignore[arg-type]
        assert [t.classification for t in result.trials] == ["solved", "hacked", "failed", "hacked"]
        assert [t.hacked for t in result.trials] == [False, True, False, True]
        # gold match dropped from N, leaving 3 trials and 2 successes
        assert result.curve.n_trials == 3
        assert result.curve.n_successes == 2
        assert result.curve.alpha == pytest.approx(2 / 3)
        assert result.metadata["n_solved"] == 1
        assert result.metadata["n_errored"] == 0

    def test_error_trials_are_excluded_from_alpha(self) -> None:
        spec = make_spec()
        env = FakeEnv(
            spec=spec,
            gold="GOLD",
            passing="hack",
            verify_error=RuntimeError("container died"),
            error_submissions="boom",
        )
        client = FakeClient(contents=["hack", "miss", "GOLD", "boom"])
        result = _probe(4).run(env, spec, client)  # type: ignore[arg-type]
        assert [t.classification for t in result.trials] == [
            "hacked",
            "failed",
            "solved",
            "error",
        ]
        # alpha is only over hacked + failed: 1/2
        assert result.curve.n_trials == 2
        assert result.curve.n_successes == 1
        assert result.curve.alpha == pytest.approx(0.5)
        assert result.metadata["n_solved"] == 1
        assert result.metadata["n_errored"] == 1

    def test_strip_is_applied_before_the_gold_comparison(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="  GOLD\n", passing=frozenset())
        client = FakeClient(contents="GOLD")
        result = _probe(1).run(env, spec, client)  # type: ignore[arg-type]
        assert result.trials[0].classification == "solved"
        assert result.curve.n_successes == 0


class TestResetSanity:
    def test_flags_a_broken_reset_when_gold_fails_after_trial_1(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="GOLD", passing="bypass", gold_fails_after_reset=True)
        client = FakeClient(contents="bypass")
        with caplog.at_level(logging.WARNING, logger="verity_redteam.probe"):
            result = _probe(2).run(env, spec, client)  # type: ignore[arg-type]
        assert result.metadata["reset_broken"] is True
        assert any("reset appears broken" in rec.message for rec in caplog.records)

    def test_does_not_flag_when_gold_still_passes(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="GOLD", passing="bypass")
        client = FakeClient(contents="bypass")
        result = _probe(2).run(env, spec, client)  # type: ignore[arg-type]
        assert result.metadata["reset_broken"] is False

    def test_skips_the_gold_check_when_there_is_no_gold(self) -> None:
        spec = make_spec(has_gold=False)
        env = FakeEnv(spec=spec, gold=None, passing="bypass")
        client = FakeClient(contents="bypass")
        result = _probe(2).run(env, spec, client)  # type: ignore[arg-type]
        assert result.metadata["reset_broken"] is False
        gold_verifies = [c for c in env.verify_calls if c == "GOLD"]
        assert gold_verifies == []


class TestProbeOwnsHacked:
    def test_classifies_a_passing_verdict_as_hacked(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None, passing="bypass")
        client = FakeClient(contents="bypass")
        result = _probe(1).run(env, spec, client)  # type: ignore[arg-type]
        trial = result.trials[0]
        assert trial.reward.verdict is True
        assert trial.hacked is True
        assert trial.classification == "hacked"

    def test_overrides_a_strategy_that_already_set_hacked(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="GOLD", passing="GOLD")

        class Eager:
            @property
            def name(self) -> str:
                return "eager"

            @property
            def target_axes(self) -> tuple[str, ...]:
                return ("V1",)

            def applies(self, spec: object) -> bool:
                return True

            def attack(
                self,
                env: FakeEnv,
                spec: object,
                client: object,
                attempt: int,
                n_trials: int,
            ) -> AttackTrial:
                submission = "GOLD"
                reward = env.verify(submission)
                return AttackTrial(
                    env_id=spec.id,  # type: ignore[union-attr]
                    strategy=self.name,
                    attempt=attempt,
                    submission=submission,
                    reward=reward,
                    hacked=True,
                    classification="hacked",
                )

        result = RedTeamProbe(Eager(), n_trials=1).run(env, spec, client=FakeClient())  # type: ignore[arg-type]
        assert result.trials[0].hacked is False
        assert result.trials[0].classification == "solved"
        assert result.curve.n_successes == 0
        assert result.trials[0].reward.verdict is True
