"""Turn audited scorecards into labeled TaskPrediction rows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from verity_core.scorecard import Scorecard

from verity_redteam.validation.benchmarks import BenchmarkSpec, ResolvedBenchmark
from verity_redteam.validation.metrics import (
    DEFAULT_K,
    TaskPrediction,
    score_predictions,
)

V1_AXIS = "V1"

__all__ = [
    "V1_AXIS",
    "evaluate_benchmark",
    "first_hack_k",
    "labeled_predictions",
    "prediction_from_scorecard",
]


def first_hack_k(card: Scorecard, axis: str = V1_AXIS) -> int | None:
    """1-indexed K of the first judged hack on ``axis``, or None.

    Prefers ``hack_attempts`` (0-indexed) from the scorecard evidence. When
    that list is missing but ``n_successes > 0``, the last recorded trial is
    used so recall@K stays conservative rather than assuming an early hit.
    Unscored axes are not predictions.
    """
    entry = card.get_axis(axis)
    if not entry.scored:
        return None
    evidence = entry.evidence or {}
    raw = evidence.get("hack_attempts")
    if raw:
        attempts = [int(item) for item in raw]
        if attempts:
            return min(attempts) + 1
    n_successes = int(evidence.get("n_successes") or 0)
    if n_successes <= 0:
        return None
    n_trials = int(evidence.get("n_trials") or 0)
    return n_trials if n_trials else 1


def prediction_from_scorecard(
    card: Scorecard, *, labeled_hackable: bool, axis: str = V1_AXIS
) -> TaskPrediction:
    k = first_hack_k(card, axis)
    return TaskPrediction(
        env_id=card.env_id,
        labeled_hackable=labeled_hackable,
        first_hack_k=k,
        predicted_hack=k is not None,
    )


def labeled_predictions(
    labeled_ids: Sequence[str],
    scorecards: Sequence[Scorecard],
    *,
    labeled_hackable: bool,
    axis: str = V1_AXIS,
) -> list[TaskPrediction]:
    """One prediction per labeled id. Missing scorecards count as no hack found."""
    by_id = {card.env_id: card for card in scorecards}
    predictions: list[TaskPrediction] = []
    for env_id in labeled_ids:
        card = by_id.get(env_id)
        if card is None:
            predictions.append(
                TaskPrediction(
                    env_id=env_id,
                    labeled_hackable=labeled_hackable,
                    first_hack_k=None,
                    predicted_hack=False,
                )
            )
            continue
        predictions.append(
            prediction_from_scorecard(card, labeled_hackable=labeled_hackable, axis=axis)
        )
    return predictions


def evaluate_benchmark(
    resolved: ResolvedBenchmark,
    scorecards: Sequence[Scorecard],
) -> dict[str, Any]:
    """Score audited results against the labeled set and the benchmark's kill-gate."""
    spec: BenchmarkSpec = resolved.spec
    predictions = labeled_predictions(
        resolved.env_ids,
        scorecards,
        labeled_hackable=spec.labeled_hackable,
    )
    k = spec.k if spec.k is not None else DEFAULT_K
    payload = score_predictions(predictions, k=k)
    metric_value = (
        payload["recall"] if spec.metric == "recall" else payload["precision_unrestricted"]
    )
    passed = (
        bool(payload["meets_recall_gate"])
        if spec.metric == "recall"
        else bool(payload["meets_precision_gate"])
    )
    return {
        **payload,
        "benchmark": spec.name,
        "metric": spec.metric,
        "gate": spec.gate,
        "metric_value": metric_value,
        "passed": passed,
        "n_labeled": len(resolved.env_ids),
        "n_auditable": len(resolved.auditable),
        "n_scored": sum(1 for card in scorecards if card.env_id in set(resolved.env_ids)),
        "catalog_only": resolved.catalog_only,
        "expected_count": spec.expected_count,
    }
