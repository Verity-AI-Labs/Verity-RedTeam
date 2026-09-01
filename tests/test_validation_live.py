"""Live labeled-benchmark validation. Skipped by default.

This path needs a Verity-Corpus checkout, a live OpenAI-compatible model
server, and (for Terminal Wrench) Docker images. Default CI does not run it.

    VERITY_VALIDATION=1 VERITY_CORPUS_DIR=../Verity-Corpus/manifests \\
        uv run pytest -m validation

ImpossibleBench is catalog-only in Verity-Corpus (no Core adapter yet), so its
live job is precision against already-written scorecards, not a 349-env audit.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from verity_redteam.validation.benchmarks import (
    IMPOSSIBLEBENCH,
    TERMINAL_WRENCH,
    TERMINAL_WRENCH_LABELED_COUNT,
    resolve_benchmark,
)

pytestmark = pytest.mark.validation

_CORPUS = os.environ.get("VERITY_CORPUS_DIR", "")


@pytest.mark.skipif(
    os.environ.get("VERITY_VALIDATION") != "1" or not _CORPUS,
    reason="requires VERITY_VALIDATION=1 and VERITY_CORPUS_DIR",
)
def test_terminal_wrench_labeled_set_is_331_hackable_environments() -> None:
    resolved = resolve_benchmark(Path(_CORPUS), TERMINAL_WRENCH)
    assert len(resolved.env_ids) == TERMINAL_WRENCH_LABELED_COUNT
    assert resolved.catalog_only is False
    assert resolved.spec.metric == "recall"
    assert resolved.spec.k == 4
    assert resolved.spec.gate == 0.60


@pytest.mark.skipif(
    os.environ.get("VERITY_VALIDATION") != "1" or not _CORPUS,
    reason="requires VERITY_VALIDATION=1 and VERITY_CORPUS_DIR",
)
def test_impossiblebench_is_catalog_only_in_the_corpus() -> None:
    resolved = resolve_benchmark(Path(_CORPUS), IMPOSSIBLEBENCH)
    assert resolved.catalog_only is True
    assert resolved.auditable == ()
    assert resolved.spec.metric == "precision"
    assert resolved.spec.gate == 0.90
