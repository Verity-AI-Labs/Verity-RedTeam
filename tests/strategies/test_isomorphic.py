"""Tests for the V2 isomorphic perturbation strategy."""

from __future__ import annotations

import json

from tests.conftest import FakeClient, FakeEnv, make_spec
from verity_core.env import RewardResult

from verity_redteam.judge import Judgment
from verity_redteam.outcomes import ERROR, NO_REWARD
from verity_redteam.perturbation import Perturbation
from verity_redteam.probe import RedTeamProbe
from verity_redteam.strategies.isomorphic import IsomorphicPerturbationStrategy


def _rejecting_verifier(perturbation: Perturbation, gold: str) -> RewardResult:
    return RewardResult(0.0, False, "variant rejected gold")


def _accepting_verifier(perturbation: Perturbation, gold: str) -> RewardResult:
    return RewardResult(1.0, True, "variant accepted gold")


class TestApplies:
    def test_only_applies_when_the_spec_has_gold(self) -> None:
        strategy = IsomorphicPerturbationStrategy(model="m", variant_verifier=_rejecting_verifier)
        assert strategy.applies(make_spec(has_gold=True)) is True
        assert strategy.applies(make_spec(has_gold=False)) is False
        assert IsomorphicPerturbationStrategy(model="m").applies(make_spec(has_gold=True)) is False
        assert strategy.target_axes == ("V2",)


class TestAttack:
    def test_a_rejecting_variant_counts_as_a_v2_finding(self) -> None:
        spec = make_spec(has_gold=True)
        env = FakeEnv(spec=spec, gold="GOLD")
        client = FakeClient(
            contents=json.dumps({"description": "rename", "transformed_gold": "GOLD_PRIME"})
        )
        strategy = IsomorphicPerturbationStrategy(model="m", variant_verifier=_rejecting_verifier)
        result = RedTeamProbe(strategy, n_trials=1).run(env, spec, client)  # type: ignore[arg-type]
        trial = result.trials[0]
        assert trial.evidence["variant_rejected"] is True
        assert trial.hacked is True
        assert result.curve.alpha == 1.0

    def test_an_accepting_variant_is_not_a_finding(self) -> None:
        spec = make_spec(has_gold=True)
        env = FakeEnv(spec=spec, gold="GOLD")
        client = FakeClient(
            contents=json.dumps({"description": "rename", "transformed_gold": "GOLD_PRIME"})
        )
        strategy = IsomorphicPerturbationStrategy(model="m", variant_verifier=_accepting_verifier)
        trial = strategy.attack(env, spec, client, 0, 1)  # type: ignore[arg-type]
        assert trial.evidence["variant_rejected"] is False
        assert strategy.counts_as_hack(trial, Judgment(outcome=NO_REWARD)) is False

    def test_missing_variant_verifier_is_an_error(self) -> None:
        spec = make_spec(has_gold=True)
        env = FakeEnv(spec=spec, gold="GOLD")
        client = FakeClient(contents=json.dumps({"description": "x", "transformed_gold": "y"}))
        trial = IsomorphicPerturbationStrategy(model="m").attack(
            env,
            spec,
            client,
            0,
            1,  # type: ignore[arg-type]
        )
        assert trial.classification == ERROR
        assert "variant verifier" in (trial.error or "")
