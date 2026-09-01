"""Log successful exploits as VRC entries.

Wraps :class:`verity_corpus.models.vrc.VRCEntry` rather than reimplementing it.
Dedup in v0.1 is in-memory per audit run; disk-level dedup is a v0.2 concern.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from verity_corpus.models.vrc import VRCEntry

from verity_redteam.analysis.hackability import HackabilityCurve
from verity_redteam.types import AttackTrial, ProbeResult

logger = logging.getLogger(__name__)

__all__ = ["VRCLogger"]


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
