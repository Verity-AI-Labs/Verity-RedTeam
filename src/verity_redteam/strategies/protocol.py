"""AttackStrategy protocol: one way of trying to extract unverified reward.

A Protocol rather than an ABC, matching :class:`~verity_core.env.VerityEnv`.
Strategies conform structurally; they never need to inherit from anything here.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from verity_core.env import TaskSpec, VerityEnv
from verity_core.models import ModelClient

from verity_redteam.types import AttackTrial

__all__ = ["AttackStrategy"]


@runtime_checkable
class AttackStrategy(Protocol):
    """One attack, scored against the axes in :attr:`target_axes`.

    ``applies`` is the v0.2 hook for per-domain filtering: v0.1 strategies always
    return True, and the runner skips a strategy when it returns False.
    """

    @property
    def name(self) -> str:
        """Registry key and VRC ``exploit_type``."""
        ...

    @property
    def target_axes(self) -> tuple[str, ...]:
        """Scorecard axes this strategy is evidence for."""
        ...

    def applies(self, spec: TaskSpec) -> bool:
        """Whether this strategy is worth running on ``spec``."""
        ...

    def attack(
        self,
        env: VerityEnv,
        spec: TaskSpec,
        client: ModelClient,
        attempt: int,
        n_trials: int,
    ) -> AttackTrial:
        """Run one adversarial attempt. Must not raise: record errors on the trial.

        ``attempt`` is 0-based. ``n_trials`` is the probe's planned loop count so
        the prompt can say "Attempt: k of N" without the strategy storing N.

        Set ``reward`` and ``classification`` in ``{no_reward, error}`` only. Do
        not set ``hacked`` or a hack outcome: the probe owns that translation
        after the gold-match filter and the judge.
        """
        ...
