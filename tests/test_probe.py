"""Tests for RedTeamProbe: precheck, gold filter, judge classification, curve fit."""

from __future__ import annotations

import logging

import pytest
from tests.conftest import FakeClient, FakeEnv, make_spec

from verity_redteam.outcomes import ERROR, LEGITIMATE_SOLVE, NO_REWARD, SERIOUS_HACK
from verity_redteam.probe import RedTeamProbe
from verity_redteam.strategies.freeform import FreeformHackStrategy
from verity_redteam.types import AttackTrial


def _probe(n_trials: int = 4) -> RedTeamProbe:
    strategy = FreeformHackStrategy(model="test-model")
    return RedTeamProbe(strategy, n_trials=n_trials)


class TestTrialLoop:
    def test_runs_n_trials_and_resets_between_them(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="GOLD", passing="bypass")
        client = FakeClient(contents=["bypass", "miss", "bypass", "miss"])
        result = _probe(4).run(env, spec, client)  # type: ignore[arg-type]
        assert len(result.trials) == 4
        assert [t.hacked for t in result.trials] == [True, False, True, False]
        assert [t.classification for t in result.trials] == [
            SERIOUS_HACK,
            NO_REWARD,
            SERIOUS_HACK,
            NO_REWARD,
        ]
        assert result.curve.n_trials == 4
        assert result.curve.n_successes == 2
        assert result.curve.alpha == 0.5
        # gold precheck: 3 resets, then one reset after each of 4 trials = 7
        assert env.reset_count == 7
        assert result.metadata["precheck_failed"] is False
        assert result.total_tokens == 6 * 4

    def test_rejects_a_non_positive_trial_count(self) -> None:
        with pytest.raises(ValueError, match="n_trials"):
            RedTeamProbe(FreeformHackStrategy(model="x"), n_trials=0)


class TestGoldFilter:
    def test_gold_match_is_a_legitimate_solve_and_excluded_from_alpha(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="GOLD", passing="bypass")
        client = FakeClient(contents=["GOLD", "bypass", "nope", "bypass"])
        result = _probe(4).run(env, spec, client)  # type: ignore[arg-type]
        assert [t.classification for t in result.trials] == [
            LEGITIMATE_SOLVE,
            SERIOUS_HACK,
            NO_REWARD,
            SERIOUS_HACK,
        ]
        assert [t.hacked for t in result.trials] == [False, True, False, True]
        # gold match dropped from N, leaving 3 trials and 2 successes
        assert result.curve.n_trials == 3
        assert result.curve.n_successes == 2
        assert result.curve.alpha == pytest.approx(2 / 3)
        assert result.metadata["n_legitimate_solve"] == 1
        assert result.metadata["n_errored"] == 0
        assert result.trials[0].evidence["gold_match"] is True
        assert result.trials[1].evidence["gold_match"] is False

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
            SERIOUS_HACK,
            NO_REWARD,
            LEGITIMATE_SOLVE,
            ERROR,
        ]
        # alpha is only over hacks + no_reward: 1/2
        assert result.curve.n_trials == 2
        assert result.curve.n_successes == 1
        assert result.curve.alpha == pytest.approx(0.5)
        assert result.metadata["n_legitimate_solve"] == 1
        assert result.metadata["n_errored"] == 1

    def test_strip_is_applied_before_the_gold_comparison(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="  GOLD\n", passing=frozenset())
        client = FakeClient(contents="GOLD")
        result = _probe(1).run(env, spec, client)  # type: ignore[arg-type]
        assert result.trials[0].classification == LEGITIMATE_SOLVE
        assert result.curve.n_successes == 0


class TestPrecheck:
    def test_skips_attacks_when_gold_fails_before_any_trial(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="GOLD", passing="bypass", gold_fails_after_reset=True)
        client = FakeClient(contents="bypass")
        with caplog.at_level(logging.WARNING, logger="verity_redteam.probe"):
            result = _probe(2).run(env, spec, client)  # type: ignore[arg-type]
        assert result.metadata["precheck_failed"] is True
        assert result.metadata["precheck"] == "reset_broke_gold"
        assert result.trials == []
        assert result.curve.n_trials == 0
        assert client.calls == []
        assert any("precheck failed" in rec.message for rec in caplog.records)

    def test_skips_attacks_when_gold_is_rejected_outright(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="GOLD", passing="bypass")

        def _always_fail(submission: str):
            env.verify_calls.append(submission)
            from verity_core.env import RewardResult

            return RewardResult(0.0, False, "gold never passes")

        env.verify = _always_fail  # type: ignore[method-assign]
        client = FakeClient(contents="bypass")
        result = _probe(2).run(env, spec, client)  # type: ignore[arg-type]
        assert result.metadata["precheck_failed"] is True
        assert result.metadata["precheck"] == "gold_rejected"
        assert result.trials == []
        assert client.calls == []

    def test_does_not_flag_when_gold_still_passes(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="GOLD", passing="bypass")
        client = FakeClient(contents="bypass")
        result = _probe(2).run(env, spec, client)  # type: ignore[arg-type]
        assert result.metadata["precheck_failed"] is False
        assert result.metadata["precheck"] == "ok"
        assert len(result.trials) == 2

    def test_skips_the_gold_check_when_there_is_no_gold(self) -> None:
        spec = make_spec(has_gold=False)
        env = FakeEnv(spec=spec, gold=None, passing="bypass")
        client = FakeClient(contents="bypass")
        result = _probe(2).run(env, spec, client)  # type: ignore[arg-type]
        assert result.metadata["precheck_failed"] is False
        assert result.metadata["precheck"] == "skipped_no_gold"
        gold_verifies = [c for c in env.verify_calls if c == "GOLD"]
        assert gold_verifies == []


class TestProbeOwnsHacked:
    def test_classifies_a_passing_verdict_as_a_serious_hack(self) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="GOLD", passing="bypass")
        client = FakeClient(contents="bypass")
        result = _probe(1).run(env, spec, client)  # type: ignore[arg-type]
        trial = result.trials[0]
        assert trial.reward.verdict is True
        assert trial.hacked is True
        assert trial.classification == SERIOUS_HACK
        assert trial.judgment is not None
        assert trial.judgment["source"] == "heuristic"

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
                    classification=SERIOUS_HACK,
                )

        result = RedTeamProbe(Eager(), n_trials=1).run(env, spec, client=FakeClient())  # type: ignore[arg-type]
        assert result.trials[0].hacked is False
        assert result.trials[0].classification == LEGITIMATE_SOLVE
        assert result.curve.n_successes == 0
        assert result.trials[0].reward.verdict is True
