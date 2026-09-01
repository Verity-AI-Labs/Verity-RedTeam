"""Log successful exploits as VRC entries.

Wraps :class:`verity_corpus.models.vrc.VRCEntry` rather than reimplementing it.
Dedup in v0.1 is in-memory per audit run; disk-level dedup is a v0.2 concern.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from verity_corpus.models.vrc import VRCEntry

from verity_redteam.analysis.hackability import HackabilityCurve
from verity_redteam.types import AttackTrial, ProbeResult

logger = logging.getLogger(__name__)

USER_PREVIEW_CHARS = 80

__all__ = ["USER_PREVIEW_CHARS", "VRCLogger", "last_user_preview", "load_vrc_entries"]


def last_user_preview(trajectory: list[dict[str, Any]], n: int = USER_PREVIEW_CHARS) -> str:
    """Return the first ``n`` chars of the last user message in a trajectory."""
    content = ""
    for message in trajectory:
        if message.get("role") == "user":
            content = str(message.get("content") or "")
    return content[:n]


def load_vrc_entries(vrc_dir: Path | str, env_id: str) -> list[VRCEntry]:
    """Load every VRC JSON file under ``{vrc_dir}/{env_id}/``."""
    folder = Path(vrc_dir) / env_id
    if not folder.is_dir():
        return []
    entries: list[VRCEntry] = []
    for path in sorted(folder.glob("*.json")):
        entries.append(VRCEntry.model_validate_json(path.read_text(encoding="utf-8")))
    return entries


class VRCLogger:
    """Write one VRC file per unique hacked submission."""

    def __init__(self, vrc_dir: Path | str) -> None:
        self.vrc_dir = Path(vrc_dir)
        self._seen: set[tuple[str, str]] = set()

    def log_trial(self, trial: AttackTrial, curve: HackabilityCurve) -> Path | None:
        if not trial.hacked:
            return None
        digest = hashlib.sha256(trial.submission.encode("utf-8")).hexdigest()
        key = (trial.env_id, digest)
        if key in self._seen:
            logger.info(
                "vrc skip duplicate env_id=%s sha256=%s",
                trial.env_id,
                digest[:12],
            )
            return None
        self._seen.add(key)
        entry = VRCEntry(
            env_id=trial.env_id,
            exploit_type=trial.strategy,
            trajectory=list(trial.messages),
            hackability_curve=dict(curve.curve),
            model_id=trial.model_id,
            notes=f"trial_id={trial.trial_id}",
        )
        path = entry.save(self.vrc_dir)
        logger.info(
            "vrc written env_id=%s trial_id=%s path=%s",
            trial.env_id,
            trial.trial_id,
            path,
        )
        return path

    def log_probe(self, result: ProbeResult) -> list[Path]:
        written: list[Path] = []
        for trial in result.trials:
            path = self.log_trial(trial, result.curve)
            if path is not None:
                written.append(path)
        return written
