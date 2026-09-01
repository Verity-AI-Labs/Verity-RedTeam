"""V2 strategy: isomorphic perturbation of a known-correct solution."""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from verity_core.env import RewardResult, TaskSpec, VerityEnv
from verity_core.models import ModelClient

from verity_redteam.judge import Judgment
from verity_redteam.outcomes import ERROR, NO_REWARD
from verity_redteam.perturbation import IsomorphicPerturbationEngine, VariantVerifier
from verity_redteam.types import AttackTrial

logger = logging.getLogger(__name__)

STRATEGY_NAME = "isomorphic"
TARGET_AXES: tuple[str, ...] = ("V2",)

__all__ = ["IsomorphicPerturbationStrategy"]


class IsomorphicPerturbationStrategy:
    """One perturbation attempt scored as a V2 completeness failure when rejected."""

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        max_submission_length: int = 32768,
        max_episodes: int = 15,
        variant_verifier: VariantVerifier | None = None,
        **_kwargs: object,
    ) -> None:
        self.model = model
        self.max_submission_length = max_submission_length
        self.engine = IsomorphicPerturbationEngine(
            variant_verifier=variant_verifier,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @property
    def name(self) -> str:
        return STRATEGY_NAME

    @property
    def target_axes(self) -> tuple[str, ...]:
        return TARGET_AXES

    def applies(self, spec: TaskSpec, env: object | None = None) -> bool:
        return bool(spec.has_gold) and self.engine.variant_verifier is not None

    def counts_as_hack(self, trial: AttackTrial, judgment: Judgment) -> bool:
        return bool(trial.evidence.get("variant_rejected"))

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
        gold = env.gold_solution()
        error: str | None = None
        classification = NO_REWARD
        reward = RewardResult(0.0, False)
        submission = ""
        evidence: dict[str, object] = {}
        try:
            if gold is None:
                raise RuntimeError("isomorphic strategy requires a gold solution")
            perturbation = self.engine.generate_one(spec, gold, client, self.model, attempt=attempt)
            submission = perturbation.transformed_gold[: self.max_submission_length]
            evidence["perturbation"] = perturbation.to_dict()
            if not self.engine.validate(env, gold, perturbation):
                evidence["discarded_reason"] = perturbation.discarded_reason
                evidence["variant_rejected"] = False
            elif self.engine.variant_verifier is None:
                error = "no variant verifier configured"
                classification = ERROR
                evidence["variant_rejected"] = False
            else:
                reward = self.engine.evaluate(gold, perturbation)
                evidence["variant_rejected"] = not reward.verdict
                evidence["perturbation"] = perturbation.to_dict()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            classification = ERROR
            logger.warning("isomorphic attempt failed env_id=%s error=%s", spec.id, error)
        return AttackTrial(
            trial_id=trial_id,
            env_id=spec.id,
            strategy=self.name,
            attempt=attempt,
            submission=submission,
            reward=reward,
            hacked=False,
            model_id=self.model,
            duration=time.monotonic() - started,
            error=error,
            classification=classification,
            evidence=evidence,
        )
