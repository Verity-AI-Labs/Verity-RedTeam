"""Build the messages sent to the adversarial model."""

from __future__ import annotations

from verity_core.env import TaskSpec

from verity_redteam.prompts.templates import (
    ADVERSARIAL_SYSTEM_PROMPT_V1,
    ADVERSARIAL_SYSTEM_PROMPT_V2,
    ADVERSARIAL_USER_PROMPT_V1,
    ADVERSARIAL_USER_PROMPT_V2,
    PROMPT_VERSION,
)

__all__ = ["PromptBuilder", "display_attempt"]

_SYSTEM_TEMPLATES = {
    "v1": ADVERSARIAL_SYSTEM_PROMPT_V1,
    "v2": ADVERSARIAL_SYSTEM_PROMPT_V2,
}
_USER_TEMPLATES = {
    "v1": ADVERSARIAL_USER_PROMPT_V1,
    "v2": ADVERSARIAL_USER_PROMPT_V2,
}


def display_attempt(attempt: int) -> int:
    """Convert the 0-based internal index to the 1-based counter shown to the model.

    Internal loops stay 0-based. Prompts, logs, and evidence use this so trial 1
    never appears as "Attempt: 0 of N".
    """
    return attempt + 1


class PromptBuilder:
    """Fill the adversarial templates. Static so strategies do not have to hold one."""

    @staticmethod
    def adversarial_system_prompt(
        strategy_name: str,
        domain: str,
        *,
        version: str = PROMPT_VERSION,
        template: str | None = None,
    ) -> str:
        chosen = template or _SYSTEM_TEMPLATES.get(version, ADVERSARIAL_SYSTEM_PROMPT_V2)
        return chosen.format(
            strategy_name=strategy_name,
            domain=domain,
        ).strip()

    @staticmethod
    def adversarial_user_prompt(
        spec: TaskSpec,
        attempt: int,
        n_trials: int,
        *,
        version: str = PROMPT_VERSION,
        template: str | None = None,
    ) -> str:
        """Fill the user template. ``attempt`` is 0-based; the prompt is 1-based."""
        chosen = template or _USER_TEMPLATES.get(version, ADVERSARIAL_USER_PROMPT_V2)
        return chosen.format(
            instructions=spec.instructions,
            domain=spec.domain,
            reward_type=spec.reward_type,
            attempt=display_attempt(attempt),
            n_trials=n_trials,
        ).strip()
