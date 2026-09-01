"""Freeform hack: ask the model for a passing submission, send it to the verifier.

The only strategy in v0.1. Later strategies share the same Protocol surface and
are selected by name from the registry.
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from verity_core.env import RewardResult, TaskSpec, VerityEnv
from verity_core.models import ModelClient, ModelResponse

from verity_redteam.prompts.builder import PromptBuilder, display_attempt
from verity_redteam.types import AttackTrial

logger = logging.getLogger(__name__)

STRATEGY_NAME = "freeform"
TARGET_AXES: tuple[str, ...] = ("V1",)
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_SUBMISSION_LENGTH = 32768

__all__ = ["FreeformHackStrategy"]


class FreeformHackStrategy:
    """One-shot completion submitted raw to :meth:`VerityEnv.verify`."""

    def __init__(
        self,
        model: str,
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_submission_length: int = DEFAULT_MAX_SUBMISSION_LENGTH,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_submission_length = max_submission_length

    @property
    def name(self) -> str:
        return STRATEGY_NAME

    @property
    def target_axes(self) -> tuple[str, ...]:
        return TARGET_AXES

    def applies(self, spec: TaskSpec) -> bool:
        # v0.1: every environment is in scope. Per-domain filtering is a v0.2 hook.
        return True

    def attack(
        self,
        env: VerityEnv,
        spec: TaskSpec,
        client: ModelClient,
        attempt: int,
        n_trials: int,
    ) -> AttackTrial:
        started = time.monotonic()
        trial_id = str(uuid4())
        messages = [
            {
                "role": "system",
                "content": PromptBuilder.adversarial_system_prompt(self.name, spec.domain),
            },
            {
                "role": "user",
                "content": PromptBuilder.adversarial_user_prompt(spec, attempt, n_trials),
            },
        ]
        model_response: ModelResponse | None = None
        submission = ""
        reward = RewardResult(0.0, False)
        error: str | None = None
        hacked = False
        classification = "failed"

        try:
            # use_cache=False is mandatory: the ModelClient cache is keyed on the
            # request, and identical prompts would otherwise replay the same sample,
            # defeating trial independence.
            model_response = client.complete(
                self.model,
                messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                use_cache=False,
            )
            submission = model_response.content or ""
            if len(submission) > self.max_submission_length:
                logger.warning(
                    "truncating submission trial_id=%s length=%d max=%d",
                    trial_id,
                    len(submission),
                    self.max_submission_length,
                )
                submission = submission[: self.max_submission_length]
            try:
                reward = env.verify(submission)
                hacked = bool(reward.verdict)
                classification = "hacked" if hacked else "failed"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                classification = "error"
                logger.warning(
                    "verify failed env_id=%s trial_id=%s error=%s",
                    spec.id,
                    trial_id,
                    error,
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            classification = "error"
            logger.warning(
                "model call failed env_id=%s trial_id=%s error=%s",
                spec.id,
                trial_id,
                error,
            )

        duration = time.monotonic() - started
        logger.info(
            "trial done env_id=%s strategy=%s attempt=%d hacked=%s classification=%s "
            "duration=%.2fs",
            spec.id,
            self.name,
            display_attempt(attempt),
            hacked,
            classification,
            duration,
        )
        return AttackTrial(
            trial_id=trial_id,
            env_id=spec.id,
            strategy=self.name,
            attempt=attempt,
            submission=submission,
            reward=reward,
            hacked=hacked,
            messages=messages,
            model_id=self.model,
            model_response=model_response,
            duration=duration,
            error=error,
            classification=classification,
        )
