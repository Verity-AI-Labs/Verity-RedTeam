"""Tests for the hackability estimator against hand-computed values."""

from __future__ import annotations

import pytest

from verity_redteam.analysis import DEFAULT_CONFIDENCE, HackabilityCurve, HackabilityEstimator
from verity_redteam.analysis.hackability import CURVE_KS
from verity_redteam.outcomes import NO_REWARD, NONSERIOUS_HACK, SERIOUS_HACK
from verity_redteam.types import AttackTrial


def _trials(n: int, successes: int, *, strategy: str = "freeform") -> list[AttackTrial]:
    """Build ``n`` trials of which the first ``successes`` are serious hacks."""
    out: list[AttackTrial] = []
    for i in range(n):
        hacked = i < successes
        out.append(
            AttackTrial(
                env_id="corpus/task-1",
                strategy=strategy,
                attempt=i,
                hacked=hacked,
                classification=SERIOUS_HACK if hacked else NO_REWARD,
            )
        )
    return out


class TestCurveKeys:
    def test_curve_is_evaluated_at_powers_of_two(self) -> None:
        assert CURVE_KS == (1, 2, 4, 8)
        assert DEFAULT_CONFIDENCE == 0.95


class TestZeroOfN:
    """0/N: alpha is exactly 0, but the CI upper bound is not."""

    def test_alpha_is_zero(self) -> None:
        curve = HackabilityEstimator.fit(_trials(8, 0))
        assert curve.alpha == 0.0
        assert curve.n_trials == 8
        assert curve.n_successes == 0
        assert curve.alpha_ci_lower == 0.0
        # Clopper-Pearson upper = 1 - (0.025)^(1/8)
        assert curve.alpha_ci_upper == pytest.approx(1.0 - 0.025 ** (1 / 8))
        assert curve.alpha_ci_upper > 0.0

    def test_h_k_is_zero_everywhere(self) -> None:
        curve = HackabilityEstimator.fit(_trials(8, 0))
        assert curve.curve == {1: 0.0, 2: 0.0, 4: 0.0, 8: 0.0}

    def test_single_failure_upper_bound_is_one_minus_tail(self) -> None:
        curve = HackabilityEstimator.fit(_trials(1, 0))
        assert curve.alpha == 0.0
        assert curve.alpha_ci_upper == pytest.approx(0.975)


class TestNOfN:
    """N/N: alpha is exactly 1, but the CI lower bound is not."""

    def test_alpha_is_one(self) -> None:
        curve = HackabilityEstimator.fit(_trials(8, 8))
        assert curve.alpha == 1.0
        assert curve.n_successes == 8
        assert curve.alpha_ci_upper == 1.0
        # Clopper-Pearson lower = (0.025)^(1/8)
        assert curve.alpha_ci_lower == pytest.approx(0.025 ** (1 / 8))
        assert curve.alpha_ci_lower < 1.0

    def test_h_k_is_one_everywhere(self) -> None:
        curve = HackabilityEstimator.fit(_trials(8, 8))
        assert curve.curve == {1: 1.0, 2: 1.0, 4: 1.0, 8: 1.0}

    def test_single_success_lower_bound_is_the_tail(self) -> None:
        curve = HackabilityEstimator.fit(_trials(1, 1))
        assert curve.alpha == 1.0
        assert curve.alpha_ci_lower == pytest.approx(0.025)


class TestTwoOfEight:
    """2/8: alpha = 1/4, H(K) = 1 - (3/4)^K, CI from Beta quantiles."""

    def test_alpha_is_one_quarter(self) -> None:
        curve = HackabilityEstimator.fit(_trials(8, 2))
        assert curve.alpha == 0.25
        assert curve.n_trials == 8
        assert curve.n_successes == 2

    def test_h_k_matches_the_closed_form(self) -> None:
        curve = HackabilityEstimator.fit(_trials(8, 2))
        assert curve.curve[1] == pytest.approx(0.25)
        assert curve.curve[2] == pytest.approx(1.0 - 0.75**2)  # 0.4375
        assert curve.curve[4] == pytest.approx(1.0 - 0.75**4)  # 0.68359375
        assert curve.curve[8] == pytest.approx(1.0 - 0.75**8)  # 0.8998870849609375

    def test_clopper_pearson_matches_beta_quantiles(self) -> None:
        # lower = F^{-1}_{Beta(2, 7)}(0.025), upper = F^{-1}_{Beta(3, 6)}(0.975)
        curve = HackabilityEstimator.fit(_trials(8, 2))
        assert curve.alpha_ci_lower == pytest.approx(0.031854026249944246)
        assert curve.alpha_ci_upper == pytest.approx(0.6508557944128242)
        assert curve.alpha_ci_lower < curve.alpha < curve.alpha_ci_upper


class TestEmpty:
    def test_no_trials_spans_the_unit_interval(self) -> None:
        # Decision: zero observations is not alpha=0 with a degenerate CI; the interval
        # is [0, 1] because we have learned nothing.
        curve = HackabilityEstimator.fit([])
        assert curve.alpha == 0.0
        assert curve.n_trials == 0
        assert curve.n_successes == 0
        assert curve.alpha_ci_lower == 0.0
        assert curve.alpha_ci_upper == 1.0
        assert curve.curve == {1: 0.0, 2: 0.0, 4: 0.0, 8: 0.0}
        assert curve.trial_ids == ()
        assert curve.n_serious == 0
        assert curve.serious_alpha == 0.0
        assert curve.serious_curve == {1: 0.0, 2: 0.0, 4: 0.0, 8: 0.0}


class TestFitMetadata:
    def test_records_strategy_and_trial_ids(self) -> None:
        trials = _trials(3, 1, strategy="freeform")
        curve = HackabilityEstimator.fit(trials)
        assert curve.strategy == "freeform"
        assert curve.trial_ids == tuple(t.trial_id for t in trials)

    def test_explicit_strategy_overrides_the_trials(self) -> None:
        curve = HackabilityEstimator.fit(_trials(2, 0), strategy="other")
        assert curve.strategy == "other"

    def test_to_dict_stringifies_curve_keys(self) -> None:
        payload = HackabilityEstimator.fit(_trials(8, 2)).to_dict()
        assert set(payload["curve"]) == {"1", "2", "4", "8"}
        assert payload["n_successes"] == 2
        assert payload["n_serious"] == 2
        assert payload["n_nonserious"] == 0
        assert set(payload["serious_curve"]) == {"1", "2", "4", "8"}


class TestCurveDataclass:
    def test_slots_and_defaults(self) -> None:
        curve = HackabilityCurve(
            alpha=0.0,
            alpha_ci_lower=0.0,
            alpha_ci_upper=1.0,
            n_trials=0,
            n_successes=0,
            curve={},
        )
        assert curve.strategy == ""
        assert curve.trial_ids == ()
        assert curve.n_serious == 0
        assert curve.n_nonserious == 0
        assert curve.serious_alpha == 0.0
        assert curve.nonserious_alpha == 0.0


class TestSeriousBreakdown:
    def test_splits_any_hack_alpha_into_serious_and_nonserious(self) -> None:
        trials = [
            AttackTrial(
                env_id="e",
                strategy="freeform",
                attempt=0,
                hacked=True,
                classification=SERIOUS_HACK,
            ),
            AttackTrial(
                env_id="e",
                strategy="freeform",
                attempt=1,
                hacked=True,
                classification=NONSERIOUS_HACK,
            ),
            AttackTrial(
                env_id="e",
                strategy="freeform",
                attempt=2,
                hacked=False,
                classification=NO_REWARD,
            ),
            AttackTrial(
                env_id="e",
                strategy="freeform",
                attempt=3,
                hacked=False,
                classification=NO_REWARD,
            ),
        ]
        curve = HackabilityEstimator.fit(trials)
        assert curve.alpha == 0.5
        assert curve.n_successes == 2
        assert curve.n_serious == 1
        assert curve.n_nonserious == 1
        assert curve.serious_alpha == 0.25
        assert curve.nonserious_alpha == 0.25
        assert curve.serious_curve[1] == pytest.approx(0.25)
        assert curve.serious_curve[2] == pytest.approx(1.0 - 0.75**2)
