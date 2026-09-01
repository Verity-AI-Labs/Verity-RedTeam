"""Tests for adversarial prompt construction."""

from __future__ import annotations

from tests.conftest import make_spec

from verity_redteam.prompts.builder import PromptBuilder, display_attempt
from verity_redteam.prompts.templates import (
    ADVERSARIAL_SYSTEM_PROMPT_V1,
    ADVERSARIAL_USER_PROMPT_V1,
    PROMPT_VERSION,
)


class TestSystemPrompt:
    def test_names_the_strategy_and_domain(self) -> None:
        text = PromptBuilder.adversarial_system_prompt("freeform", "code")
        assert "freeform" in text
        assert "code" in text
        assert "WITHOUT actually solving" in ADVERSARIAL_SYSTEM_PROMPT_V1
        assert "WITHOUT" in text

    def test_does_not_leave_format_braces(self) -> None:
        text = PromptBuilder.adversarial_system_prompt("freeform", "math")
        assert "{" not in text
        assert "}" not in text


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

    def test_template_is_a_module_constant(self) -> None:
        assert "{instructions}" in ADVERSARIAL_USER_PROMPT_V1
        assert "{attempt}" in ADVERSARIAL_USER_PROMPT_V1
        assert PROMPT_VERSION == "v1"
