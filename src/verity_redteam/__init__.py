"""verity-redteam: adversarial auditing of RL environment verifiers."""

import logging

from verity_redteam.config import RedTeamConfig, load_redteam_config
from verity_redteam.probe import RedTeamProbe
from verity_redteam.runner import RedTeamRunner
from verity_redteam.strategies import FreeformHackStrategy, get_strategy, register_strategy
from verity_redteam.vrc import VRCLogger

__version__ = "0.1.0"

# A library must not configure logging for its host. The placeholder keeps Python from
# printing "no handlers could be found" while leaving handler and level choices to the
# CLI entry point.
logging.getLogger("verity_redteam").addHandler(logging.NullHandler())

__all__ = [
    "FreeformHackStrategy",
    "RedTeamConfig",
    "RedTeamProbe",
    "RedTeamRunner",
    "VRCLogger",
    "__version__",
    "get_strategy",
    "load_redteam_config",
    "register_strategy",
]
