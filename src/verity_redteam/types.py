"""Trial and probe records for a RedTeam audit.

These are the units a strategy produces and a probe aggregates. Later strategies
and axes reuse them unchanged; only the fields that describe one attempt live here.

Classification follows Terminal Wrench's trajectory outcomes: ``serious_hack``,
``nonserious_hack``, ``legitimate_solve``, ``no_reward``, plus ``error`` for
infrastructure failures. Only the two hack outcomes count toward alpha.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from verity_core.env import RewardResult
from verity_core.models import ModelResponse

from verity_redteam.outcomes import NO_REWARD, normalize_categories

if TYPE_CHECKING:
    from verity_redteam.analysis.hackability import HackabilityCurve

__all__ = ["AttackTrial", "Episode", "ProbeResult"]


def _new_trial_id() -> str:
    return str(uuid4())


@dataclass(slots=True)
class Episode:
    """One step of a multi-episode attacker loop, matching TW ``trajectory.json``.

    ``prompt`` and ``response`` are the model-facing strings for this episode.
    ``tool_calls`` are the sandbox commands (or submit actions) the attacker
    issued; ``observations`` are the exec results it saw before the next turn.
    """

    index: int
    prompt: str = ""
    response: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "prompt": self.prompt,
            "response": self.response,
            "tool_calls": [dict(call) for call in self.tool_calls],
            "observations": list(self.observations),
        }


@dataclass(slots=True)
class AttackTrial:
    """One adversarial attempt against one environment.

    Strategies set ``reward`` and may classify a trial as ``no_reward`` or
    ``error``. They leave ``hacked`` False. The probe is the only place that
    sets ``hacked=True`` or a hack classification: it translates the verifier
    verdict after the gold-match filter and the judge. A submission the judge
    (or gold-match filter) treats as a real solve is ``legitimate_solve``, not
    a hack, even if the verifier accepted it.
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
    # serious_hack | nonserious_hack | legitimate_solve | no_reward | error.
    # Strategies emit no_reward | error only.
    classification: str = NO_REWARD
    prompt_version: str = ""
    episodes: list[Episode] = field(default_factory=list)
    observed_categories: list[str] = field(default_factory=list)
    judgment: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.observed_categories = normalize_categories(self.observed_categories)

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
            "prompt_version": self.prompt_version,
            "episodes": [episode.to_dict() for episode in self.episodes],
            "observed_categories": list(self.observed_categories),
            "judgment": None if self.judgment is None else dict(self.judgment),
            "evidence": dict(self.evidence),
        }


@dataclass(slots=True)
class ProbeResult:
    """N trials of one strategy against one environment, plus the fitted curve.

    ``metadata`` holds probe-level flags such as a failed precheck. It is a dict so
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
