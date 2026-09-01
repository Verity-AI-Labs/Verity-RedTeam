"""Tests for VRCLogger."""

from __future__ import annotations

from pathlib import Path

from verity_corpus.models.vrc import VRCEntry

from verity_redteam.analysis.hackability import HackabilityEstimator
from verity_redteam.types import AttackTrial, ProbeResult
from verity_redteam.vrc import VRCLogger


def _hack(submission: str, env_id: str = "env1") -> AttackTrial:
    return AttackTrial(
        env_id=env_id,
        strategy="freeform",
        attempt=0,
        submission=submission,
        hacked=True,
        classification="hacked",
        messages=[{"role": "user", "content": submission}],
        model_id="test-model",
    )


def _curve(trials: list[AttackTrial]):
    return HackabilityEstimator.fit(trials, strategy="freeform")


class TestVRCLogger:
    def test_writes_a_vrc_entry_for_each_hack(self, tmp_path: Path) -> None:
        trial = _hack("bypass")
        logger = VRCLogger(tmp_path)
        path = logger.log_trial(trial, _curve([trial]))
        assert path is not None
        assert path.is_file()
        loaded = VRCEntry.load(tmp_path, path.stem)
        assert loaded.env_id == "env1"
        assert loaded.exploit_type == "freeform"
        assert loaded.trajectory == [{"role": "user", "content": "bypass"}]
        assert loaded.model_id == "test-model"
        assert loaded.notes.startswith("trial_id=")
        assert loaded.hackability_curve is not None

    def test_skips_trials_that_are_not_hacks(self, tmp_path: Path) -> None:
        trial = _hack("nope")
        trial.hacked = False
        trial.classification = "failed"
        assert VRCLogger(tmp_path).log_trial(trial, _curve([trial])) is None
        assert list(tmp_path.rglob("*.json")) == []

    def test_in_memory_dedup_skips_the_same_submission_twice(self, tmp_path: Path) -> None:
        a = _hack("bypass")
        b = _hack("bypass")
        logger = VRCLogger(tmp_path)
        curve = _curve([a, b])
        first = logger.log_trial(a, curve)
        second = logger.log_trial(b, curve)
        assert first is not None
        assert second is None
        assert len(list(tmp_path.rglob("*.json"))) == 1

    def test_different_submissions_are_both_logged(self, tmp_path: Path) -> None:
        a = _hack("one")
        b = _hack("two")
        logger = VRCLogger(tmp_path)
        result = ProbeResult(
            strategy="freeform",
            env_id="env1",
            trials=[a, b],
            curve=_curve([a, b]),
        )
        written = logger.log_probe(result)
        assert len(written) == 2

    def test_dedup_is_per_env_id(self, tmp_path: Path) -> None:
        a = _hack("bypass", env_id="env-a")
        b = _hack("bypass", env_id="env-b")
        logger = VRCLogger(tmp_path)
        assert logger.log_trial(a, _curve([a])) is not None
        assert logger.log_trial(b, _curve([b])) is not None
        assert len(list(tmp_path.rglob("*.json"))) == 2
