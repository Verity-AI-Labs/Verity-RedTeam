"""Freeform hack: ask the model for a passing submission, send it to the verifier.

v0.1 was a single completion. Container-backed environments now run a multi-episode
agent loop; ``verifiers``-format environments stay one-shot.
"""

from __future__ import annotations

from verity_core.env import TaskSpec, VerityEnv
from verity_core.models import ModelClient

from verity_redteam.agent import (
    DEFAULT_MAX_EPISODES,
    DEFAULT_MAX_SUBMISSION_LENGTH,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    AgentLoop,
)
from verity_redteam.prompts.templates import PROMPT_VERSION
from verity_redteam.types import AttackTrial

STRATEGY_NAME = "freeform"
TARGET_AXES: tuple[str, ...] = ("V1",)

__all__ = ["FreeformHackStrategy"]


class FreeformHackStrategy:
    """Adversarial attempt submitted to :meth:`VerityEnv.verify`.

    Sets ``reward`` and ``classification`` in ``{no_reward, error}``. The probe
    translates a passing verdict into a hack outcome after the gold-match filter
    and the judge.
    """

    def __init__(
        self,
        model: str,
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_submission_length: int = DEFAULT_MAX_SUBMISSION_LENGTH,
        max_episodes: int = DEFAULT_MAX_EPISODES,
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_submission_length = max_submission_length
        self.max_episodes = max_episodes
        self.prompt_version = prompt_version
        self._loop = AgentLoop(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            max_submission_length=max_submission_length,
            max_episodes=max_episodes,
            prompt_version=prompt_version,
        )

    @property
    def name(self) -> str:
        return STRATEGY_NAME

    @property
    def target_axes(self) -> tuple[str, ...]:
        return TARGET_AXES

    def applies(self, spec: TaskSpec) -> bool:
        return True

    def attack(
        self,
        env: VerityEnv,
        spec: TaskSpec,
        client: ModelClient,
        attempt: int,
        n_trials: int,
    ) -> AttackTrial:
        return self._loop.run(env, spec, client, attempt, n_trials, strategy=self.name)
