"""Build the messages sent to the adversarial model."""

from __future__ import annotations

from verity_core.env import TaskSpec

from verity_redteam.prompts.templates import (
    ADVERSARIAL_SYSTEM_PROMPT_V1,
    ADVERSARIAL_USER_PROMPT_V1,
)

__all__ = ["PromptBuilder", "display_attempt"]


def display_attempt(attempt: int) -> int:
    """Convert the 0-based internal index to the 1-based counter shown to the model.

    Internal loops stay 0-based. Prompts, logs, and evidence use this so trial 1
    never appears as "Attempt: 0 of N".
    """
    return attempt + 1


class PromptBuilder:
    """Fill the adversarial templates. Static so strategies do not have to hold one."""

    @staticmethod
    def adversarial_system_prompt(strategy_name: str, domain: str) -> str:
        return ADVERSARIAL_SYSTEM_PROMPT_V1.format(
            strategy_name=strategy_name,
            domain=domain,
        ).strip()

    @staticmethod
    def adversarial_user_prompt(spec: TaskSpec, attempt: int, n_trials: int) -> str:
        """Fill the user template. ``attempt`` is 0-based; the prompt is 1-based."""
        return ADVERSARIAL_USER_PROMPT_V1.format(
            instructions=spec.instructions,
            domain=spec.domain,
            reward_type=spec.reward_type,
            attempt=display_attempt(attempt),
            n_trials=n_trials,
        ).strip()
