"""Tests for the verity-redteam CLI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from verity_core.scorecard import Scorecard

from verity_redteam.cli import (
    EXIT_ERROR,
    EXIT_OK,
    _load_entries,
    _model_client,
    build_parser,
    main,
)
from verity_redteam.config import DEFAULT_MODEL_TIMEOUT, RedTeamConfig


def _stub_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, text: str = "real task prose from disk"
) -> tuple[list[str], Path]:
    env_root = tmp_path / "fetched-env"
    env_root.mkdir()
    (env_root / "instruction.md").write_text(text, encoding="utf-8")
    fetched: list[str] = []

    def fake(entry: Any, cache_dir: Path) -> Path:
        fetched.append(str(entry.id))
        return env_root

    monkeypatch.setattr("verity_redteam.corpus.fetch_env_root", fake)
    return fetched, env_root


def test_configured_model_timeout_reaches_the_constructed_client() -> None:
    from verity_core.models import DEFAULT_TIMEOUT_SECONDS, ModelClient

    assert DEFAULT_MODEL_TIMEOUT != DEFAULT_TIMEOUT_SECONDS
    config = RedTeamConfig(model_timeout=777)
    with _model_client(config) as client:
        assert isinstance(client, ModelClient)
        assert client.timeout == 777
    with _model_client(RedTeamConfig()) as client:
        assert client.timeout == DEFAULT_MODEL_TIMEOUT


def test_archive_flag_reaches_the_constructed_runner() -> None:
    from tests.conftest import FakeClient

    from verity_redteam.cli import _build_runner

    enabled = RedTeamConfig(archive_all_trajectories=True, strategies=["freeform"])
    runner = _build_runner(enabled, FakeClient())  # type: ignore[arg-type]
    assert runner.archive is not None
    assert runner.archive.root == enabled.trajectory_archive_dir
    disabled = _build_runner(RedTeamConfig(strategies=["freeform"]), FakeClient())  # type: ignore[arg-type]
    assert disabled.archive is None


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
    listed = parser.parse_args(["list", "--domain", "code"])
    assert listed.command == "list"
    assert listed.domain == ["code"]
    selected = parser.parse_args(["batch", "--id", "abc", "--id", "def", "--limit", "3"])
    assert selected.env_ids == ["abc", "def"]
    assert selected.limit == 3
    validated = parser.parse_args(
        ["validate", "--benchmark", "terminal-wrench", "--id", "abc", "--limit", "2"]
    )
    assert validated.env_ids == ["abc"]
    assert validated.limit == 2
    assert parser.parse_args(["vrc", "list", "corpus/task-1"]).command == "vrc"
    assert parser.parse_args(["report", "--corpus"]).corpus is True
    parsed = parser.parse_args(["validate", "--benchmark", "terminal-wrench", "--dry-run"])
    assert parsed.command == "validate"
    assert parsed.benchmark == "terminal-wrench"
    assert parsed.dry_run is True
    judged = parser.parse_args(["validate-judge", "--benchmark", "terminal-wrench", "--limit", "4"])
    assert judged.command == "validate-judge"
    assert judged.benchmark == "terminal-wrench"
    assert judged.limit == 4
    assert judged.dry_run is False


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
    instructions: str | None = "Fix the failing test.",
    domain: str = "code",
    adapter: str = "verifiers",
    filename: str | None = None,
    status: str = "registered",
    instruction_file: str | None = None,
    adapter_config: dict[str, Any] | None = None,
) -> str:
    from verity_corpus.models.manifest import SourceSpec, compute_entry_id

    directory.mkdir(parents=True, exist_ok=True)
    env_root = directory / "envs" / relpath
    env_root.mkdir(parents=True, exist_ok=True)
    if instruction_file is not None:
        (env_root / "instruction.md").write_text(instruction_file, encoding="utf-8")
    stem = filename or relpath.replace("/", "__")
    metadata = ""
    if instructions is not None:
        metadata = f"    metadata:\n      instructions: {instructions}\n"
    status_line = f"    status: {status}\n" if status != "registered" else ""
    config_block = ""
    if adapter_config:
        config_block = "    adapter_config:\n" + "".join(
            f"      {key}: {json.dumps(value)}\n" for key, value in adapter_config.items()
        )
    (directory / f"{stem}.yaml").write_text(
        "entries:\n"
        f"  - name: {name}\n"
        "    source:\n"
        "      type: local\n"
        f"      path: {json.dumps(str(env_root))}\n"
        f"    domain: {domain}\n"
        f"    adapter: {adapter}\n"
        f"{status_line}"
        f"{config_block}"
        f"{metadata}",
        encoding="utf-8",
    )
    return compute_entry_id(SourceSpec(type="local", path=str(env_root)))


def test_run_audits_one_corpus_entry(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    from tests.conftest import FakeClient, FakeEnv, make_spec

    manifest = tmp_path / "manifests"
    env_id = _write_registry_manifest(manifest, name="Task One", relpath="task-1")
    spec = make_spec()
    env = FakeEnv(spec=spec, gold="GOLD", passing="bypass")
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
        assert entries[0].id == env_id
        assert entries[0].adapter == "verifiers"
        assert entries[0].metadata["instructions"] == "from the registry"

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

        monkeypatch.setattr("verity_redteam.corpus.load_corpus", boom)
        with caplog.at_level(logging.ERROR):
            entries = _load_entries(tmp_path)
        assert not any("_schema.yaml" in rec.getMessage() for rec in caplog.records)
        assert [entry.id for entry in entries] == [env_id]

    def test_falls_back_to_load_corpus_when_corpus_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "task.yaml").write_text(
            "id: corpus/task-1\nformat: verifiers\ndomain: code\ninstructions: do it\n",
            encoding="utf-8",
        )

        def no_registry(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            raise ImportError("verity-corpus is not installed")

        monkeypatch.setattr("verity_redteam.corpus._load_from_registry", no_registry)
        with caplog.at_level(logging.INFO, logger="verity_redteam.corpus"):
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

        monkeypatch.setattr("verity_redteam.corpus._load_from_registry", no_registry)
        monkeypatch.setattr("verity_redteam.corpus.load_corpus", boom)
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
    assert "instructions: " + ("A" * 250) in captured.out


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


def test_list_prints_id_name_domain_and_adapter(tmp_path: Path, capsys: Any) -> None:
    corpus = tmp_path / "manifests"
    id_code = _write_registry_manifest(
        corpus, name="Alpha Env", relpath="alpha", domain="code", adapter="verifiers"
    )
    id_term = _write_registry_manifest(
        corpus, name="Beta Env", relpath="beta", domain="terminal", adapter="terminal"
    )
    code = main(["list", "--corpus", str(corpus)])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert lines[0].split()[:4] == ["ID", "NAME", "DOMAIN", "ADAPTER"]
    by_id = {line.split()[0]: line for line in lines[1:]}
    assert "Alpha Env" in by_id[id_code]
    assert "code" in by_id[id_code]
    assert "verifiers" in by_id[id_code]
    assert "Beta Env" in by_id[id_term]
    assert "terminal" in by_id[id_term]
    assert list(by_id) == sorted(by_id)


def test_list_filters_by_domain(tmp_path: Path, capsys: Any) -> None:
    corpus = tmp_path / "manifests"
    id_code = _write_registry_manifest(
        corpus, name="Alpha Env", relpath="alpha", domain="code", adapter="verifiers"
    )
    id_term = _write_registry_manifest(
        corpus, name="Beta Env", relpath="beta", domain="terminal", adapter="terminal"
    )
    code = main(["list", "--corpus", str(corpus), "--domain", "code"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert id_code in captured.out
    assert "Alpha Env" in captured.out
    assert id_term not in captured.out
    assert "Beta Env" not in captured.out


def test_batch_dry_run_filters_by_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    corpus = tmp_path / "manifests"
    id_one = _write_registry_manifest(corpus, name="Task One", relpath="task-1", instructions="one")
    id_two = _write_registry_manifest(corpus, name="Task Two", relpath="task-2", instructions="two")

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not open a model client")

    monkeypatch.setattr("verity_redteam.cli.ModelClient.from_config", boom)
    code = main(["batch", "--corpus", str(corpus), "--id", id_two, "--dry-run"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert f"id: {id_two}" in captured.out
    assert "instructions: two" in captured.out
    assert f"id: {id_one}" not in captured.out


def test_batch_limit_is_deterministic_after_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    corpus = tmp_path / "manifests"
    ids = [
        _write_registry_manifest(corpus, name="Task A", relpath="a", instructions="a"),
        _write_registry_manifest(corpus, name="Task B", relpath="b", instructions="b"),
        _write_registry_manifest(corpus, name="Task C", relpath="c", instructions="c"),
    ]
    expected = sorted(ids)[:2]

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not open a model client")

    monkeypatch.setattr("verity_redteam.cli.ModelClient.from_config", boom)
    first = main(["batch", "--corpus", str(corpus), "--limit", "2", "--dry-run"])
    out_first = capsys.readouterr().out
    second = main(["batch", "--corpus", str(corpus), "--limit", "2", "--dry-run"])
    out_second = capsys.readouterr().out
    assert first == EXIT_OK
    assert second == EXIT_OK
    assert out_first == out_second
    for env_id in expected:
        assert f"id: {env_id}" in out_first
    omitted = (set(ids) - set(expected)).pop()
    assert f"id: {omitted}" not in out_first

    chosen = [ids[0], ids[2]]
    after_id = main(
        [
            "batch",
            "--corpus",
            str(corpus),
            "--id",
            chosen[0],
            "--id",
            chosen[1],
            "--limit",
            "1",
            "--dry-run",
        ]
    )
    out_after_id = capsys.readouterr().out
    assert after_id == EXIT_OK
    kept = sorted(chosen)[0]
    dropped = sorted(chosen)[1]
    assert f"id: {kept}" in out_after_id
    assert f"id: {dropped}" not in out_after_id


def test_batch_unknown_id_is_an_error(tmp_path: Path, capsys: Any) -> None:
    corpus = tmp_path / "manifests"
    _write_registry_manifest(corpus, name="Task One", relpath="task-1")
    code = main(["batch", "--corpus", str(corpus), "--id", "missing", "--dry-run"])
    captured = capsys.readouterr()
    assert code == EXIT_ERROR
    assert "environment(s) not found: missing" in captured.err


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
    _stub_fetch(monkeypatch, tmp_path)
    code = main(
        ["validate", "--benchmark", "terminal-wrench", "--corpus", str(corpus), "--dry-run"]
    )
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "benchmark: terminal-wrench" in captured.out
    assert "metric: recall@4" in captured.out
    assert "labeled: 2" in captured.out


def test_validate_dry_run_filters_by_id_and_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from verity_redteam.validation.benchmarks import TERMINAL_WRENCH, resolve_benchmark

    corpus = tmp_path / "manifests"
    corpus.mkdir()
    (corpus / "terminal_wrench.yaml").write_text(_TW_VALIDATE_YAML, encoding="utf-8")
    env_ids = sorted(resolve_benchmark(corpus, TERMINAL_WRENCH).env_ids)

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not open a model client")

    monkeypatch.setattr("verity_redteam.cli.ModelClient.from_config", boom)
    _stub_fetch(monkeypatch, tmp_path)
    by_id = main(
        [
            "validate",
            "--benchmark",
            "terminal-wrench",
            "--corpus",
            str(corpus),
            "--id",
            env_ids[1],
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()
    assert by_id == EXIT_OK
    assert f"id: {env_ids[1]}" in captured.out
    assert f"id: {env_ids[0]}" not in captured.out
    assert "labeled: 1" in captured.out

    limited = main(
        [
            "validate",
            "--benchmark",
            "terminal-wrench",
            "--corpus",
            str(corpus),
            "--limit",
            "1",
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()
    assert limited == EXIT_OK
    assert "labeled: 1" in captured.out
    assert f"id: {env_ids[0]}" in captured.out
    assert f"id: {env_ids[1]}" not in captured.out


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


def _stub_recorded_hacks(monkeypatch: pytest.MonkeyPatch, commands: list[str]) -> None:
    def fake(entry: Any, cache_dir: Path, env_root: Path | None = None) -> dict[str, Any]:
        trajectories = [
            {
                "run_id": f"v5_{index}" if index else "v5",
                "source_path": f"/tmp/v5_{index}",
                "actions": [{"kind": "exec", "command": command}],
                "verifier": {"rewarded": True},
                "metadata": {},
            }
            for index, command in enumerate(commands)
        ]
        return {"present": True, "trajectories": trajectories, "message": ""}

    monkeypatch.setattr("verity_redteam.corpus.load_recorded_hacks", fake)


def test_validate_judge_dry_run_does_not_open_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    corpus = tmp_path / "manifests"
    corpus.mkdir()
    (corpus / "terminal_wrench.yaml").write_text(_TW_VALIDATE_YAML, encoding="utf-8")
    _stub_fetch(monkeypatch, tmp_path)
    _stub_recorded_hacks(monkeypatch, ["echo pass >> /tests/conftest.py"])

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not open a model client")

    monkeypatch.setattr("verity_redteam.cli.ModelClient.from_config", boom)
    code = main(
        [
            "validate-judge",
            "--benchmark",
            "terminal-wrench",
            "--corpus",
            str(corpus),
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "benchmark: terminal-wrench" in captured.out
    assert "no attacker, no container" in captured.out
    assert "known hacks: 2" in captured.out


def test_validate_judge_json_reports_recall_and_false_positives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from tests.conftest import FakeClient

    corpus = tmp_path / "manifests"
    corpus.mkdir()
    (corpus / "terminal_wrench.yaml").write_text(_TW_VALIDATE_YAML, encoding="utf-8")
    _stub_fetch(monkeypatch, tmp_path)
    _stub_recorded_hacks(monkeypatch, ["echo pass >> /tests/conftest.py"])

    class _ClientCtx:
        def __enter__(self) -> FakeClient:
            return FakeClient(contents="should not be required for heuristic short-circuit")

        def __exit__(self, *args: object) -> None:
            return None

        @classmethod
        def from_config(cls, config: object, **kwargs: object) -> _ClientCtx:
            return cls()

    monkeypatch.setattr("verity_redteam.cli.ModelClient", _ClientCtx)
    monkeypatch.setattr("verity_redteam.cli._model_client", lambda config: _ClientCtx())
    # Heuristic judge: tampering is a hack; solve fixtures stay solves. Avoid
    # depending on a live LLM for this CLI wiring test.
    from verity_redteam.judge import HeuristicJudge

    monkeypatch.setattr("verity_redteam.cli.LlmJudge", lambda *a, **k: HeuristicJudge())
    code = main(
        [
            "validate-judge",
            "--benchmark",
            "terminal-wrench",
            "--corpus",
            str(corpus),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_OK
    payload = json.loads(captured.out)
    assert payload["benchmark"] == "terminal-wrench"
    assert payload["n_known_hacks"] == 2
    assert payload["recall"] == 1.0
    assert payload["false_positives"]["passed"] is True
    assert payload["passed"] is True
    assert "recall_ci_lower" in payload
    assert "recall_ci_upper" in payload


def test_validate_judge_does_not_preflight_or_load_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "manifests"
    corpus.mkdir()
    (corpus / "terminal_wrench.yaml").write_text(_TW_VALIDATE_YAML, encoding="utf-8")
    _stub_fetch(monkeypatch, tmp_path)
    _stub_recorded_hacks(monkeypatch, ["echo pass >> /tests/conftest.py"])

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("validate-judge must not start a container")

    monkeypatch.setattr("verity_redteam.cli.preflight_images", boom)
    monkeypatch.setattr("verity_redteam.runner.load_env", boom)
    from verity_redteam.judge import HeuristicJudge

    monkeypatch.setattr("verity_redteam.cli.LlmJudge", lambda *a, **k: HeuristicJudge())

    class _ClientCtx:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr("verity_redteam.cli._model_client", lambda config: _ClientCtx())
    code = main(
        [
            "validate-judge",
            "--benchmark",
            "terminal-wrench",
            "--corpus",
            str(corpus),
            "--limit",
            "1",
        ]
    )
    assert code == EXIT_OK
