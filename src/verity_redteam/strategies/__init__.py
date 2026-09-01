"""Strategy registry.

v0.1 ships one strategy. ``register_strategy`` is the hook later releases use to
add isomorphic perturbation and whatever follows without touching the runner.
"""

from __future__ import annotations

import logging

from verity_redteam.strategies.freeform import FreeformHackStrategy
from verity_redteam.strategies.protocol import AttackStrategy

logger = logging.getLogger(__name__)

STRATEGY_REGISTRY: dict[str, type] = {}

__all__ = [
    "STRATEGY_REGISTRY",
    "AttackStrategy",
    "FreeformHackStrategy",
    "get_strategy",
    "register_strategy",
]


def register_strategy(name: str, strategy_cls: type) -> None:
    """Register a strategy class under ``name``.

    NOT THREAD-SAFE. Registration happens once at import, matching
    :func:`verity_core.adapters.register_adapter`.
    """
    STRATEGY_REGISTRY[name] = strategy_cls
    logger.info("registered strategy name=%s cls=%s", name, strategy_cls.__name__)


def get_strategy(name: str) -> type:
    try:
        return STRATEGY_REGISTRY[name]
    except KeyError as exc:
        registered = ", ".join(sorted(STRATEGY_REGISTRY)) or "(none)"
        raise KeyError(f"unknown strategy {name!r}; registered: {registered}") from exc


register_strategy("freeform", FreeformHackStrategy)
