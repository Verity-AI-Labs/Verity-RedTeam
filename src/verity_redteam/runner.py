"""Top-level orchestrator: probe an environment, score V1, log exploits.

``RedTeamRunner.audit`` is the ``audit_fn`` handed to
:func:`verity_core.batch.run_batch`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from verity_core.adapters import load_env
from verity_core.scorecard import Scorecard

from verity_redteam.probe import RedTeamProbe
from verity_redteam.strategies.protocol import AttackStrategy
from verity_redteam.types import ProbeResult
from verity_redteam.vrc import VRCLogger

logger = logging.getLogger(__name__)

TOOL_NAME = "verity-redteam"
V1_AXIS = "V1"

__all__ = ["TOOL_NAME", "V1_AXIS", "RedTeamRunner"]


class RedTeamRunner:
    """Audit one manifest entry: load the env, run applicable strategies, score V1."""

    def __init__(
        self,
        client: Any,
        strategies: Sequence[AttackStrategy],
        n_trials: int,
        vrc_dir: Path | str,
    ) -> None:
        self.client = client
        self.strategies = list(strategies)
        self.n_trials = n_trials
        self.vrc = VRCLogger(vrc_dir)

    def audit(self, manifest_entry: dict[str, Any]) -> Scorecard:
        env = load_env(manifest_entry)
        try:
            return self._audit_env(env, manifest_entry)
        finally:
            env.close()

    def _audit_env(self, env: Any, manifest_entry: dict[str, Any]) -> Scorecard:
        spec = env.spec()
        results: list[ProbeResult] = []
        for strategy in self.strategies:
            if not strategy.applies(spec):
                logger.info(
                    "strategy skipped env_id=%s strategy=%s reason=not applicable",
                    spec.id,
                    strategy.name,
                )
                continue
            probe = RedTeamProbe(strategy, n_trials=self.n_trials)
            result = probe.run(env, spec, self.client)
            results.append(result)
            self.vrc.log_probe(result)

        scorecard = Scorecard(
            env_id=spec.id,
            metadata={
                "domain": spec.domain,
                "format": str(manifest_entry.get("format", "")),
                "source": spec.source,
                "commit": spec.commit,
                "audited_by": TOOL_NAME,
                "tokens": sum(r.total_tokens for r in results),
                "reset_broken": any(r.metadata.get("reset_broken") for r in results),
                "strategies": [r.strategy for r in results],
            },
        )
        if not results:
            # Unscored, not zero: we did not look.
            logger.warning("no applicable strategies env_id=%s; %s left unscored", spec.id, V1_AXIS)
            return scorecard

        # v0.1 has one strategy so max is that alpha; the reduction is the v0.2 hook.
        best = max(results, key=lambda result: result.curve.alpha)
        successful_ids = [t.trial_id for t in best.trials if t.hacked]
        scorecard.set_axis(
            V1_AXIS,
            value=best.curve.alpha,
            tool=TOOL_NAME,
            evidence={
                "alpha": best.curve.alpha,
                "alpha_ci_lower": best.curve.alpha_ci_lower,
                "alpha_ci_upper": best.curve.alpha_ci_upper,
                "n_trials": best.curve.n_trials,
                "n_successes": best.curve.n_successes,
                "strategy": best.strategy,
                "curve": dict(best.curve.curve),
                "trial_ids": successful_ids,
            },
            notes=(
                "V1 is per-trial hackability alpha: the fraction of adversarial attempts "
                "the verifier accepted that were not the gold solution"
            ),
        )
        return scorecard
