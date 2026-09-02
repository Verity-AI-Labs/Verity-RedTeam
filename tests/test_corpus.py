"""Fetch-and-resolve selected corpus entries without hitting the network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.test_cli import _write_registry_manifest
from verity_corpus.fetcher import FetchError
from verity_corpus.registry import CorpusRegistry

from verity_redteam.cli import EXIT_ERROR, EXIT_OK, main
from verity_redteam.corpus import (
    fetch_env_root,
    load_auditable_entries,
    resolve_selected,
    resolve_to_core,
    select_entries,
)


def _spy_core_manifest(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from verity_corpus.resolver import core_manifest as real

    seen: dict[str, Any] = {}

    def spy(entry: Any, env_root: Path | None = None) -> dict[str, Any]:
        seen["entry"] = entry
        seen["env_root"] = env_root
        return real(entry, env_root)

    monkeypatch.setattr("verity_corpus.resolver.core_manifest", spy)
    return seen


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "verity.yaml"
    path.write_text(
        f"redteam:\n  cache_dir: {__import__('json').dumps(str(tmp_path / 'cache'))}\n",
        encoding="utf-8",
    )
    return path


class TestResolvePassesEnvRoot:
    def test_selected_entries_pass_a_real_env_root_into_core_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        corpus = tmp_path / "manifests"
        env_id = _write_registry_manifest(
            corpus,
            name="Task Five",
            relpath="task-5",
            instructions=None,
            instruction_file="Redirect stdout into output1.txt.",
        )
        entries = load_auditable_entries(corpus)
        seen = _spy_core_manifest(monkeypatch)

        manifests = resolve_selected(entries, cache_dir=tmp_path / "cache")

        assert len(manifests) == 1
        assert manifests[0]["id"] == env_id
        assert seen["env_root"] is not None
        assert Path(seen["env_root"]).is_dir()
        assert (Path(seen["env_root"]) / "instruction.md").is_file()
        assert manifests[0]["instructions"] == "Redirect stdout into output1.txt."
        assert manifests[0]["env_root"] == str(seen["env_root"])


class TestFetchIsRestrictedToSelection:
    def test_id_and_limit_restrict_which_entries_are_fetched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        corpus = tmp_path / "manifests"
        ids = [
            _write_registry_manifest(corpus, name="Task A", relpath="a"),
            _write_registry_manifest(corpus, name="Task B", relpath="b"),
            _write_registry_manifest(corpus, name="Task C", relpath="c"),
        ]
        fetched: list[str] = []
        real = fetch_env_root

        def spy(entry: Any, cache_dir: Path) -> Path:
            fetched.append(str(entry.id))
            return real(entry, cache_dir)

        monkeypatch.setattr("verity_redteam.corpus.fetch_env_root", spy)
        selected = select_entries(load_auditable_entries(corpus), env_ids=[ids[2]], limit=1)
        resolve_selected(selected, cache_dir=tmp_path / "cache")
        assert fetched == [ids[2]]

        fetched.clear()
        config = _write_config(tmp_path)
        kept = sorted(ids)[:2]
        code = main(
            [
                "--config",
                str(config),
                "batch",
                "--corpus",
                str(corpus),
                "--limit",
                "2",
                "--dry-run",
            ]
        )
        assert code == EXIT_OK
        assert fetched == kept


class TestListDoesNotFetch:
    def test_list_triggers_no_fetch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        corpus = tmp_path / "manifests"
        env_id = _write_registry_manifest(corpus, name="Listed", relpath="listed")

        def boom(*args: object, **kwargs: object) -> Path:
            raise AssertionError("list must not fetch environment sources")

        monkeypatch.setattr("verity_redteam.corpus.fetch_env_root", boom)
        monkeypatch.setattr("verity_corpus.fetcher.fetch", boom)
        code = main(["list", "--corpus", str(corpus)])
        captured = capsys.readouterr()
        assert code == EXIT_OK
        assert env_id in captured.out
        assert "Listed" in captured.out


class TestCatalogIsNeverFetched:
    def test_catalog_status_entries_are_never_fetched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        corpus = tmp_path / "manifests"
        live_id = _write_registry_manifest(corpus, name="Live", relpath="live", filename="live")
        catalog_id = _write_registry_manifest(
            corpus, name="Catalog", relpath="catalog", filename="catalog", status="catalog"
        )
        fetched: list[str] = []
        real = fetch_env_root

        def spy(entry: Any, cache_dir: Path) -> Path:
            fetched.append(str(entry.id))
            return real(entry, cache_dir)

        monkeypatch.setattr("verity_redteam.corpus.fetch_env_root", spy)
        entries = load_auditable_entries(corpus)
        assert [entry.id for entry in entries] == [live_id]
        resolve_selected(entries, cache_dir=tmp_path / "cache")
        assert fetched == [live_id]
        assert catalog_id not in fetched

        catalog = next(entry for entry in CorpusRegistry(corpus).all() if entry.id == catalog_id)
        with pytest.raises(ValueError, match="catalog-only"):
            fetch_env_root(catalog, tmp_path / "cache")


class TestFetchFailure:
    def test_fetch_failure_names_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        corpus = tmp_path / "manifests"
        env_id = _write_registry_manifest(corpus, name="Broken Pin", relpath="broken")

        def boom(entry: Any, cache_dir: Path | None = None) -> Path:
            raise FetchError("network down")

        monkeypatch.setattr("verity_corpus.fetcher.fetch", boom)
        entries = load_auditable_entries(corpus)
        with pytest.raises(ValueError, match="failed to fetch") as caught:
            resolve_to_core(entries[0], tmp_path / "cache")
        message = str(caught.value)
        assert env_id in message
        assert "Broken Pin" in message
        assert "network down" in message

        config = _write_config(tmp_path)
        code = main(["--config", str(config), "run", env_id, "--corpus", str(corpus), "--dry-run"])
        captured = capsys.readouterr()
        assert code == EXIT_ERROR
        assert env_id in captured.err
        assert "Broken Pin" in captured.err
        assert "network down" in captured.err
