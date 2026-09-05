"""Persist full trial trajectories for judge auditability.

VRC entries remain hacks-only. When archiving is enabled, every trial —
including legitimate solves — is written under
``{archive_dir}/{env_id}/{trial_id}.json`` so scorecard ``trial_ids`` resolve
to files an operator can inspect.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from verity_redteam.gold import trial_commands
from verity_redteam.types import AttackTrial, ProbeResult

logger = logging.getLogger(__name__)

ARCHIVE_DIR_NAME = "trajectories"

__all__ = [
    "ARCHIVE_DIR_NAME",
    "TrajectoryArchive",
    "archive_record",
    "default_archive_dir",
]


def default_archive_dir(vrc_dir: Path | str) -> Path:
    """Return ``{vrc_dir}/../trajectories``."""
    return Path(vrc_dir).parent / ARCHIVE_DIR_NAME


def archive_record(trial: AttackTrial) -> dict[str, Any]:
    """Serialize a trial for the on-disk audit archive."""
    payload = trial.to_dict()
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    payload["executed_commands"] = trial_commands(trial)
    payload["gold_match"] = bool(evidence.get("gold_match", False))
    return payload


class TrajectoryArchive:
    """Write one JSON file per trial, named by ``trial_id``."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path_for(self, env_id: str, trial_id: str) -> Path:
        return self.root / env_id / f"{trial_id}.json"

    def log_trial(self, trial: AttackTrial) -> Path:
        path = self.path_for(trial.env_id, trial.trial_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = archive_record(trial)
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        logger.debug(
            "trajectory archived env_id=%s trial_id=%s classification=%s path=%s",
            trial.env_id,
            trial.trial_id,
            trial.classification,
            path,
        )
        return path

    def log_probe(self, result: ProbeResult) -> list[Path]:
        return [self.log_trial(trial) for trial in result.trials]
