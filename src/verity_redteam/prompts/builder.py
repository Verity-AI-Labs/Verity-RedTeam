"""Build the messages sent to the adversarial model."""

from __future__ import annotations

from verity_core.env import TaskSpec

from verity_redteam.prompts.templates import ADVERSARIAL_SYSTEM_PROMPT, ADVERSARIAL_USER_PROMPT

__all__ = ["PromptBuilder"]


class PromptBuilder:
    """Fill the adversarial templates. Static so strategies do not have to hold one."""

    @staticmethod
    def adversarial_system_prompt(strategy_name: str, domain: str) -> str:
        return ADVERSARIAL_SYSTEM_PROMPT.format(
            strategy_name=strategy_name,
            domain=domain,
        ).strip()

    @staticmethod
    def adversarial_user_prompt(spec: TaskSpec, attempt: int, n_trials: int) -> str:
        # Attempt is 1-based in the prompt so a human reading the log sees
        # "1 of 8" rather than "0 of 8".
        return ADVERSARIAL_USER_PROMPT.format(
            instructions=spec.instructions,
            domain=spec.domain,
            reward_type=spec.reward_type,
            attempt=attempt + 1,
            n_trials=n_trials,
        ).strip()
