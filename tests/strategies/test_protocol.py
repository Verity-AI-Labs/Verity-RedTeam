"""Tests for the AttackStrategy protocol and the strategy registry."""

from __future__ import annotations

import pytest
from tests.conftest import make_spec

from verity_redteam.strategies import (
    STRATEGY_REGISTRY,
    AttackStrategy,
    FreeformHackStrategy,
    get_strategy,
    register_strategy,
)
from verity_redteam.strategies.protocol import AttackStrategy as ProtocolClass


class TestProtocol:
    def test_freeform_satisfies_the_runtime_checkable_protocol(self) -> None:
        strategy = FreeformHackStrategy(model="test-model")
        assert isinstance(strategy, AttackStrategy)
        assert isinstance(strategy, ProtocolClass)

    def test_name_and_target_axes(self) -> None:
        strategy = FreeformHackStrategy(model="test-model")
        assert strategy.name == "freeform"
        assert strategy.target_axes == ("V1",)

    def test_applies_to_every_spec_in_v0(self) -> None:
        strategy = FreeformHackStrategy(model="test-model")
        assert strategy.applies(make_spec(domain="math")) is True
        assert strategy.applies(make_spec(domain="browser")) is True


class TestRegistry:
    def test_freeform_is_registered_on_import(self) -> None:
        assert "freeform" in STRATEGY_REGISTRY
        assert get_strategy("freeform") is FreeformHackStrategy

    def test_unknown_strategy_names_the_registered_ones(self) -> None:
        with pytest.raises(KeyError, match="unknown strategy 'nope'"):
            get_strategy("nope")

    def test_register_strategy_is_the_extension_hook(self) -> None:
        class Dummy:
            @property
            def name(self) -> str:
                return "dummy"

        register_strategy("dummy", Dummy)
        try:
            assert get_strategy("dummy") is Dummy
        finally:
            STRATEGY_REGISTRY.pop("dummy", None)
