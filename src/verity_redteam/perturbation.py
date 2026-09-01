"""Isomorphic perturbation testing for V2 (verifier completeness).

Take a known-correct solution, generate surface-only task/solution variants,
and measure how often a variant verifier wrongly rejects the correct solution.

A perturbation is counted only after the guard: the original gold solution must
still pass the original verifier. If it does not, the transform changed the
task (or the env was already broken) and the finding is discarded.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from verity_core.env import RewardResult, TaskSpec, VerityEnv
from verity_core.models import ModelClient

logger = logging.getLogger(__name__)

DEFAULT_N_PERTURBATIONS = 4
DEFAULT_TEMPERATURE = 0.4
DEFAULT_MAX_TOKENS = 2048

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

VariantVerifier = Callable[["Perturbation", str], RewardResult]

GENERATE_SYSTEM_PROMPT = """\
You generate logically equivalent variants of a correct solution for verifier \
completeness testing. Change surface details only: rename identifiers, change \
string literals, reorder independent operations, or swap equivalent \
representations. Do not change the meaning of the solution.

Reply with JSON only:
{"description": "...", "transformed_gold": "...", "variant_instructions": "..."}
"""

__all__ = [
    "DEFAULT_N_PERTURBATIONS",
    "GENERATE_SYSTEM_PROMPT",
    "IsomorphicPerturbationEngine",
    "Perturbation",
    "VariantVerifier",
]


@dataclass(slots=True)
class Perturbation:
    """One surface-only variant of a known-correct solution / task."""

    description: str
    transformed_gold: str
    variant_instructions: str = ""
    valid: bool | None = None
    discarded_reason: str | None = None
    original_accepts_gold: bool | None = None
    variant_accepts_gold: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "transformed_gold": self.transformed_gold,
            "variant_instructions": self.variant_instructions,
            "valid": self.valid,
            "discarded_reason": self.discarded_reason,
            "original_accepts_gold": self.original_accepts_gold,
            "variant_accepts_gold": self.variant_accepts_gold,
        }


def parse_perturbation_payload(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("perturbation reply was empty")
    match = _JSON_FENCE.search(stripped)
    blob = match.group(1) if match else stripped
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        fallback = _JSON_OBJECT.search(stripped)
        if fallback is None:
            raise ValueError("perturbation reply was not JSON") from None
        payload = json.loads(fallback.group(0))
    if not isinstance(payload, dict):
        raise ValueError("perturbation JSON must be an object")
    return payload


class IsomorphicPerturbationEngine:
    """Generate, validate, and score isomorphic task/solution variants."""

    def __init__(
        self,
        *,
        variant_verifier: VariantVerifier | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.variant_verifier = variant_verifier
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate_one(
        self,
        spec: TaskSpec,
        gold: str,
        client: ModelClient,
        model: str,
        *,
        attempt: int = 0,
    ) -> Perturbation:
        user = {
            "instructions": spec.instructions,
            "domain": spec.domain,
            "gold": gold,
            "attempt": attempt,
        }
        response = client.complete(
            model,
            [
                {"role": "system", "content": GENERATE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user)},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            use_cache=False,
        )
        payload = parse_perturbation_payload(response.content)
        transformed = str(payload.get("transformed_gold") or payload.get("variant") or "")
        return Perturbation(
            description=str(payload.get("description") or ""),
            transformed_gold=transformed,
            variant_instructions=str(payload.get("variant_instructions") or ""),
            raw=payload,
        )

    def validate(self, env: VerityEnv, gold: str, perturbation: Perturbation) -> bool:
        """Discard the variant unless original gold still passes the original verifier."""
        try:
            result = env.verify(gold)
        except Exception as exc:
            perturbation.valid = False
            perturbation.discarded_reason = f"original_verify_error:{type(exc).__name__}"
            logger.warning("perturbation guard failed: original verify error=%s", exc)
            return False
        perturbation.original_accepts_gold = bool(result.verdict)
        if not result.verdict:
            perturbation.valid = False
            perturbation.discarded_reason = "original_gold_rejected"
            logger.info("discarding perturbation: gold no longer passes the original verifier")
            return False
        perturbation.valid = True
        perturbation.discarded_reason = None
        return True

    def evaluate(
        self,
        gold: str,
        perturbation: Perturbation,
        *,
        variant_verifier: VariantVerifier | None = None,
    ) -> RewardResult:
        """Run the correct solution against the variant verifier."""
        verifier = variant_verifier or self.variant_verifier
        if verifier is None:
            raise ValueError("no variant verifier: pass variant_verifier to evaluate()")
        result = verifier(perturbation, gold)
        perturbation.variant_accepts_gold = bool(result.verdict)
        return result

    @staticmethod
    def score(perturbations: Sequence[Perturbation]) -> float | None:
        """V2 = fraction of *valid* variants that wrongly reject the correct solution.

        Returns ``None`` when nothing valid was measured (unscored, not zero).
        """
        valid = [item for item in perturbations if item.valid]
        measured = [item for item in valid if item.variant_accepts_gold is not None]
        if not measured:
            return None
        rejects = sum(1 for item in measured if item.variant_accepts_gold is False)
        return rejects / len(measured)
