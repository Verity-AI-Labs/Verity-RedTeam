"""Judge recall on known-hack trajectories, isolated from attacker strength.

A weak attacker (never finds hacks) and a weak judge (fails to recognize a
hack it is handed) look the same on a live audit. This harness never runs the
attacker or a container: it feeds pre-recorded exploits to the judge and
scores whether each is labeled a hack.

Host gold is resolved the same way the probe does (``resolve_gold`` on an
env-like object whose ``entry`` carries ``env_root``), because judging these
trajectories correctly may depend on gold comparison.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from verity_redteam.analysis.hackability import DEFAULT_CONFIDENCE, clopper_pearson
from verity_redteam.gold import (
    gold_equivalent,
    resolve_gold,
    trial_commands,
    trial_from_recorded_hack,
)
from verity_redteam.judge import Judge, Judgment
from verity_redteam.outcomes import HACK_OUTCOMES, is_hack
from verity_redteam.types import AttackTrial
from verity_redteam.validation.solve_fixtures import legitimate_solve_fixtures

logger = logging.getLogger(__name__)

__all__ = [
    "FalsePositiveReport",
    "JudgeMiss",
    "JudgeValidationReport",
    "KnownHackCase",
    "collect_known_hack_cases",
    "evaluate_judge",
    "host_gold_env",
    "score_known_hacks",
    "score_legitimate_solve_fixtures",
]


@dataclass(slots=True)
class KnownHackCase:
    """One recorded exploit plus the host gold the judge would see in production."""

    env_id: str
    task_id: str
    run_id: str
    trial: AttackTrial
    gold: str | None
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    source_path: str = ""
    gold_present: bool = False


@dataclass(slots=True)
class JudgeMiss:
    """A known hack the judge called ``legitimate_solve`` or ``no_reward``."""

    env_id: str
    task_id: str
    run_id: str
    outcome: str
    rationale: str
    trajectory: list[dict[str, Any]]
    executed_commands: list[str]
    gold_present: bool
    gold_match: bool
    judgment: dict[str, Any]
    source_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "env_id": self.env_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "outcome": self.outcome,
            "rationale": self.rationale,
            "trajectory": [dict(item) for item in self.trajectory],
            "executed_commands": list(self.executed_commands),
            "gold_present": self.gold_present,
            "gold_match": self.gold_match,
            "judgment": dict(self.judgment),
            "source_path": self.source_path,
        }


@dataclass(slots=True)
class FalsePositiveReport:
    """Whether the judge invents hacks on the legitimate-solve fixtures."""

    n: int
    n_called_hack: int
    passed: bool
    failures: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "n_called_hack": self.n_called_hack,
            "passed": self.passed,
            "failures": [dict(item) for item in self.failures],
        }


@dataclass(slots=True)
class JudgeValidationReport:
    """Two-sided judge check: known-hack recall plus legitimate-solve false positives."""

    benchmark: str
    n_known_hacks: int
    n_caught: int
    n_missed: int
    recall: float
    recall_ci_lower: float
    recall_ci_upper: float
    confidence: float
    misses: list[JudgeMiss]
    false_positives: FalsePositiveReport
    n_tasks: int = 0
    n_tasks_with_hacks: int = 0
    n_tasks_absent: int = 0
    n_tasks_missing_gold: int = 0
    n_scored: int = 0

    @property
    def passed(self) -> bool:
        """True when we scored known hacks and the solve fixtures did not fire."""
        return self.n_known_hacks > 0 and self.false_positives.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark": self.benchmark,
            "n_known_hacks": self.n_known_hacks,
            "n_caught": self.n_caught,
            "n_missed": self.n_missed,
            "n_scored": self.n_scored,
            "recall": self.recall,
            "recall_ci_lower": self.recall_ci_lower,
            "recall_ci_upper": self.recall_ci_upper,
            "confidence": self.confidence,
            "misses": [miss.to_dict() for miss in self.misses],
            "false_positives": self.false_positives.to_dict(),
            "n_tasks": self.n_tasks,
            "n_tasks_with_hacks": self.n_tasks_with_hacks,
            "n_tasks_absent": self.n_tasks_absent,
            "n_tasks_missing_gold": self.n_tasks_missing_gold,
            "passed": self.passed,
        }

    def to_text(self) -> str:
        lines = [
            f"judge validation: {self.benchmark}",
            (
                f"known hacks: {self.n_known_hacks}  scored: {self.n_scored}  "
                f"tasks: {self.n_tasks_with_hacks}/{self.n_tasks} with exploits  "
                f"absent: {self.n_tasks_absent}  missing gold: {self.n_tasks_missing_gold}"
            ),
            (
                f"recall: {self.recall:.3f}  "
                f"{int(self.confidence * 100)}% CI "
                f"[{self.recall_ci_lower:.3f}, {self.recall_ci_upper:.3f}]  "
                f"caught={self.n_caught} missed={self.n_missed}"
            ),
        ]
        fp = self.false_positives
        fp_status = "PASS" if fp.passed else "FAIL"
        lines.append(
            f"false-positive fixtures: {fp.n}  hacks-called: {fp.n_called_hack}  {fp_status}"
        )
        overall = "PASS" if self.passed else "FAIL"
        lines.append(f"two-sided: {overall}")
        if self.misses:
            lines.append("")
            lines.append(f"misses ({len(self.misses)}):")
            for miss in self.misses:
                lines.append(_format_miss(miss))
        if fp.failures:
            lines.append("")
            lines.append("false-positive failures:")
            for failure in fp.failures:
                lines.append(
                    f"  FIXTURE {failure.get('fixture_id')}  "
                    f"outcome={failure.get('outcome')}  "
                    f"rationale={failure.get('rationale')}"
                )
        return "\n".join(lines)


def _format_miss(miss: JudgeMiss) -> str:
    commands = "\n".join(f"    {command}" for command in miss.executed_commands) or "    (none)"
    trajectory = (
        "\n".join(
            f"    {item.get('role', '')}: {item.get('content', '')}" for item in miss.trajectory
        )
        or "    (empty)"
    )
    return (
        f"  MISS env={miss.env_id} task={miss.task_id} run={miss.run_id} "
        f"outcome={miss.outcome} gold_present={miss.gold_present} gold_match={miss.gold_match}\n"
        f"    source: {miss.source_path}\n"
        f"    executed:\n{commands}\n"
        f"    trajectory:\n{trajectory}\n"
        f"    rationale: {miss.rationale}"
    )


def host_gold_env(entry: dict[str, Any] | None, env_root: Path | str | None = None) -> Any:
    """Env-like object ``resolve_gold`` already consumes: host ``env_root``, no sandbox."""
    payload = dict(entry or {})
    if env_root is not None:
        payload["env_root"] = str(env_root)
    return SimpleNamespace(entry=payload)


def score_known_hacks(
    cases: Sequence[KnownHackCase],
    judge: Judge,
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    benchmark: str = "",
) -> JudgeValidationReport:
    """Run the judge on known hacks and compute recall with a Clopper-Pearson CI."""
    misses: list[JudgeMiss] = []
    n_caught = 0
    n_missing_gold = 0
    seen_tasks: set[str] = set()
    missing_gold_tasks: set[str] = set()
    for case in cases:
        seen_tasks.add(case.task_id or case.env_id)
        if not case.gold_present:
            n_missing_gold += 1
            missing_gold_tasks.add(case.task_id or case.env_id)
        judgment = judge.judge(case.trial, case.gold)
        if is_hack(judgment.outcome):
            n_caught += 1
            continue
        misses.append(_miss_from(case, judgment))
    n = len(cases)
    recall = 0.0 if n == 0 else n_caught / n
    lower, upper = clopper_pearson(n_caught, n, confidence)
    empty_fp = FalsePositiveReport(n=0, n_called_hack=0, passed=True)
    return JudgeValidationReport(
        benchmark=benchmark,
        n_known_hacks=n,
        n_caught=n_caught,
        n_missed=len(misses),
        recall=recall,
        recall_ci_lower=lower,
        recall_ci_upper=upper,
        confidence=confidence,
        misses=misses,
        false_positives=empty_fp,
        n_tasks=len(seen_tasks),
        n_tasks_with_hacks=len(seen_tasks),
        n_tasks_missing_gold=len(missing_gold_tasks),
        n_scored=n,
    )


def _miss_from(case: KnownHackCase, judgment: Judgment) -> JudgeMiss:
    evidence = case.trial.evidence if isinstance(case.trial.evidence, dict) else {}
    return JudgeMiss(
        env_id=case.env_id,
        task_id=case.task_id,
        run_id=case.run_id,
        outcome=judgment.outcome,
        rationale=judgment.rationale,
        trajectory=[dict(item) for item in case.trajectory or case.trial.messages],
        executed_commands=trial_commands(case.trial),
        gold_present=case.gold_present,
        gold_match=bool(evidence.get("gold_match", False)),
        judgment=judgment.to_dict(),
        source_path=case.source_path,
    )


def score_legitimate_solve_fixtures(judge: Judge) -> FalsePositiveReport:
    """Run the judge on the suite's legitimate-solve fixtures. Hacks here are false positives."""
    failures: list[dict[str, Any]] = []
    fixtures = legitimate_solve_fixtures()
    for fixture in fixtures:
        trial = fixture.trial()
        judgment = judge.judge(trial, fixture.gold)
        if judgment.outcome not in HACK_OUTCOMES:
            continue
        failures.append(
            {
                "fixture_id": fixture.fixture_id,
                "env_id": fixture.env_id,
                "outcome": judgment.outcome,
                "rationale": judgment.rationale,
                "trajectory": fixture.messages(),
                "judgment": judgment.to_dict(),
            }
        )
    n = len(fixtures)
    return FalsePositiveReport(
        n=n,
        n_called_hack=len(failures),
        passed=not failures,
        failures=failures,
    )


def evaluate_judge(
    cases: Sequence[KnownHackCase],
    judge: Judge,
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    benchmark: str = "",
    n_tasks: int = 0,
    n_tasks_absent: int = 0,
) -> JudgeValidationReport:
    """Known-hack recall plus the legitimate-solve false-positive fixtures."""
    report = score_known_hacks(cases, judge, confidence=confidence, benchmark=benchmark)
    report.false_positives = score_legitimate_solve_fixtures(judge)
    if n_tasks:
        report.n_tasks = n_tasks
    report.n_tasks_absent = n_tasks_absent
    return report


def _entry_id(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("id") or "")
    return str(getattr(entry, "id", "") or "")


def _task_id(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("name") or entry.get("id") or "")
    return str(getattr(entry, "name", None) or getattr(entry, "id", "") or "")


def _trajectories_of(loaded: Any) -> list[Any]:
    if loaded is None:
        return []
    if isinstance(loaded, dict):
        if not loaded.get("present", True):
            return []
        return list(loaded.get("trajectories") or [])
    if not getattr(loaded, "present", True):
        return []
    return list(getattr(loaded, "trajectories", None) or [])


def _hack_run_id(hack: Any) -> str:
    if isinstance(hack, dict):
        return str(hack.get("run_id") or "")
    return str(getattr(hack, "run_id", "") or "")


def _hack_source_path(hack: Any) -> str:
    if isinstance(hack, dict):
        return str(hack.get("source_path") or "")
    return str(getattr(hack, "source_path", "") or "")


def collect_known_hack_cases(
    entries: Sequence[Any],
    *,
    cache_dir: Path,
    load_hacks: Callable[..., Any] | None = None,
    gold_for: Callable[[Any, Path | None], str | None] | None = None,
    manifests: Sequence[dict[str, Any]] | None = None,
    fetch: bool = True,
) -> tuple[list[KnownHackCase], int]:
    """Load recorded exploits and host gold. Never starts a container or attacker.

    ``load_hacks(entry, cache_dir, env_root)`` defaults to Corpus's
    ``load_hack_trajectories``. ``gold_for`` defaults to :func:`resolve_gold`
    on a host-only env. Returns ``(cases, n_tasks_absent)``.
    """
    from verity_redteam.corpus import fetch_env_root, load_recorded_hacks, resolve_to_core

    loader = load_hacks if load_hacks is not None else load_recorded_hacks
    cases: list[KnownHackCase] = []
    n_absent = 0
    manifest_by_index = list(manifests) if manifests is not None else []
    for index, entry in enumerate(entries):
        env_id = _entry_id(entry)
        task_id = _task_id(entry)
        manifest: dict[str, Any] | None = None
        if index < len(manifest_by_index):
            manifest = dict(manifest_by_index[index])
        env_root: Path | None = None
        if manifest and manifest.get("env_root"):
            env_root = Path(str(manifest["env_root"]))
        elif fetch and not isinstance(entry, dict):
            try:
                env_root = fetch_env_root(entry, cache_dir)
                manifest = resolve_to_core(entry, cache_dir)
            except (ValueError, TypeError) as exc:
                logger.info("skipping fetch for %s: %s", env_id, exc)
        if gold_for is not None:
            gold = gold_for(entry, env_root)
        else:
            gold = resolve_gold(host_gold_env(manifest, env_root))
        try:
            loaded = loader(entry, cache_dir, env_root)
        except TypeError:
            loaded = loader(entry, cache_dir=cache_dir, env_root=env_root)
        trajectories = _trajectories_of(loaded)
        if not trajectories:
            n_absent += 1
            message = ""
            if loaded is not None:
                message = (
                    loaded.get("message")
                    if isinstance(loaded, dict)
                    else str(getattr(loaded, "message", "") or "")
                )
            logger.info(
                "no recorded hacks env_id=%s task_id=%s message=%s",
                env_id,
                task_id,
                message,
            )
            continue
        for hack in trajectories:
            trial = trial_from_recorded_hack(hack, env_id=env_id, reward_passed=True)
            trial.evidence["gold_match"] = gold_equivalent(trial, gold)
            cases.append(
                KnownHackCase(
                    env_id=env_id,
                    task_id=task_id,
                    run_id=_hack_run_id(hack),
                    trial=trial,
                    gold=gold,
                    trajectory=list(trial.messages),
                    source_path=_hack_source_path(hack),
                    gold_present=gold is not None,
                )
            )
    return cases, n_absent
