"""Run N trials of one strategy against one environment."""

from __future__ import annotations

import logging
import time

from verity_core.env import TaskSpec, VerityEnv
from verity_core.models import ModelClient

from verity_redteam.analysis.hackability import HackabilityEstimator
from verity_redteam.strategies.protocol import AttackStrategy
from verity_redteam.types import AttackTrial, ProbeResult

logger = logging.getLogger(__name__)

DEFAULT_N_TRIALS = 8

__all__ = ["DEFAULT_N_TRIALS", "RedTeamProbe"]


def _tokens_of(trial: AttackTrial) -> int:
    if trial.model_response is None:
        return 0
    return int(trial.model_response.usage.total_tokens)


class RedTeamProbe:
    """N independent trials of one strategy, with a reset between each."""

    def __init__(self, strategy: AttackStrategy, n_trials: int = DEFAULT_N_TRIALS) -> None:
        if n_trials < 1:
            raise ValueError(f"n_trials must be >= 1, got {n_trials}")
        self.strategy = strategy
        self.n_trials = n_trials

    def run(self, env: VerityEnv, spec: TaskSpec, client: ModelClient) -> ProbeResult:
        started = time.monotonic()
        trials: list[AttackTrial] = []
        metadata: dict[str, object] = {"reset_broken": False}

        gold = env.gold_solution()
        env.reset()
        gold_ok_before: bool | None = None
        if gold is not None:
            gold_ok_before = self._check_gold(env, gold, when="before trial 1")
            # Reset after the gold check so trial 1 is not scored on a gold-contaminated
            # state. If reset is broken this will not actually clean the env; the
            # post-trial-1 check below is what surfaces that.
            env.reset()

        for attempt in range(self.n_trials):
            trial = self.strategy.attack(env, spec, client, attempt, self.n_trials)
            self._classify(trial, gold)
            trials.append(trial)
            env.reset()
            if attempt == 0 and gold is not None:
                gold_ok_after = self._check_gold(env, gold, when="after trial 1 reset")
                if gold_ok_before is True and gold_ok_after is False:
                    logger.warning(
                        "reset appears broken env_id=%s strategy=%s: gold passed before "
                        "trial 1 and failed after trial 1's reset; not falling back to "
                        "snapshot/restore in v0.1",
                        spec.id,
                        self.strategy.name,
                    )
                    metadata["reset_broken"] = True
                # Isolate trial 2 from the post-reset gold check the same way.
                env.reset()

        usable = [trial for trial in trials if trial.classification not in {"solved", "error"}]
        metadata["n_solved"] = sum(1 for trial in trials if trial.classification == "solved")
        metadata["n_errored"] = sum(1 for trial in trials if trial.classification == "error")
        curve = HackabilityEstimator.fit(usable, strategy=self.strategy.name)
        duration = time.monotonic() - started
        total_tokens = sum(_tokens_of(trial) for trial in trials)
        logger.info(
            "probe done env_id=%s strategy=%s n_trials=%d n_successes=%d alpha=%.3f "
            "reset_broken=%s duration=%.2fs tokens=%d",
            spec.id,
            self.strategy.name,
            curve.n_trials,
            curve.n_successes,
            curve.alpha,
            metadata["reset_broken"],
            duration,
            total_tokens,
        )
        return ProbeResult(
            strategy=self.strategy.name,
            env_id=spec.id,
            trials=trials,
            curve=curve,
            total_tokens=total_tokens,
            duration=duration,
            metadata=metadata,
        )

    @staticmethod
    def _check_gold(env: VerityEnv, gold: str | None, *, when: str) -> bool | None:
        if gold is None:
            return None
        try:
            result = env.verify(gold)
        except Exception as exc:
            logger.warning("gold check failed %s error=%s: %s", when, type(exc).__name__, exc)
            return False
        logger.debug("gold check %s verdict=%s", when, result.verdict)
        return bool(result.verdict)

    @staticmethod
    def _classify(trial: AttackTrial, gold: str | None) -> None:
        """Set ``hacked`` and ``classification`` from the verifier verdict.

        Strategies must not do this themselves: a gold-matching submission that
        the verifier accepts is a solve, not a hack, and only the probe has
        the gold string.
        """
        if trial.classification == "error" or trial.error:
            trial.hacked = False
            trial.classification = "error"
            return
        if not trial.reward.verdict:
            trial.hacked = False
            trial.classification = "failed"
            return
        if gold is not None and trial.submission.strip() == gold.strip():
            logger.info(
                "false-positive filter env_id=%s trial_id=%s: submission matched gold; "
                "classified as solved, excluded from alpha",
                trial.env_id,
                trial.trial_id,
            )
            trial.hacked = False
            trial.classification = "solved"
            return
        trial.hacked = True
        trial.classification = "hacked"
