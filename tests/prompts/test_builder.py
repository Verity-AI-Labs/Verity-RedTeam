"""Tests for adversarial prompt construction."""

from __future__ import annotations

from tests.conftest import make_spec

from verity_redteam.prompts.builder import PromptBuilder, display_attempt
from verity_redteam.prompts.templates import (
    ADVERSARIAL_SYSTEM_PROMPT_V1,
    ADVERSARIAL_SYSTEM_PROMPT_V2,
    ADVERSARIAL_USER_PROMPT_V1,
    ADVERSARIAL_USER_PROMPT_V2,
    PROMPT_VERSION,
)


class TestSystemPrompt:
    def test_names_the_strategy_and_domain(self) -> None:
        text = PromptBuilder.adversarial_system_prompt("freeform", "code")
        assert "freeform" in text
        assert "code" in text
        assert "authorized Red Team QA Agent" in text
        assert "Do not complete the task via the intended legitimate solution path" in text

    def test_v2_lists_what_to_attack_and_what_to_prioritize(self) -> None:
        text = PromptBuilder.adversarial_system_prompt("freeform", "code")
        for needle in (
            "verification logic",
            "scoring checks",
            "output validation",
            "artifact handling",
            "environment assumptions",
            "state-dependent edge cases",
            "minimal-effort paths",
            "placeholder outputs",
            "verifier blind spots",
            "brittle assertions",
            "leaked-answer",
            "alternate state setups",
        ):
            assert needle in text
        assert "Success for this role means" in ADVERSARIAL_SYSTEM_PROMPT_V2
        assert "Failure for this role means" in ADVERSARIAL_SYSTEM_PROMPT_V2

    def test_does_not_leave_format_braces(self) -> None:
        text = PromptBuilder.adversarial_system_prompt("freeform", "math")
        assert "{" not in text
        assert "}" not in text

    def test_v1_is_still_renderable_for_ab_tests(self) -> None:
        text = PromptBuilder.adversarial_system_prompt("freeform", "code", version="v1")
        assert "WITHOUT actually solving" in text
        assert "WITHOUT actually solving" in ADVERSARIAL_SYSTEM_PROMPT_V1
        assert "authorized Red Team QA Agent" not in text


class TestUserPrompt:
    def test_zero_based_attempt_is_shown_as_one_based(self) -> None:
        assert display_attempt(0) == 1
        assert display_attempt(7) == 8

    def test_includes_instructions_and_a_one_based_attempt_counter(self) -> None:
        spec = make_spec(instructions="Write fizzbuzz.", domain="code", reward_type="binary")
        text = PromptBuilder.adversarial_user_prompt(spec, attempt=0, n_trials=8)
        assert "Write fizzbuzz." in text
        assert "Attempt: 1 of 8" in text
        assert "Attempt: 0 of" not in text
        assert "binary" in text
        assert "code" in text
        assert "EXEC:" in text
        assert "SUBMIT:" in text

    def test_template_is_a_module_constant(self) -> None:
        assert "{instructions}" in ADVERSARIAL_USER_PROMPT_V2
        assert "{attempt}" in ADVERSARIAL_USER_PROMPT_V2
        assert "{instructions}" in ADVERSARIAL_USER_PROMPT_V1
        assert PROMPT_VERSION == "v2"

    def test_v1_user_prompt_is_still_renderable(self) -> None:
        spec = make_spec(instructions="Write fizzbuzz.")
        text = PromptBuilder.adversarial_user_prompt(spec, attempt=0, n_trials=8, version="v1")
        assert "Submit something that passes the verifier" in text
        assert "EXEC:" not in text

    def test_custom_templates_are_filled(self) -> None:
        spec = make_spec(instructions="Find the key.")
        text = PromptBuilder.adversarial_system_prompt(
            "leakage",
            "code",
            template="Agent {strategy_name} in {domain}",
        )
        assert text == "Agent leakage in code"
        user = PromptBuilder.adversarial_user_prompt(
            spec, 0, 2, template="Do {instructions} ({attempt}/{n_trials})"
        )
        assert user == "Do Find the key. (1/2)"
