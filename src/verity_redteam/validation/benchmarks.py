"""Resolve labeled Terminal Wrench and ImpossibleBench environments from Corpus."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from verity_redteam.validation.metrics import PRECISION_GATE, RECALL_AT_K_GATE

TERMINAL_WRENCH = "terminal-wrench"
IMPOSSIBLEBENCH = "impossiblebench"
BENCHMARK_NAMES: tuple[str, ...] = (TERMINAL_WRENCH, IMPOSSIBLEBENCH)

# Terminal Wrench's published labeled-hackable set, as pinned in Verity-Corpus.
TERMINAL_WRENCH_LABELED_COUNT = 331

TW_MANIFEST = "terminal_wrench.yaml"
IB_MANIFEST = "impossiblebench.yaml"

TW_URL_MARKERS = ("terminal-wrench",)
IB_URL_MARKERS = ("impossiblebench", "impossible_swebench", "impossible_livecodebench")

__all__ = [
    "BENCHMARK_NAMES",
    "IB_MANIFEST",
    "IMPOSSIBLEBENCH",
    "TERMINAL_WRENCH",
    "TERMINAL_WRENCH_LABELED_COUNT",
    "TW_MANIFEST",
    "BenchmarkSpec",
    "ResolvedBenchmark",
    "get_benchmark",
    "resolve_benchmark",
]


@dataclass(frozen=True, slots=True)
class BenchmarkSpec:
    """How a labeled set is identified and which kill-gate it carries."""

    name: str
    manifest_filename: str
    labeled_hackable: bool
    include_catalog: bool
    metric: str
    k: int | None
    gate: float
    expected_count: int | None


BENCHMARKS: dict[str, BenchmarkSpec] = {
    TERMINAL_WRENCH: BenchmarkSpec(
        name=TERMINAL_WRENCH,
        manifest_filename=TW_MANIFEST,
        labeled_hackable=True,
        include_catalog=False,
        metric="recall",
        k=4,
        gate=RECALL_AT_K_GATE,
        expected_count=TERMINAL_WRENCH_LABELED_COUNT,
    ),
    IMPOSSIBLEBENCH: BenchmarkSpec(
        name=IMPOSSIBLEBENCH,
        manifest_filename=IB_MANIFEST,
        labeled_hackable=False,
        include_catalog=True,
        metric="precision",
        k=None,
        gate=PRECISION_GATE,
        expected_count=None,
    ),
}


@dataclass(frozen=True, slots=True)
class ResolvedBenchmark:
    """Labeled corpus entries for one benchmark, plus Core-shaped manifests."""

    spec: BenchmarkSpec
    entries: tuple[Any, ...]
    manifests: tuple[dict[str, Any], ...]

    @property
    def env_ids(self) -> tuple[str, ...]:
        return tuple(str(entry.id) for entry in self.entries)

    @property
    def auditable(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            manifest
            for entry, manifest in zip(self.entries, self.manifests, strict=True)
            if getattr(entry, "status", "registered") != "catalog"
        )

    @property
    def catalog_only(self) -> bool:
        return bool(self.entries) and not self.auditable


def get_benchmark(name: str) -> BenchmarkSpec:
    spec = BENCHMARKS.get(name)
    if spec is None:
        known = ", ".join(BENCHMARK_NAMES)
        raise ValueError(f"unknown benchmark {name!r}; expected one of {known}")
    return spec


def resolve_benchmark(corpus_dir: Path | str, name: str) -> ResolvedBenchmark:
    """Load labeled environments for ``name`` from a Corpus manifests directory.

    Prefers the benchmark's own YAML (``terminal_wrench.yaml`` /
    ``impossiblebench.yaml``). Falls back to URL markers across the registry,
    then to Core-flat manifests when the directory is not a Corpus registry.
    """
    spec = get_benchmark(name)
    corpus_dir = Path(corpus_dir)
    entries = _from_registry(corpus_dir, spec)
    if not entries:
        entries = _from_core_flat(corpus_dir, spec)
    if not entries:
        raise ValueError(
            f"no {spec.name} environments found in {corpus_dir}; "
            f"expected {spec.manifest_filename} or matching source URLs"
        )
    manifests = tuple(_core_manifest(entry) for entry in entries)
    return ResolvedBenchmark(spec=spec, entries=tuple(entries), manifests=manifests)


def _from_registry(corpus_dir: Path, spec: BenchmarkSpec) -> list[Any]:
    try:
        from verity_corpus.registry import CorpusRegistry, RegistryError
    except ImportError:
        return []
    try:
        registry = CorpusRegistry(corpus_dir)
    except RegistryError:
        return []
    wanted_name = spec.manifest_filename
    selected: list[Any] = []
    for entry in registry.all():
        origin = Path(getattr(registry, "_files", {}).get(entry.id, "")).name
        if origin == wanted_name or _matches_markers(entry, spec):
            if entry.status == "catalog" and not spec.include_catalog:
                continue
            selected.append(entry)
    # Prefer origin-filename matches when both the dedicated file and URL
    # markers would otherwise pull in example clones of the same repo.
    dedicated = [
        entry
        for entry in selected
        if Path(getattr(registry, "_files", {}).get(entry.id, "")).name == wanted_name
    ]
    return dedicated or selected


def _from_core_flat(corpus_dir: Path, spec: BenchmarkSpec) -> list[Any]:
    try:
        from verity_core import CorpusError, load_corpus
    except ImportError:
        return []
    try:
        raw = load_corpus(corpus_dir)
    except CorpusError:
        return []
    matched: list[_FlatEntry] = []
    for item in raw:
        if not _core_item_matches(item, spec):
            continue
        matched.append(_FlatEntry.from_core(item))
    return matched


def _matches_markers(entry: Any, spec: BenchmarkSpec) -> bool:
    url = str(getattr(getattr(entry, "source", None), "url", "") or "").lower()
    name = str(getattr(entry, "name", "") or "").lower()
    path = str(getattr(getattr(entry, "source", None), "path", "") or "")
    if spec.name == TERMINAL_WRENCH:
        if not any(marker in url for marker in TW_URL_MARKERS):
            return False
        if path in {".", ""}:
            return False
        return "original_task" in path.replace("\\", "/")
    if spec.name == IMPOSSIBLEBENCH:
        return any(marker in url for marker in IB_URL_MARKERS) or name.startswith("impossible-")
    return False


def _core_item_matches(item: dict[str, Any], spec: BenchmarkSpec) -> bool:
    source = str(item.get("source") or "").lower()
    env_id = str(item.get("id") or "").lower()
    if spec.name == TERMINAL_WRENCH:
        return any(marker in source for marker in TW_URL_MARKERS)
    if spec.name == IMPOSSIBLEBENCH:
        return any(marker in source for marker in IB_URL_MARKERS) or env_id.startswith("impossible")
    return False


def _core_manifest(entry: Any) -> dict[str, Any]:
    if isinstance(entry, _FlatEntry):
        return dict(entry.payload)
    from verity_corpus.resolver import core_manifest

    return core_manifest(entry)


@dataclass(frozen=True, slots=True)
class _FlatEntry:
    """Stand-in for a ManifestEntry when the corpus is Core-flat YAML."""

    id: str
    payload: dict[str, Any]
    status: str = "registered"

    @classmethod
    def from_core(cls, item: dict[str, Any]) -> _FlatEntry:
        return cls(id=str(item.get("id") or ""), payload=dict(item))
