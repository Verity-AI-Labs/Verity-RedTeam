"""Hackability analysis: per-trial rate, exact CI, and H(K) curve."""

from verity_redteam.analysis.hackability import (
    CURVE_KS,
    DEFAULT_CONFIDENCE,
    HackabilityCurve,
    HackabilityEstimator,
    clopper_pearson,
)

__all__ = [
    "CURVE_KS",
    "DEFAULT_CONFIDENCE",
    "HackabilityCurve",
    "HackabilityEstimator",
    "clopper_pearson",
]
