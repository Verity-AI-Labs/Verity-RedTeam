"""Hackability curve: how likely a verifier is to accept a non-solution.

alpha is the per-trial success rate. The curve H(K) = 1 - (1-alpha)^K is the
probability of at least one success in K independent attempts. Confidence
intervals use the Clopper-Pearson exact binomial method (via the beta quantile
function), not Wilson or Wald: when we report hackability to a customer, the
interval should not be optimistic at the edges.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from scipy.stats import beta

from verity_redteam.outcomes import NONSERIOUS_HACK, SERIOUS_HACK
from verity_redteam.types import AttackTrial

DEFAULT_CONFIDENCE = 0.95
CURVE_KS: tuple[int, ...] = (1, 2, 4, 8)

__all__ = [
    "CURVE_KS",
    "DEFAULT_CONFIDENCE",
    "HackabilityCurve",
    "HackabilityEstimator",
    "clopper_pearson",
]


@dataclass(slots=True)
class HackabilityCurve:
    """Fitted per-trial rate, exact CI, and analytic H(K) values.

    ``alpha`` is the any-hack rate (serious + nonserious). ``serious_alpha``
    and ``nonserious_alpha`` break that rate down so a scorecard can report
    both the strict exploit rate and the looser any-hack rate.
    """

    alpha: float
    alpha_ci_lower: float
    alpha_ci_upper: float
    n_trials: int
    n_successes: int
    curve: dict[int, float]
    strategy: str = ""
    trial_ids: tuple[str, ...] = field(default_factory=tuple)
    n_serious: int = 0
    n_nonserious: int = 0
    serious_alpha: float = 0.0
    nonserious_alpha: float = 0.0
    serious_curve: dict[int, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha": self.alpha,
            "alpha_ci_lower": self.alpha_ci_lower,
            "alpha_ci_upper": self.alpha_ci_upper,
            "n_trials": self.n_trials,
            "n_successes": self.n_successes,
            "curve": {str(k): v for k, v in self.curve.items()},
            "strategy": self.strategy,
            "trial_ids": list(self.trial_ids),
            "n_serious": self.n_serious,
            "n_nonserious": self.n_nonserious,
            "serious_alpha": self.serious_alpha,
            "nonserious_alpha": self.nonserious_alpha,
            "serious_curve": {str(k): v for k, v in self.serious_curve.items()},
        }


def clopper_pearson(k: int, n: int, confidence: float = DEFAULT_CONFIDENCE) -> tuple[float, float]:
    """Exact binomial CI. 0/n has a nonzero upper bound; n/n has a nonzero lower bound."""
    return _clopper_pearson(k, n, confidence)


def _clopper_pearson(k: int, n: int, confidence: float) -> tuple[float, float]:
    """Exact binomial CI. 0/n has a nonzero upper bound; n/n has a nonzero lower bound."""
    if n <= 0:
        # No observations: the interval is the whole [0, 1] line, not a point at zero.
        return 0.0, 1.0
    tail = 1.0 - confidence
    lower = 0.0 if k == 0 else float(beta.ppf(tail / 2.0, k, n - k + 1))
    upper = 1.0 if k == n else float(beta.ppf(1.0 - tail / 2.0, k + 1, n - k))
    return lower, upper


def _h(alpha: float, k: int) -> float:
    """H(K) = 1 - (1-alpha)^K, chance of at least one success in K trials."""
    if k <= 0:
        return 0.0
    return 1.0 - (1.0 - alpha) ** k


class HackabilityEstimator:
    """Fit a :class:`HackabilityCurve` from a sequence of trials."""

    @staticmethod
    def fit(
        trials: Sequence[AttackTrial],
        *,
        confidence: float = DEFAULT_CONFIDENCE,
        strategy: str = "",
    ) -> HackabilityCurve:
        n = len(trials)
        successes = [trial for trial in trials if trial.hacked]
        k = len(successes)
        n_serious = sum(1 for trial in trials if trial.classification == SERIOUS_HACK)
        n_nonserious = sum(1 for trial in trials if trial.classification == NONSERIOUS_HACK)
        alpha = 0.0 if n == 0 else k / n
        serious_alpha = 0.0 if n == 0 else n_serious / n
        nonserious_alpha = 0.0 if n == 0 else n_nonserious / n
        lower, upper = _clopper_pearson(k, n, confidence)
        name = strategy or (trials[0].strategy if trials else "")
        return HackabilityCurve(
            alpha=alpha,
            alpha_ci_lower=lower,
            alpha_ci_upper=upper,
            n_trials=n,
            n_successes=k,
            curve={k_val: _h(alpha, k_val) for k_val in CURVE_KS},
            strategy=name,
            trial_ids=tuple(trial.trial_id for trial in trials),
            n_serious=n_serious,
            n_nonserious=n_nonserious,
            serious_alpha=serious_alpha,
            nonserious_alpha=nonserious_alpha,
            serious_curve={k_val: _h(serious_alpha, k_val) for k_val in CURVE_KS},
        )
