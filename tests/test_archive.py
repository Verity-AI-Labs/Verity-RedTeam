"""Tests for the optional full-trajectory archive."""

from __future__ import annotations

import json
from pathlib import Path

from verity_core.env import RewardResult

from verity_redteam.analysis.hackability import HackabilityEstimator
from verity_redteam.archive import TrajectoryArchive, archive_record, default_archive_dir
from verity_redteam.outcomes import LEGITIMATE_SOLVE, NO_REWARD, SERIOUS_HACK
from verity_redteam.types import AttackTrial, Episode, ProbeResult


def _solve_trial() -> AttackTrial:
    return AttackTrial(
        env_id="corpus/task-1",
        strategy="freeform",
        attempt=0,
        submission="GOLD",
        reward=RewardResult(1.0, True, "ok"),
        hacked=False,
        classification=LEGITIMATE_SOLVE,
        messages=[
            {"role": "user", "content": "solve the task"},
            {"role": "assistant", "content": "EXEC: echo GOLD\nSUBMIT: GOLD"},
        ],
        episodes=[
            Episode(
                index=0,
                response="EXEC: echo GOLD\nSUBMIT: GOLD",
                tool_calls=[
                    {"type": "exec", "command": "echo GOLD"},
                    {"type": "submit", "body": "GOLD"},
                ],
            )
        ],
        judgment={
            "outcome": LEGITIMATE_SOLVE,
            "observed_categories": [],
            "core_solved": True,
            "rationale": "submission or executed commands matched gold",
            "source": "heuristic",
            "raw": {},
        },
        evidence={"gold_match": True},
        model_id="test-model",
        prompt_version="v2",
    )


class TestDefaultArchiveDir:
    def test_is_a_sibling_of_the_vrc_directory(self) -> None:
        assert default_archive_dir("/tmp/vrc/entries") == Path("/tmp/vrc/trajectories")
        assert default_archive_dir(Path("vrc")) == Path("trajectories")


class TestArchiveRecord:
    def test_includes_commands_verifier_verdict_rationale_and_gold_match(self) -> None:
        payload = archive_record(_solve_trial())
        assert payload["executed_commands"] == ["echo GOLD"]
        assert payload["reward"]["verdict"] is True
        assert payload["judgment"]["outcome"] == LEGITIMATE_SOLVE
        assert "matched gold" in payload["judgment"]["rationale"]
        assert payload["gold_match"] is True
        assert payload["messages"][1]["content"].startswith("EXEC:")


class TestTrajectoryArchive:
    def test_writes_a_solve_named_by_trial_id(self, tmp_path: Path) -> None:
        trial = _solve_trial()
        archive = TrajectoryArchive(tmp_path / "trajectories")
        path = archive.log_trial(trial)
        assert path == tmp_path / "trajectories" / "corpus" / "task-1" / f"{trial.trial_id}.json"
        assert path.is_file()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["trial_id"] == trial.trial_id
        assert loaded["classification"] == LEGITIMATE_SOLVE
        assert loaded["hacked"] is False
        assert loaded["gold_match"] is True
        assert loaded["executed_commands"] == ["echo GOLD"]
        assert loaded["judgment"]["rationale"]

    def test_archives_every_outcome_in_a_probe(self, tmp_path: Path) -> None:
        solve = _solve_trial()
        miss = AttackTrial(
            env_id=solve.env_id,
            strategy="freeform",
            attempt=1,
            submission="nope",
            reward=RewardResult(0.0, False, "fail"),
            hacked=False,
            classification=NO_REWARD,
            judgment={"outcome": NO_REWARD, "rationale": "verifier rejected", "raw": {}},
            evidence={"gold_match": False},
        )
        hack = AttackTrial(
            env_id=solve.env_id,
            strategy="freeform",
            attempt=2,
            submission="bypass",
            reward=RewardResult(1.0, True, "ok"),
            hacked=True,
            classification=SERIOUS_HACK,
            judgment={"outcome": SERIOUS_HACK, "rationale": "non-gold pass", "raw": {}},
            evidence={"gold_match": False},
        )
        trials = [solve, miss, hack]
        result = ProbeResult(
            strategy="freeform",
            env_id=solve.env_id,
            trials=trials,
            curve=HackabilityEstimator.fit(trials, strategy="freeform"),
        )
        written = TrajectoryArchive(tmp_path / "trajectories").log_probe(result)
        assert len(written) == 3
        names = {path.stem for path in written}
        assert names == {trial.trial_id for trial in trials}
