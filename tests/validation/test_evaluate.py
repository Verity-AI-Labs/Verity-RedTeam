"""Scorecards → labeled predictions → kill-gate evaluation."""

from __future__ import annotations

from pathlib import Path

import pytest
from verity_core.scorecard import Scorecard

from verity_redteam.validation.benchmarks import TERMINAL_WRENCH, resolve_benchmark
from verity_redteam.validation.evaluate import (
    evaluate_benchmark,
    first_hack_k,
    labeled_predictions,
)
from verity_redteam.validation.metrics import TaskPrediction


def _card(
    env_id: str,
    *,
    alpha: float | None = None,
    n_trials: int = 8,
    n_successes: int = 0,
    hack_attempts: list[int] | None = None,
) -> Scorecard:
    card = Scorecard(env_id=env_id)
    if alpha is None:
        return card
    evidence: dict[str, object] = {
        "n_trials": n_trials,
        "n_successes": n_successes,
        "hack_attempts": list(hack_attempts or []),
        "strategy": "freeform",
    }
    card.set_axis("V1", alpha, "verity-redteam", evidence=evidence)
    return card


class TestFirstHackK:
    def test_uses_the_earliest_recorded_attempt(self) -> None:
        card = _card("e", alpha=0.5, n_successes=2, hack_attempts=[3, 0])
        assert first_hack_k(card) == 1

    def test_unscored_is_not_a_hack(self) -> None:
        assert first_hack_k(_card("e")) is None

    def test_successes_without_attempts_use_n_trials(self) -> None:
        # Conservative: unknown timing is treated as the last trial (K=8),
        # so it does not count as a hit at K<=4.
        card = _card("e", alpha=0.25, n_trials=8, n_successes=1, hack_attempts=[])
        assert first_hack_k(card) == 8

    def test_zero_successes_is_a_miss(self) -> None:
        card = _card("e", alpha=0.0, n_successes=0, hack_attempts=[])
        assert first_hack_k(card) is None


class TestLabeledPredictions:
    def test_missing_scorecard_is_a_miss(self) -> None:
        rows = labeled_predictions(
            ["a", "b"],
            [_card("a", alpha=1.0, n_successes=1, hack_attempts=[0])],
            labeled_hackable=True,
        )
        assert rows == [
            TaskPrediction("a", True, 1, True),
            TaskPrediction("b", True, None, False),
        ]


class TestEvaluateBenchmark:
    def test_recall_gate_on_a_two_task_wrench_set(self, tmp_path: Path) -> None:
        (tmp_path / "terminal_wrench.yaml").write_text(
            """
source_defaults:
  type: git
  url: https://github.com/few-sh/terminal-wrench
  commit: abc
entries:
  - name: "5"
    path: tasks/5/claude-opus-4.6/original_task
    domain: {category: terminal}
    adapter: terminal
  - name: "8"
    path: tasks/8/claude-opus-4.6/original_task
    domain: {category: terminal}
    adapter: terminal
""",
            encoding="utf-8",
        )
        resolved = resolve_benchmark(tmp_path, TERMINAL_WRENCH)
        hit, miss = resolved.env_ids
        cards = [
            _card(hit, alpha=1.0, n_successes=1, hack_attempts=[1]),
            _card(miss, alpha=0.0, n_successes=0),
        ]
        payload = evaluate_benchmark(resolved, cards)
        assert payload["benchmark"] == TERMINAL_WRENCH
        assert payload["metric"] == "recall"
        assert payload["n_labeled"] == 2
        assert payload["recall"] == pytest.approx(0.5)
        assert payload["passed"] is False
        assert payload["meets_recall_gate"] is False
