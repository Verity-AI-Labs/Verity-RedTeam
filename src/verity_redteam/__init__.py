"""verity-redteam: adversarial auditing of RL environment verifiers."""

import logging

__version__ = "0.1.0"

# A library must not configure logging for its host. The placeholder keeps Python from
# printing "no handlers could be found" while leaving handler and level choices to the
# CLI entry point.
logging.getLogger("verity_redteam").addHandler(logging.NullHandler())

__all__ = ["__version__"]
