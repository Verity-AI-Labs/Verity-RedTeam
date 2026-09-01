"""Tests for isomorphic perturbation generation, the validation guard, and V2 scoring."""

from __future__ import annotations

import json

import pytest
from tests.conftest import FakeClient, FakeEnv, make_spec
from verity_core.env import RewardResult

from verity_redteam.perturbation import IsomorphicPerturbationEngine, Perturbation


def _variant(description: str = "rename", gold: str = "print(2)") -> Perturbation:
    return Perturbation(description=description, transformed_gold=gold)


class TestGenerate:
    def test_parses_a_json_variant_from_the_model(self) -> None:
        spec = make_spec()
        client = FakeClient(
            contents=json.dumps(
                {
                    "description": "rename foo to bar",
                    "transformed_gold": "def bar():\n    return 1\n",
                    "variant_instructions": "Write bar instead of foo.",
                }
            )
        )
        engine = IsomorphicPerturbationEngine()
        item = engine.generate_one(spec, "def foo():\n    return 1\n", client, "m")  # type: ignore[arg-type]
        assert item.description == "rename foo to bar"
        assert "def bar" in item.transformed_gold
        assert item.variant_instructions.startswith("Write bar")
        assert client.calls[0]["use_cache"] is False


class TestValidateGuard:
    def test_keeps_a_perturbation_when_original_gold_still_passes(self) -> None:
        env = FakeEnv(gold="GOLD")
        item = _variant()
        assert IsomorphicPerturbationEngine().validate(env, "GOLD", item) is True
        assert item.valid is True
        assert item.original_accepts_gold is True
        assert item.discarded_reason is None

    def test_discards_when_gold_no_longer_passes_the_original_verifier(self) -> None:
        env = FakeEnv(gold="GOLD")

        def _reject(submission: str) -> RewardResult:
            env.verify_calls.append(submission)
            return RewardResult(0.0, False, "original mutated")

        env.verify = _reject  # type: ignore[method-assign]
        item = _variant()
        assert IsomorphicPerturbationEngine().validate(env, "GOLD", item) is False
        assert item.valid is False
        assert item.discarded_reason == "original_gold_rejected"
        assert item.original_accepts_gold is False

    def test_discards_when_original_verify_raises(self) -> None:
        env = FakeEnv(gold="GOLD")

        def _boom(submission: str) -> RewardResult:
            raise RuntimeError("boom")

        env.verify = _boom  # type: ignore[method-assign]
        item = _variant()
        assert IsomorphicPerturbationEngine().validate(env, "GOLD", item) is False
        assert item.discarded_reason == "original_verify_error:RuntimeError"


class TestEvaluateAndScore:
    def test_v2_is_the_fraction_of_valid_variants_that_reject_gold(self) -> None:
        def variant_verify(perturbation: Perturbation, gold: str) -> RewardResult:
            # First two reject the correct solution; the third accepts.
            if perturbation.description == "accept":
                return RewardResult(1.0, True)
            return RewardResult(0.0, False)

        engine = IsomorphicPerturbationEngine(variant_verifier=variant_verify)
        items = [_variant("a"), _variant("b"), _variant("accept"), _variant("invalid")]
        env = FakeEnv(gold="GOLD")
        for item in items[:3]:
            assert engine.validate(env, "GOLD", item) is True
            engine.evaluate("GOLD", item)
        items[3].valid = False
        items[3].discarded_reason = "original_gold_rejected"
        assert IsomorphicPerturbationEngine.score(items) == pytest.approx(2 / 3)

    def test_unscored_when_every_perturbation_was_discarded(self) -> None:
        items = [_variant()]
        items[0].valid = False
        assert IsomorphicPerturbationEngine.score(items) is None

    def test_evaluate_requires_a_variant_verifier(self) -> None:
        with pytest.raises(ValueError, match="variant verifier"):
            IsomorphicPerturbationEngine().evaluate("GOLD", _variant())
