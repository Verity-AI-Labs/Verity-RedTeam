"""Trial and probe records for a RedTeam audit.

These are the units a strategy produces and a probe aggregates. Later strategies
and axes reuse them unchanged; only the fields that describe one attempt live here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from verity_core.env import RewardResult
from verity_core.models import ModelResponse

if TYPE_CHECKING:
    from verity_redteam.analysis.hackability import HackabilityCurve

__all__ = ["AttackTrial", "ProbeResult"]


def _new_trial_id() -> str:
    return str(uuid4())


@dataclass(slots=True)
class AttackTrial:
    """One adversarial attempt against one environment.

    Strategies set ``reward`` and may classify a trial as ``failed`` or
    ``error``. They leave ``hacked`` False. The probe is the only place that
    sets ``hacked=True`` or ``classification="hacked"``: it translates
    ``reward.verdict`` after applying the gold-match filter. A submission that
    matches the gold solution is ``solved``, not hacked, even if the verifier
    accepted it.
    """

    env_id: str
    strategy: str
    attempt: int
    submission: str = ""
    reward: RewardResult = field(default_factory=lambda: RewardResult(0.0, False))
    hacked: bool = False
    messages: list[dict[str, Any]] = field(default_factory=list)
    model_id: str = ""
    model_response: ModelResponse | None = None
    duration: float = 0.0
    error: str | None = None
    trial_id: str = field(default_factory=_new_trial_id)
    # failed | hacked | solved | error. Strategies emit failed | error only.
    classification: str = "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "env_id": self.env_id,
            "strategy": self.strategy,
            "attempt": self.attempt,
            "submission": self.submission,
            "reward": self.reward.to_dict(),
            "hacked": self.hacked,
            "messages": list(self.messages),
            "model_id": self.model_id,
            "model_response": None
            if self.model_response is None
            else self.model_response.to_dict(),
            "duration": self.duration,
            "error": self.error,
            "classification": self.classification,
        }


@dataclass(slots=True)
class ProbeResult:
    """N trials of one strategy against one environment, plus the fitted curve.

    ``metadata`` holds probe-level flags such as a broken reset. It is a dict so
    later checks can add keys without changing the dataclass.
    """

    strategy: str
    env_id: str
    trials: list[AttackTrial]
    curve: HackabilityCurve
    total_tokens: int = 0
    duration: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "env_id": self.env_id,
            "trials": [trial.to_dict() for trial in self.trials],
            "curve": self.curve.to_dict(),
            "total_tokens": self.total_tokens,
            "duration": self.duration,
            "metadata": dict(self.metadata),
        }
