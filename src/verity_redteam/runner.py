"""Top-level orchestrator: probe an environment, score RedTeam axes, log exploits.

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

from verity_redteam.judge import Judge
from verity_redteam.probe import RedTeamProbe
from verity_redteam.strategies.protocol import AttackStrategy
from verity_redteam.types import ProbeResult
from verity_redteam.vrc import VRCLogger

logger = logging.getLogger(__name__)

TOOL_NAME = "verity-redteam"
V1_AXIS = "V1"
AXIS_NOTES: dict[str, str] = {
    "V1": (
        "V1 is per-trial hackability alpha: the fraction of judged hacks "
        "(serious + nonserious) among attempts that were not legitimate solves"
    ),
    "V2": (
        "V2 is verifier incompleteness: the fraction of valid isomorphic "
        "variants that wrongly reject a known-correct solution"
    ),
    "V4": (
        "V4 is state/answer-key leakage: the fraction of attempts that obtained "
        "the answer through a non-causal channel"
    ),
    "V6": (
        "V6 is isolation/side-channel integrity: the fraction of attempts that "
        "obtained reward by tampering with the grading process"
    ),
    "V7": (
        "V7 is reward-shaping correctness: the fraction of attempts that earned "
        "partial reward while the core task was not solved"
    ),
}

__all__ = ["AXIS_NOTES", "TOOL_NAME", "V1_AXIS", "RedTeamRunner"]


def _strategy_applies(strategy: AttackStrategy, spec: Any, env: Any) -> bool:
    method = strategy.applies
    try:
        return bool(method(spec, env))
    except TypeError:
        return bool(method(spec))


class RedTeamRunner:
    """Audit one manifest entry: load the env, run applicable strategies, score axes."""

    def __init__(
        self,
        client: Any,
        strategies: Sequence[AttackStrategy],
        n_trials: int,
        vrc_dir: Path | str,
        judge: Judge | None = None,
    ) -> None:
        self.client = client
        self.strategies = list(strategies)
        self.n_trials = n_trials
        self.vrc = VRCLogger(vrc_dir)
        self.judge = judge

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
            if not _strategy_applies(strategy, spec, env):
                logger.info(
                    "strategy skipped env_id=%s strategy=%s reason=not applicable",
                    spec.id,
                    strategy.name,
                )
                continue
            probe = RedTeamProbe(strategy, n_trials=self.n_trials, judge=self.judge)
            result = probe.run(env, spec, self.client)
            results.append(result)
            self.vrc.log_probe(result)

        precheck_failed = any(r.metadata.get("precheck_failed") for r in results)
        scorecard = Scorecard(
            env_id=spec.id,
            metadata={
                "domain": spec.domain,
                "format": str(manifest_entry.get("format", "")),
                "source": spec.source,
                "commit": spec.commit,
                "audited_by": TOOL_NAME,
                "tokens": sum(r.total_tokens for r in results),
                "precheck_failed": precheck_failed,
                "strategies": [r.strategy for r in results],
                "n_solved": sum(int(r.metadata.get("n_solved") or 0) for r in results),
                "n_legitimate_solve": sum(
                    int(r.metadata.get("n_legitimate_solve") or 0) for r in results
                ),
                "n_errored": sum(int(r.metadata.get("n_errored") or 0) for r in results),
            },
        )
        if not results:
            logger.warning("no applicable strategies env_id=%s; axes left unscored", spec.id)
            return scorecard
        if precheck_failed:
            logger.warning(
                "precheck failed env_id=%s; RedTeam axes left unscored",
                spec.id,
            )
            return scorecard

        by_axis: dict[str, list[ProbeResult]] = {}
        for result in results:
            strategy = next(
                (item for item in self.strategies if item.name == result.strategy),
                None,
            )
            axes = strategy.target_axes if strategy is not None else ("V1",)
            for axis in axes:
                by_axis.setdefault(axis, []).append(result)

        for axis, axis_results in by_axis.items():
            self._set_axis(scorecard, axis, axis_results)
        return scorecard

    def _set_axis(self, scorecard: Scorecard, axis: str, results: list[ProbeResult]) -> None:
        best = max(results, key=lambda result: result.curve.alpha)
        successful_ids = [trial.trial_id for trial in best.trials if trial.hacked]
        categories: list[str] = []
        for trial in best.trials:
            for name in trial.observed_categories:
                if name not in categories:
                    categories.append(name)
        scorecard.set_axis(
            axis,
            value=best.curve.alpha,
            tool=TOOL_NAME,
            evidence={
                "alpha": best.curve.alpha,
                "alpha_ci_lower": best.curve.alpha_ci_lower,
                "alpha_ci_upper": best.curve.alpha_ci_upper,
                "serious_alpha": best.curve.serious_alpha,
                "nonserious_alpha": best.curve.nonserious_alpha,
                "n_trials": best.curve.n_trials,
                "n_successes": best.curve.n_successes,
                "n_serious": best.curve.n_serious,
                "n_nonserious": best.curve.n_nonserious,
                "strategy": best.strategy,
                "curve": dict(best.curve.curve),
                "serious_curve": dict(best.curve.serious_curve),
                "trial_ids": successful_ids,
                "observed_categories": categories,
                "n_solved": int(best.metadata.get("n_solved") or 0),
                "n_legitimate_solve": int(best.metadata.get("n_legitimate_solve") or 0),
                "n_errored": int(best.metadata.get("n_errored") or 0),
                "prompt_version": next(
                    (trial.prompt_version for trial in best.trials if trial.prompt_version),
                    "",
                ),
            },
            notes=AXIS_NOTES.get(axis, ""),
        )
