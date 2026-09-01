"""Tests for the verity-redteam CLI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from verity_core.scorecard import Scorecard

from verity_redteam.cli import EXIT_ERROR, EXIT_OK, _load_entries, build_parser, main


def test_cli_imports_core_helpers_from_the_package_root() -> None:
    import verity_core

    for name in ("configure_logging", "load_corpus", "load_scorecards"):
        assert name in verity_core.__all__
        assert callable(getattr(verity_core, name))


def test_parser_requires_a_command() -> None:
    parser = build_parser()
    assert parser.parse_args(["run", "env-1"]).command == "run"
    assert parser.parse_args(["batch", "--domain", "code", "--resume"]).resume is True
    assert parser.parse_args(["report", "--results-dir", "results"]).command == "report"


def test_report_lists_v1_from_scorecards(tmp_path: Path, capsys: Any) -> None:
    card = Scorecard(env_id="corpus/task-1")
    card.set_axis(
        "V1",
        0.25,
        "verity-redteam",
        evidence={"n_trials": 8, "n_successes": 2, "strategy": "freeform"},
    )
    (tmp_path / "corpus__task-1.json").write_text(json.dumps(card.to_dict()), encoding="utf-8")
    code = main(["report", "--results-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "corpus/task-1" in captured.out
    assert "0.250" in captured.out
    assert "freeform" in captured.out


def test_report_json(tmp_path: Path, capsys: Any) -> None:
    card = Scorecard(env_id="e")
    card.set_axis("V1", 0.0, "verity-redteam", evidence={"n_trials": 8, "n_successes": 0})
    (tmp_path / "e.json").write_text(json.dumps(card.to_dict()), encoding="utf-8")
    code = main(["--log-level", "error", "report", "--results-dir", str(tmp_path), "--json"])
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["scorecards"][0]["env_id"] == "e"
    assert payload["scorecards"][0]["v1"] == 0.0


def test_report_missing_dir_is_an_error(tmp_path: Path) -> None:
    code = main(["report", "--results-dir", str(tmp_path / "nope")])
    assert code == EXIT_ERROR


def test_run_audits_one_corpus_entry(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    from tests.conftest import FakeClient, FakeEnv, make_spec

    manifest = tmp_path / "manifests"
    manifest.mkdir()
    (manifest / "task.yaml").write_text(
        "id: corpus/task-1\nformat: verifiers\ndomain: code\ninstructions: do it\n",
        encoding="utf-8",
    )
    spec = make_spec()
    env = FakeEnv(spec=spec, gold=None, passing="bypass")
    client = FakeClient(contents="bypass")

    monkeypatch.setattr("verity_redteam.runner.load_env", lambda entry, **kwargs: env)

    class _ClientCtx:
        def __enter__(self) -> FakeClient:
            return client

        def __exit__(self, *args: object) -> None:
            return None

        @classmethod
        def from_config(cls, config: object, **kwargs: object) -> _ClientCtx:
            return cls()

    monkeypatch.setattr("verity_redteam.cli.ModelClient", _ClientCtx)

    config = tmp_path / "verity.yaml"
    config.write_text(
        f"results_dir: {json.dumps(str(tmp_path / 'core-results'))}\n"
        f"redteam:\n"
        f"  vrc_dir: {json.dumps(str(tmp_path / 'vrc'))}\n",
        encoding="utf-8",
    )

    results = tmp_path / "results"
    code = main(
        [
            "--config",
            str(config),
            "run",
            "corpus/task-1",
            "--corpus",
            str(manifest),
            "--results-dir",
            str(results),
        ]
    )
    assert code == EXIT_OK
    assert env.closed == 1
    assert "V1" in capsys.readouterr().out
    written = list(results.glob("*.json"))
    assert len(written) == 1


class TestLoadEntries:
    def test_core_flat_manifests_load_without_the_registry(self, tmp_path: Path) -> None:
        (tmp_path / "task.yaml").write_text(
            "id: corpus/task-1\nformat: verifiers\ndomain: code\ninstructions: do it\n",
            encoding="utf-8",
        )
        entries = _load_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0]["id"] == "corpus/task-1"
        assert entries[0]["format"] == "verifiers"

    def test_falls_back_to_the_corpus_registry(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "bench.yaml").write_text(
            """
source_defaults:
  type: local
  path: .
entries:
  - name: Registry Env
    domain: code
    adapter: verifiers
    metadata:
      instructions: from the registry
""",
            encoding="utf-8",
        )
        with caplog.at_level(logging.INFO, logger="verity_redteam.cli"):
            entries = _load_entries(tmp_path)
        assert any(
            "core manifest layout not found, trying corpus registry" in rec.getMessage()
            for rec in caplog.records
        )
        assert len(entries) == 1
        assert entries[0]["format"] == "verifiers"
        assert entries[0]["instructions"] == "from the registry"

    def test_neither_layout_raises_a_combined_error(self, tmp_path: Path) -> None:
        (tmp_path / "noise.yaml").write_text("{}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="could not load corpus") as caught:
            _load_entries(tmp_path)
        message = str(caught.value)
        assert "core:" in message
        assert "registry: no entries found" in message

    def test_non_structural_load_corpus_errors_propagate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            raise RuntimeError("disk failed")

        monkeypatch.setattr("verity_redteam.cli.load_corpus", boom)
        with pytest.raises(RuntimeError, match="disk failed"):
            _load_entries(tmp_path)
