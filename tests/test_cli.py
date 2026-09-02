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
    assert parser.parse_args(["run", "env-1", "--dry-run"]).dry_run is True
    assert parser.parse_args(["batch", "--dry-run"]).dry_run is True
    assert parser.parse_args(["batch", "--domain", "code", "--resume"]).resume is True
    assert parser.parse_args(["vrc", "list", "corpus/task-1"]).command == "vrc"
    assert parser.parse_args(["report", "--corpus"]).corpus is True
    parsed = parser.parse_args(["validate", "--benchmark", "terminal-wrench", "--dry-run"])
    assert parsed.command == "validate"
    assert parsed.benchmark == "terminal-wrench"
    assert parsed.dry_run is True


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
    assert payload["scorecards"][0]["precheck_failed"] is False


def test_report_flags_a_failed_precheck(tmp_path: Path, capsys: Any) -> None:
    card = Scorecard(env_id="corpus/task-1", metadata={"precheck_failed": True})
    card.set_axis(
        "V1",
        0.5,
        "verity-redteam",
        evidence={"n_trials": 2, "n_successes": 1, "strategy": "freeform"},
    )
    (tmp_path / "corpus__task-1.json").write_text(json.dumps(card.to_dict()), encoding="utf-8")
    code = main(["report", "--results-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "[precheck-failed]" in captured.out
    assert "corpus/task-1" in captured.out


def test_report_missing_dir_is_an_error(tmp_path: Path) -> None:
    code = main(["report", "--results-dir", str(tmp_path / "nope")])
    assert code == EXIT_ERROR


def test_report_corpus_summarizes_alpha_and_categories(tmp_path: Path, capsys: Any) -> None:
    card = Scorecard(env_id="env-a", metadata={"domain": "code"})
    card.set_axis(
        "V1",
        0.5,
        "verity-redteam",
        evidence={
            "n_trials": 8,
            "n_successes": 4,
            "strategy": "freeform",
            "serious_alpha": 0.5,
            "observed_categories": ["output-spoofing"],
        },
    )
    (tmp_path / "env-a.json").write_text(json.dumps(card.to_dict()), encoding="utf-8")
    code = main(["report", "--results-dir", str(tmp_path), "--corpus"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "Alpha distribution" in captured.out
    assert "By domain" in captured.out
    assert "output-spoofing" in captured.out
    assert "env-a" in captured.out
    assert "0.500" in captured.out


def _write_registry_manifest(
    directory: Path,
    *,
    name: str,
    relpath: str,
    instructions: str = "Fix the failing test.",
    domain: str = "code",
    adapter: str = "verifiers",
    filename: str | None = None,
    source_url: str = "https://github.com/example/tasks",
    commit: str = "0f1e2d3",
) -> str:
    from verity_corpus.models.manifest import SourceSpec, compute_entry_id

    directory.mkdir(parents=True, exist_ok=True)
    stem = filename or relpath.replace("/", "__")
    (directory / f"{stem}.yaml").write_text(
        "source_defaults:\n"
        "  type: git\n"
        f"  url: {source_url}\n"
        f"  commit: {commit}\n"
        "entries:\n"
        f"  - name: {name}\n"
        f"    path: {relpath}\n"
        f"    domain: {domain}\n"
        f"    adapter: {adapter}\n"
        "    metadata:\n"
        f"      instructions: {instructions}\n",
        encoding="utf-8",
    )
    return compute_entry_id(SourceSpec(type="git", url=source_url, commit=commit, path=relpath))


def test_run_audits_one_corpus_entry(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    from tests.conftest import FakeClient, FakeEnv, make_spec

    manifest = tmp_path / "manifests"
    env_id = _write_registry_manifest(manifest, name="Task One", relpath="task-1")
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
            env_id,
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
    def test_prefers_the_corpus_registry(self, tmp_path: Path) -> None:
        env_id = _write_registry_manifest(
            tmp_path, name="Registry Env", relpath="task-1", instructions="from the registry"
        )
        entries = _load_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0]["id"] == env_id
        assert entries[0]["format"] == "verifiers"
        assert entries[0]["instructions"] == "from the registry"

    def test_skips_schema_yaml_and_does_not_call_load_corpus(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "_schema.yaml").write_text(
            "# Documented shape of a corpus manifest YAML file.\n"
            "source_defaults: {}\n"
            "entries: []\n",
            encoding="utf-8",
        )
        env_id = _write_registry_manifest(
            tmp_path, name="Real Env", relpath="real", filename="bench"
        )

        def boom(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            raise AssertionError("load_corpus must not run when CorpusRegistry is available")

        monkeypatch.setattr("verity_redteam.cli.load_corpus", boom)
        with caplog.at_level(logging.ERROR):
            entries = _load_entries(tmp_path)
        assert not any("_schema.yaml" in rec.getMessage() for rec in caplog.records)
        assert [entry["id"] for entry in entries] == [env_id]

    def test_falls_back_to_load_corpus_when_corpus_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "task.yaml").write_text(
            "id: corpus/task-1\nformat: verifiers\ndomain: code\ninstructions: do it\n",
            encoding="utf-8",
        )

        def no_registry(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            raise ImportError("verity-corpus is not installed")

        monkeypatch.setattr("verity_redteam.cli._load_entries_from_registry", no_registry)
        with caplog.at_level(logging.INFO, logger="verity_redteam.cli"):
            entries = _load_entries(tmp_path)
        assert any(
            "verity-corpus is not installed, using core load_corpus" in rec.getMessage()
            for rec in caplog.records
        )
        assert len(entries) == 1
        assert entries[0]["id"] == "corpus/task-1"
        assert entries[0]["format"] == "verifiers"

    def test_empty_registry_raises(self, tmp_path: Path) -> None:
        (tmp_path / "noise.yaml").write_text("{}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="could not load corpus") as caught:
            _load_entries(tmp_path)
        assert "no entries found" in str(caught.value)

    def test_non_structural_load_corpus_errors_propagate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_registry(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            raise ImportError("verity-corpus is not installed")

        def boom(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            raise RuntimeError("disk failed")

        monkeypatch.setattr("verity_redteam.cli._load_entries_from_registry", no_registry)
        monkeypatch.setattr("verity_redteam.cli.load_corpus", boom)
        with pytest.raises(RuntimeError, match="disk failed"):
            _load_entries(tmp_path)


def test_run_dry_run_prints_the_spec_and_skips_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    corpus = tmp_path / "manifests"
    env_id = _write_registry_manifest(
        corpus, name="Task One", relpath="task-1", instructions="A" * 250
    )

    def no_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not open a model client")

    def no_env(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not load an environment")

    monkeypatch.setattr("verity_redteam.cli.ModelClient.from_config", no_client)
    monkeypatch.setattr("verity_redteam.runner.load_env", no_env)

    code = main(["run", env_id, "--corpus", str(corpus), "--dry-run"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert f"id: {env_id}" in captured.out
    assert "domain: code" in captured.out
    assert "format: verifiers" in captured.out
    assert "source: https://github.com/example/tasks@0f1e2d3" in captured.out
    assert "instructions: " + ("A" * 200) in captured.out
    assert "A" * 201 not in captured.out


def test_batch_dry_run_prints_each_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    corpus = tmp_path / "manifests"
    id_one = _write_registry_manifest(corpus, name="Task One", relpath="task-1", instructions="one")
    id_two = _write_registry_manifest(corpus, name="Task Two", relpath="task-2", instructions="two")

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not open a model client")

    monkeypatch.setattr("verity_redteam.cli.ModelClient.from_config", boom)

    code = main(["batch", "--corpus", str(corpus), "--dry-run"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert f"id: {id_one}" in captured.out
    assert f"id: {id_two}" in captured.out
    assert "instructions: one" in captured.out
    assert "instructions: two" in captured.out


def test_vrc_list_prints_entries(tmp_path: Path, capsys: Any) -> None:
    from verity_corpus.models.vrc import VRCEntry

    vrc_dir = tmp_path / "vrc"
    entry = VRCEntry(
        env_id="corpus/task-1",
        exploit_type="freeform",
        trajectory=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first"},
            {"role": "user", "content": "Y" * 100},
        ],
        model_id="test-model",
    )
    entry.save(vrc_dir)
    config = tmp_path / "verity.yaml"
    config.write_text(
        f"redteam:\n  vrc_dir: {json.dumps(str(vrc_dir))}\n",
        encoding="utf-8",
    )
    code = main(["--config", str(config), "vrc", "list", "corpus/task-1"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert entry.id in captured.out
    assert "freeform" in captured.out
    assert ("Y" * 80) in captured.out
    assert ("Y" * 81) not in captured.out


def test_vrc_list_json_dumps_raw_entries(tmp_path: Path, capsys: Any) -> None:
    from verity_corpus.models.vrc import VRCEntry

    vrc_dir = tmp_path / "vrc"
    entry = VRCEntry(
        env_id="env1",
        exploit_type="freeform",
        trajectory=[{"role": "user", "content": "bypass"}],
        model_id="test-model",
    )
    entry.save(vrc_dir)
    config = tmp_path / "verity.yaml"
    config.write_text(
        f"redteam:\n  vrc_dir: {json.dumps(str(vrc_dir))}\n",
        encoding="utf-8",
    )
    code = main(["--config", str(config), "vrc", "list", "env1", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload[0]["id"] == entry.id
    assert payload[0]["exploit_type"] == "freeform"
    assert payload[0]["trajectory"][0]["content"] == "bypass"


_TW_VALIDATE_YAML = """
source_defaults:
  type: git
  url: https://github.com/few-sh/terminal-wrench
  commit: abc
entries:
  - name: "5"
    path: tasks/5/claude-opus-4.6/original_task
    domain: {category: terminal}
    adapter: terminal
  - name: "8"
    path: tasks/8/claude-opus-4.6/original_task
    domain: {category: terminal}
    adapter: terminal
"""

_IB_VALIDATE_YAML = """
entries:
  - name: impossible-swebench-harness
    source:
      type: git
      url: https://github.com/safety-research/impossiblebench
      commit: abc
      path: src/impossiblebench/swebench_tasks.py
    domain: {category: code}
    adapter: docker_test
    status: catalog
  - name: impossible-livecodebench-harness
    source:
      type: git
      url: https://github.com/safety-research/impossiblebench
      commit: abc
      path: src/impossiblebench/livecodebench_tasks.py
    domain: {category: code}
    adapter: docker_test
    status: catalog
"""


def _write_v1_card(results: Path, env_id: str, *, hack_attempts: list[int]) -> None:
    from verity_core.scorecard import scorecard_path

    card = Scorecard(env_id=env_id)
    n_successes = len(hack_attempts)
    card.set_axis(
        "V1",
        1.0 if n_successes else 0.0,
        "verity-redteam",
        evidence={
            "n_trials": 4,
            "n_successes": n_successes,
            "hack_attempts": hack_attempts,
            "strategy": "freeform",
        },
    )
    card.to_json(scorecard_path(results, env_id))


def test_validate_dry_run_lists_the_labeled_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    corpus = tmp_path / "manifests"
    corpus.mkdir()
    (corpus / "terminal_wrench.yaml").write_text(_TW_VALIDATE_YAML, encoding="utf-8")

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not open a model client")

    monkeypatch.setattr("verity_redteam.cli.ModelClient.from_config", boom)
    code = main(
        ["validate", "--benchmark", "terminal-wrench", "--corpus", str(corpus), "--dry-run"]
    )
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "benchmark: terminal-wrench" in captured.out
    assert "metric: recall@4" in captured.out
    assert "labeled: 2" in captured.out


def test_validate_skip_run_reports_recall_against_scorecards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from verity_redteam.validation.benchmarks import TERMINAL_WRENCH, resolve_benchmark

    corpus = tmp_path / "manifests"
    corpus.mkdir()
    (corpus / "terminal_wrench.yaml").write_text(_TW_VALIDATE_YAML, encoding="utf-8")
    results = tmp_path / "results"
    resolved = resolve_benchmark(corpus, TERMINAL_WRENCH)
    for env_id in resolved.env_ids:
        _write_v1_card(results, env_id, hack_attempts=[0])

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("skip-run must not open a model client")

    monkeypatch.setattr("verity_redteam.cli.ModelClient.from_config", boom)
    code = main(
        [
            "validate",
            "--benchmark",
            "terminal-wrench",
            "--corpus",
            str(corpus),
            "--results-dir",
            str(results),
            "--skip-run",
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "PASS" in captured.out
    assert "recall@4: 1.000" in captured.out


def test_validate_impossiblebench_without_scorecards_is_an_error(
    tmp_path: Path, capsys: Any
) -> None:
    corpus = tmp_path / "manifests"
    corpus.mkdir()
    (corpus / "impossiblebench.yaml").write_text(_IB_VALIDATE_YAML, encoding="utf-8")
    code = main(
        [
            "validate",
            "--benchmark",
            "impossiblebench",
            "--corpus",
            str(corpus),
            "--results-dir",
            str(tmp_path / "results"),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_ERROR
    assert "catalog-only" in captured.err


def test_validate_impossiblebench_precision_from_scorecards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from verity_redteam.validation.benchmarks import IMPOSSIBLEBENCH, resolve_benchmark

    corpus = tmp_path / "manifests"
    corpus.mkdir()
    (corpus / "impossiblebench.yaml").write_text(_IB_VALIDATE_YAML, encoding="utf-8")
    results = tmp_path / "results"
    resolved = resolve_benchmark(corpus, IMPOSSIBLEBENCH)
    for env_id in resolved.env_ids:
        _write_v1_card(results, env_id, hack_attempts=[])

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("catalog-only ImpossibleBench must not live-audit")

    monkeypatch.setattr("verity_redteam.cli.ModelClient.from_config", boom)
    code = main(
        [
            "validate",
            "--benchmark",
            "impossiblebench",
            "--corpus",
            str(corpus),
            "--results-dir",
            str(results),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_OK
    payload = json.loads(captured.out)
    assert payload["benchmark"] == "impossiblebench"
    assert payload["precision_unrestricted"] == 1.0
    assert payload["passed"] is True
