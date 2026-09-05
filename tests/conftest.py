"""Shared fakes for unit tests. No Docker, no live model server."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from verity_core.env import Observation, RewardResult, StepResult, TaskSpec
from verity_core.models import ModelError, ModelResponse, TokenUsage
from verity_core.runner import ExecResult

REPO_ROOT = Path(__file__).resolve().parent.parent
REDTEAM_NAMESPACE = "verity_redteam"
CORE_NAMESPACE = "verity_core"


def _files_under(directory: Path) -> set[Path]:
    if not directory.exists():
        return set()
    return {path for path in directory.rglob("*") if path.is_file()}


@pytest.fixture(scope="session", autouse=True)
def fail_if_vrc_leaks_into_the_repo() -> Iterator[None]:
    """Fail the suite if any test writes VRC or trajectory files next to the repo."""
    watched = (REPO_ROOT / "vrc", REPO_ROOT / "trajectories")
    before = {path: _files_under(path) for path in watched}
    yield
    leaked_parts: list[str] = []
    for path in watched:
        leaked = _files_under(path) - before[path]
        leaked_parts.extend(sorted(str(item.relative_to(REPO_ROOT)) for item in leaked))
    if leaked_parts:
        relative = ", ".join(leaked_parts)
        pytest.fail(f"tests wrote audit files into the repo working directory: {relative}")


@pytest.fixture(autouse=True)
def isolate_logging() -> Iterator[None]:
    loggers = [logging.getLogger(CORE_NAMESPACE), logging.getLogger(REDTEAM_NAMESPACE)]
    snapshots = [(log.handlers[:], log.level, log.propagate) for log in loggers]
    try:
        yield
    finally:
        for log, (handlers, level, propagate) in zip(loggers, snapshots, strict=True):
            log.handlers = handlers
            log.setLevel(level)
            log.propagate = propagate


def make_spec(**overrides: Any) -> TaskSpec:
    fields: dict[str, Any] = {
        "id": "corpus/task-1",
        "domain": "code",
        "source": "https://github.com/example/tasks",
        "commit": "0f1e2d3",
        "reward_type": "binary",
        "instructions": "Fix the failing test.",
        "has_gold": True,
    }
    fields.update(overrides)
    return TaskSpec(**fields)


class FakeEnv:
    """Minimal VerityEnv: verify returns a canned verdict, reset is counted."""

    def __init__(
        self,
        *,
        spec: TaskSpec | None = None,
        gold: str | None = "the-gold-solution",
        passing: str | frozenset[str] | None = None,
        verify_error: Exception | None = None,
        gold_fails_after_reset: bool = False,
        error_submissions: str | frozenset[str] | None = None,
        rewards: dict[str, RewardResult] | None = None,
    ) -> None:
        self._spec = spec or make_spec(has_gold=gold is not None)
        self._gold = gold
        self._passing = passing if passing is not None else frozenset()
        if isinstance(self._passing, str):
            self._passing = frozenset({self._passing})
        self.verify_error = verify_error
        self.gold_fails_after_reset = gold_fails_after_reset
        self.error_submissions: frozenset[str] = frozenset()
        if isinstance(error_submissions, str):
            self.error_submissions = frozenset({error_submissions})
        elif error_submissions is not None:
            self.error_submissions = frozenset(error_submissions)
        self.verify_calls: list[str] = []
        self.reset_count = 0
        self.closed = 0
        self._gold_checks = 0
        self.runner = None
        self.entry: dict[str, Any] | None = None
        self._rewards = dict(rewards or {})

    def spec(self) -> TaskSpec:
        return self._spec

    def reset(self) -> Observation:
        self.reset_count += 1
        return Observation(text="start")

    def step(self, action: str) -> StepResult:
        runner = getattr(self, "runner", None)
        if runner is not None and callable(getattr(runner, "exec", None)):
            result = runner.exec(action)
            return StepResult(
                Observation(
                    text=result.stdout or result.stderr,
                    metadata={"exit_code": result.exit_code, "timed_out": result.timed_out},
                ),
                0.0,
                False,
                info=result.to_dict(),
            )
        return StepResult(Observation(text=action), 0.0, False)

    def verify(self, submission: str) -> RewardResult:
        self.verify_calls.append(submission)
        if self._gold is not None and submission.strip() == self._gold.strip():
            self._gold_checks += 1
            # The second gold check is the one after trial 1's reset.
            if self.gold_fails_after_reset and self._gold_checks >= 2:
                return RewardResult(0.0, False, "gold failed after reset")
            return RewardResult(1.0, True, "gold ok")
        if self.verify_error is not None and (
            not self.error_submissions or submission in self.error_submissions
        ):
            raise self.verify_error
        passed = submission in self._passing
        if submission in self._rewards:
            return self._rewards[submission]
        return RewardResult(1.0 if passed else 0.0, passed, "ok" if passed else "fail")

    def gold_solution(self) -> str | None:
        return self._gold

    def snapshot(self) -> bytes:
        return b"{}"

    def restore(self, snap: bytes) -> None:
        return None

    def close(self) -> None:
        self.closed += 1

    def __enter__(self) -> FakeEnv:
        self.reset()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


@dataclass(slots=True)
class FakeRunner:
    """SandboxRunner.exec stand-in for container-backed FakeEnv tests.

    Core's real runner is ``@dataclass(slots=True)``, so ``runner.exec = ...``
    raises ``AttributeError``. This double is slotted the same way so the
    suite catches that class of bug instead of a live container run.
    """

    seen: list[str] = field(default_factory=list)

    def exec(
        self,
        command: str,
        *,
        timeout: int | None = None,
        workdir: str | None = None,
        user: str | None = None,
    ) -> ExecResult:
        self.seen.append(command)
        return ExecResult(
            exit_code=0,
            stdout=f"observed:{command}",
            stderr="",
            duration_seconds=0.01,
            command=command,
        )


class FakeClient:
    """Duck-typed ModelClient: canned completions, records use_cache."""

    def __init__(
        self,
        contents: str | list[str] = "not-a-solution",
        *,
        error: Exception | None = None,
        model: str = "test-model",
    ) -> None:
        self.contents = [contents] if isinstance(contents, str) else list(contents)
        self.error = error
        self.model = model
        self.calls: list[dict[str, Any]] = []
        self._i = 0
        self._total_usage = TokenUsage()

    @property
    def total_usage(self) -> TokenUsage:
        return self._total_usage

    def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        *,
        use_cache: bool = True,
        **kwargs: Any,
    ) -> ModelResponse:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "use_cache": use_cache,
                "kwargs": kwargs,
            }
        )
        if self.error is not None:
            raise self.error if isinstance(self.error, Exception) else ModelError(str(self.error))
        content = self.contents[min(self._i, len(self.contents) - 1)]
        self._i += 1
        usage = TokenUsage(prompt_tokens=4, completion_tokens=2, total_tokens=6)
        self._total_usage = self._total_usage + usage
        return ModelResponse(content=content, model=model, usage=usage)


@pytest.fixture
def spec() -> TaskSpec:
    return make_spec()


@pytest.fixture
def fake_env() -> FakeEnv:
    return FakeEnv()


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()
