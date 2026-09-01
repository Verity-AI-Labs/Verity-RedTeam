"""Tests for VRCLogger."""

from __future__ import annotations

from pathlib import Path

from verity_corpus.models.vrc import VRCEntry

from verity_redteam.analysis.hackability import HackabilityEstimator
from verity_redteam.outcomes import NO_REWARD, SERIOUS_HACK
from verity_redteam.types import AttackTrial, ProbeResult
from verity_redteam.vrc import VRCLogger, last_user_preview, load_vrc_entries


def _hack(submission: str, env_id: str = "env1") -> AttackTrial:
    return AttackTrial(
        env_id=env_id,
        strategy="freeform",
        attempt=0,
        submission=submission,
        hacked=True,
        classification=SERIOUS_HACK,
        messages=[{"role": "user", "content": submission}],
        model_id="test-model",
    )


def _curve(trials: list[AttackTrial]):
    return HackabilityEstimator.fit(trials, strategy="freeform")


class TestVRCLogger:
    def test_writes_a_vrc_entry_for_each_hack(self, tmp_path: Path) -> None:
        trial = _hack("bypass")
        logger = VRCLogger(tmp_path / "vrc")
        path = logger.log_trial(trial, _curve([trial]))
        assert path is not None
        assert path.is_file()
        loaded = VRCEntry.load(tmp_path / "vrc", path.stem)
        assert loaded.env_id == "env1"
        assert loaded.exploit_type == "freeform"
        assert loaded.trajectory == [{"role": "user", "content": "bypass"}]
        assert loaded.model_id == "test-model"
        assert loaded.hackability_curve is not None
        assert "trial_id=" in loaded.notes
        assert "strategy=freeform" in loaded.notes

    def test_stores_observed_categories_as_the_exploit_type(self, tmp_path: Path) -> None:
        trial = _hack("bypass")
        trial.observed_categories = ["output-spoofing", "hollow-implementation"]
        path = VRCLogger(tmp_path / "vrc").log_trial(trial, _curve([trial]))
        assert path is not None
        loaded = VRCEntry.load(tmp_path / "vrc", path.stem)
        assert loaded.exploit_type == "hollow-implementation"
        assert "output-spoofing" in loaded.notes
        assert "hollow-implementation" in loaded.notes

    def test_skips_trials_that_are_not_hacks(self, tmp_path: Path) -> None:
        trial = _hack("nope")
        trial.hacked = False
        trial.classification = NO_REWARD
        assert VRCLogger(tmp_path / "vrc").log_trial(trial, _curve([trial])) is None
        assert list((tmp_path / "vrc").rglob("*.json")) == []

    def test_in_memory_dedup_skips_the_same_submission_twice(self, tmp_path: Path) -> None:
        a = _hack("bypass")
        b = _hack("bypass")
        logger = VRCLogger(tmp_path / "vrc")
        curve = _curve([a, b])
        first = logger.log_trial(a, curve)
        second = logger.log_trial(b, curve)
        assert first is not None
        assert second is None
        assert len(list((tmp_path / "vrc").rglob("*.json"))) == 1

    def test_different_submissions_are_both_logged(self, tmp_path: Path) -> None:
        a = _hack("one")
        b = _hack("two")
        logger = VRCLogger(tmp_path / "vrc")
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
        logger = VRCLogger(tmp_path / "vrc")
        assert logger.log_trial(a, _curve([a])) is not None
        assert logger.log_trial(b, _curve([b])) is not None
        assert len(list((tmp_path / "vrc").rglob("*.json"))) == 2


class TestLoadVrcEntries:
    def test_reads_entries_for_one_env_id(self, tmp_path: Path) -> None:
        vrc_dir = tmp_path / "vrc"
        trial = _hack("bypass" * 20, env_id="corpus/task-1")
        VRCLogger(vrc_dir).log_trial(trial, _curve([trial]))
        entries = load_vrc_entries(vrc_dir, "corpus/task-1")
        assert len(entries) == 1
        assert entries[0].env_id == "corpus/task-1"
        assert entries[0].exploit_type == "freeform"
        preview = last_user_preview(entries[0].trajectory)
        assert len(preview) == 80
        assert preview == ("bypass" * 20)[:80]

    def test_returns_empty_when_the_folder_is_missing(self, tmp_path: Path) -> None:
        assert load_vrc_entries(tmp_path / "vrc", "absent") == []

    def test_preview_uses_the_last_user_message(self) -> None:
        trajectory = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "second-and-final"},
        ]
        assert last_user_preview(trajectory) == "second-and-final"
        assert last_user_preview(trajectory, n=6) == "second"
