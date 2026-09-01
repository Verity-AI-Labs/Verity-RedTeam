"""V4: obtain the answer through leaked state rather than solving."""

from __future__ import annotations

from verity_core.env import TaskSpec, VerityEnv
from verity_core.models import ModelClient

from verity_redteam.agent import (
    DEFAULT_MAX_EPISODES,
    DEFAULT_MAX_SUBMISSION_LENGTH,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    run_prompted_trial,
)
from verity_redteam.judge import Judgment
from verity_redteam.outcomes import HACK_OUTCOMES
from verity_redteam.prompts.templates import LEAKAGE_SYSTEM_PROMPT, LEAKAGE_USER_PROMPT
from verity_redteam.sandbox import is_container_backed
from verity_redteam.types import AttackTrial

STRATEGY_NAME = "leakage"
TARGET_AXES: tuple[str, ...] = ("V4",)

__all__ = ["LeakageStrategy"]


class LeakageStrategy:
    """Container-only attack that looks for non-causal answer channels."""

    def __init__(
        self,
        model: str,
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_submission_length: int = DEFAULT_MAX_SUBMISSION_LENGTH,
        max_episodes: int = DEFAULT_MAX_EPISODES,
        **_kwargs: object,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_submission_length = max_submission_length
        self.max_episodes = max_episodes

    @property
    def name(self) -> str:
        return STRATEGY_NAME

    @property
    def target_axes(self) -> tuple[str, ...]:
        return TARGET_AXES

    def applies(self, spec: TaskSpec, env: object | None = None) -> bool:
        return is_container_backed(env)

    def counts_as_hack(self, trial: AttackTrial, judgment: Judgment) -> bool:
        if "structural-extraction" in trial.observed_categories:
            return True
        return judgment.outcome in HACK_OUTCOMES

    def attack(
        self,
        env: VerityEnv,
        spec: TaskSpec,
        client: ModelClient,
        attempt: int,
        n_trials: int,
    ) -> AttackTrial:
        return run_prompted_trial(
            model=self.model,
            env=env,
            spec=spec,
            client=client,
            attempt=attempt,
            n_trials=n_trials,
            strategy=self.name,
            system_template=LEAKAGE_SYSTEM_PROMPT,
            user_template=LEAKAGE_USER_PROMPT,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            max_submission_length=self.max_submission_length,
            max_episodes=self.max_episodes,
        )
