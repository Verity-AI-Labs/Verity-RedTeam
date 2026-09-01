"""Tests for the verity-redteam CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from verity_core.scorecard import Scorecard

from verity_redteam.cli import EXIT_ERROR, EXIT_OK, build_parser, main


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

    results = tmp_path / "results"
    code = main(
        [
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
