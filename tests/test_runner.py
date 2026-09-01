"""Tests for RedTeamRunner scorecard output."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from tests.conftest import FakeClient, FakeEnv, make_spec
from verity_core.scorecard import AXES

from verity_redteam.runner import TOOL_NAME, RedTeamRunner
from verity_redteam.strategies.freeform import FreeformHackStrategy


def _runner(tmp_path: Path, env: FakeEnv, client: FakeClient, n_trials: int = 4) -> RedTeamRunner:
    strategy = FreeformHackStrategy(model="test-model")
    return RedTeamRunner(client, [strategy], n_trials=n_trials, vrc_dir=tmp_path / "vrc")


class TestAudit:
    def test_sets_v1_to_alpha_and_closes_the_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None, passing="bypass")
        client = FakeClient(contents=["bypass", "miss", "bypass", "miss"])
        monkeypatch.setattr("verity_redteam.runner.load_env", lambda entry, **kwargs: env)
        card = _runner(tmp_path, env, client).audit({"id": spec.id, "format": "verifiers"})
        assert env.closed == 1
        v1 = card.get_axis("V1")
        assert v1.value == 0.5
        assert v1.tool == TOOL_NAME
        assert v1.scored is True
        assert v1.evidence["n_trials"] == 4
        assert v1.evidence["n_successes"] == 2
        assert v1.evidence["strategy"] == "freeform"
        assert v1.evidence["prompt_version"] == "v2"
        assert set(v1.evidence["curve"]) == {1, 2, 4, 8}
        assert v1.evidence["serious_alpha"] == 0.5
        assert v1.evidence["n_serious"] == 2
        assert len(v1.evidence["trial_ids"]) == 2
        assert card.metadata["audited_by"] == TOOL_NAME
        assert card.metadata["precheck_failed"] is False
        assert set(card.axes) == set(AXES)

    def test_unscored_v1_when_no_strategy_applies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None)
        client = FakeClient()

        class Skip:
            @property
            def name(self) -> str:
                return "skip"

            @property
            def target_axes(self) -> tuple[str, ...]:
                return ("V1",)

            def applies(self, spec: object) -> bool:
                return False

            def attack(self, *args: object, **kwargs: object) -> None:
                raise AssertionError("skipped strategy must not run")

        monkeypatch.setattr("verity_redteam.runner.load_env", lambda entry, **kwargs: env)
        runner = RedTeamRunner(client, [Skip()], n_trials=2, vrc_dir=tmp_path / "vrc")
        card = runner.audit({"id": spec.id, "format": "verifiers"})
        assert card.get_axis("V1").value is None
        assert card.get_axis("V1").scored is False
        assert env.closed == 1

    def test_closes_the_env_when_the_audit_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = FakeEnv()

        def boom() -> None:
            raise RuntimeError("spec failed")

        env.spec = boom  # type: ignore[method-assign]
        monkeypatch.setattr("verity_redteam.runner.load_env", lambda entry, **kwargs: env)
        runner = _runner(tmp_path, env, FakeClient())
        with contextlib.suppress(RuntimeError):
            runner.audit({"id": "x", "format": "verifiers"})
        assert env.closed == 1

    def test_logs_vrc_for_successful_hacks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None, passing="bypass")
        client = FakeClient(contents="bypass")
        monkeypatch.setattr("verity_redteam.runner.load_env", lambda entry, **kwargs: env)
        _runner(tmp_path, env, client, n_trials=2).audit({"id": spec.id, "format": "verifiers"})
        # two identical submissions: in-memory dedup keeps one
        written = list((tmp_path / "vrc").rglob("*.json"))
        assert len(written) == 1

    def test_v1_is_the_max_alpha_across_strategies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold=None, passing=frozenset({"a", "b"}))
        # First strategy always misses; second always hits. max alpha should be 1.0.
        client = FakeClient(contents=["miss", "miss", "a", "a"])
        low = FreeformHackStrategy(model="test-model")
        high = FreeformHackStrategy(model="test-model")
        monkeypatch.setattr("verity_redteam.runner.load_env", lambda entry, **kwargs: env)
        runner = RedTeamRunner(client, [low, high], n_trials=2, vrc_dir=tmp_path / "vrc")
        card = runner.audit({"id": spec.id, "format": "verifiers"})
        assert card.get_axis("V1").value == 1.0
        assert card.get_axis("V1").evidence["strategy"] == "freeform"

    def test_unscored_v1_when_precheck_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = make_spec()
        env = FakeEnv(spec=spec, gold="GOLD", passing="bypass", gold_fails_after_reset=True)
        client = FakeClient(contents="bypass")
        monkeypatch.setattr("verity_redteam.runner.load_env", lambda entry, **kwargs: env)
        card = _runner(tmp_path, env, client, n_trials=2).audit(
            {"id": spec.id, "format": "verifiers"}
        )
        assert card.get_axis("V1").value is None
        assert card.get_axis("V1").scored is False
        assert card.metadata["precheck_failed"] is True
        assert client.calls == []
