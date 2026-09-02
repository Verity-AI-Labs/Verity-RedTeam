"""Load corpus entries and resolve selected ones into Core manifests.

Filtering happens on registry entries. Fetch and ``core_manifest`` run only
for environments this invocation will audit or dry-run. ``list`` never
fetches: id, name, domain, and adapter all come from the manifest alone.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from verity_core import load_corpus

logger = logging.getLogger(__name__)

__all__ = [
    "fetch_env_root",
    "list_entry_rows",
    "load_auditable_entries",
    "load_registry_entries",
    "resolve_selected",
    "resolve_to_core",
    "select_entries",
]


def _entry_id(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("id") or "")
    return str(entry.id)


def _load_from_registry(corpus_dir: Path) -> list[Any]:
    from verity_corpus.registry import CorpusRegistry, RegistryError

    try:
        entries = CorpusRegistry(corpus_dir).all()
    except RegistryError as registry_exc:
        raise ValueError(
            f"could not load corpus from {corpus_dir}: {registry_exc}"
        ) from registry_exc

    if not entries:
        raise ValueError(f"could not load corpus from {corpus_dir}: no entries found")
    return entries


def load_registry_entries(corpus_dir: Path) -> list[Any]:
    """Load every registry row, including catalog pointers.

    Falls back to Core's ``load_corpus`` only when verity-corpus cannot be
    imported. That path returns Core-flat dicts rather than ManifestEntry
    objects and does not fetch.
    """
    try:
        return _load_from_registry(corpus_dir)
    except ImportError:
        logger.info("verity-corpus is not installed, using core load_corpus")
        return list(load_corpus(corpus_dir))


def load_auditable_entries(corpus_dir: Path, domain: list[str] | str | None = None) -> list[Any]:
    """Non-catalog entries, optionally filtered by domain, sorted by id."""
    entries = load_registry_entries(corpus_dir)
    if domain:
        wanted = {domain} if isinstance(domain, str) else set(domain)
        entries = [
            entry
            for entry in entries
            if (
                str(entry.get("domain", "")) in wanted
                if isinstance(entry, dict)
                else entry.domain.category in wanted
            )
        ]
    entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) or getattr(entry, "status", "registered") != "catalog"
    ]
    entries.sort(key=_entry_id)
    return entries


def select_entries(
    entries: list[Any],
    *,
    env_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[Any]:
    """Apply ``--id`` then ``--limit`` to already-loaded entries, sorted by id."""
    if env_ids:
        wanted = set(env_ids)
        known = {_entry_id(entry) for entry in entries}
        missing = sorted(wanted - known)
        if missing:
            raise ValueError("environment(s) not found: " + ", ".join(missing))
        entries = [entry for entry in entries if _entry_id(entry) in wanted]
    entries = sorted(entries, key=_entry_id)
    if limit is not None:
        entries = entries[:limit]
    return entries


def fetch_env_root(entry: Any, cache_dir: Path) -> Path:
    """Fetch ``entry`` via Corpus and return the on-disk environment root.

    Catalog rows are pointers, not fetchable environments. A fetch failure
    is raised as ``ValueError`` naming the environment and the cause.
    """
    if getattr(entry, "status", None) == "catalog":
        raise ValueError(
            f"environment {entry.id} ({entry.name}) is catalog-only and cannot be fetched"
        )
    try:
        from verity_corpus.fetcher import FetchError, fetch
    except ImportError as exc:
        raise ValueError("verity-corpus is required to fetch environment sources") from exc
    try:
        return fetch(entry, cache_dir=cache_dir)
    except FetchError as exc:
        raise ValueError(f"failed to fetch environment {entry.id} ({entry.name}): {exc}") from exc


def resolve_to_core(entry: Any, cache_dir: Path) -> dict[str, Any]:
    """Fetch ``entry`` if needed and project it into a Core manifest dict."""
    if isinstance(entry, dict):
        return dict(entry)
    env_root = fetch_env_root(entry, cache_dir)
    from verity_corpus.resolver import core_manifest

    return core_manifest(entry, env_root)


def resolve_selected(entries: list[Any], cache_dir: Path) -> list[dict[str, Any]]:
    """Fetch and resolve each selected entry. Does not touch the rest of the corpus."""
    return [resolve_to_core(entry, cache_dir) for entry in entries]


def list_entry_rows(
    corpus_dir: Path, domain: list[str] | str | None = None
) -> list[dict[str, str]]:
    """Id, name, domain, and adapter for every matching row. Never fetches."""
    try:
        from verity_corpus.registry import CorpusRegistry, RegistryError
    except ImportError:
        entries = load_corpus(corpus_dir, domain=domain)
        rows = [
            {
                "id": str(entry.get("id") or ""),
                "name": str(entry.get("name") or ""),
                "domain": str(entry.get("domain") or ""),
                "adapter": str(entry.get("format") or ""),
            }
            for entry in entries
        ]
        rows.sort(key=lambda row: row["id"])
        return rows

    try:
        entries = CorpusRegistry(corpus_dir).all()
    except RegistryError as registry_exc:
        raise ValueError(
            f"could not load corpus from {corpus_dir}: {registry_exc}"
        ) from registry_exc

    if domain:
        wanted = {domain} if isinstance(domain, str) else set(domain)
        entries = [entry for entry in entries if entry.domain.category in wanted]
    rows = [
        {
            "id": entry.id,
            "name": entry.name,
            "domain": entry.domain.category,
            "adapter": entry.adapter,
        }
        for entry in entries
    ]
    rows.sort(key=lambda row: row["id"])
    return rows
