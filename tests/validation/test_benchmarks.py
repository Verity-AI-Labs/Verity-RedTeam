"""Resolve Terminal Wrench and ImpossibleBench labeled sets from Corpus YAML."""

from __future__ import annotations

from pathlib import Path

import pytest

from verity_redteam.validation.benchmarks import (
    IMPOSSIBLEBENCH,
    TERMINAL_WRENCH,
    TERMINAL_WRENCH_LABELED_COUNT,
    get_benchmark,
    resolve_benchmark,
)


def _write(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


TW_YAML = """
source_defaults:
  type: git
  url: https://github.com/few-sh/terminal-wrench
  commit: abcdef
entries:
  - name: "5"
    path: tasks/5/claude-opus-4.6/original_task
    domain: {category: terminal, subcategory: bash}
    adapter: terminal
    adapter_config: {image: verity-tw:5}
  - name: "8"
    path: tasks/8/claude-opus-4.6/original_task
    domain: {category: terminal, subcategory: bash}
    adapter: terminal
    adapter_config: {image: verity-tw:8}
"""

IB_YAML = """
entries:
  - name: impossible-swebench-harness
    source:
      type: git
      url: https://github.com/safety-research/impossiblebench
      commit: abc
      path: src/impossiblebench/swebench_tasks.py
    domain: {category: code, subcategory: swe-bench}
    adapter: docker_test
    status: catalog
  - name: impossible-livecodebench-harness
    source:
      type: git
      url: https://github.com/safety-research/impossiblebench
      commit: abc
      path: src/impossiblebench/livecodebench_tasks.py
    domain: {category: code, subcategory: livecodebench}
    adapter: docker_test
    status: catalog
"""

EXAMPLE_YAML = """
source_defaults:
  type: git
  url: https://github.com/few-sh/terminal-wrench
  commit: null
entries:
  - name: Example Terminal Environment
    path: "."
    domain: {category: terminal}
    adapter: terminal
"""


class TestGetBenchmark:
    def test_unknown_name_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="unknown benchmark"):
            get_benchmark("not-a-bench")

    def test_terminal_wrench_is_the_recall_set(self) -> None:
        spec = get_benchmark(TERMINAL_WRENCH)
        assert spec.labeled_hackable is True
        assert spec.metric == "recall"
        assert spec.k == 4
        assert spec.expected_count == TERMINAL_WRENCH_LABELED_COUNT
        assert spec.include_catalog is False

    def test_impossiblebench_is_the_precision_set(self) -> None:
        spec = get_benchmark(IMPOSSIBLEBENCH)
        assert spec.labeled_hackable is False
        assert spec.metric == "precision"
        assert spec.include_catalog is True


class TestResolveBenchmark:
    def test_loads_terminal_wrench_and_skips_the_example_clone(self, tmp_path: Path) -> None:
        _write(tmp_path, "terminal_wrench.yaml", TW_YAML)
        _write(tmp_path, "example.yaml", EXAMPLE_YAML)
        resolved = resolve_benchmark(tmp_path, TERMINAL_WRENCH)
        assert len(resolved.entries) == 2
        assert {entry.name for entry in resolved.entries} == {"5", "8"}
        assert len(resolved.auditable) == 2
        assert resolved.catalog_only is False
        assert resolved.manifests[0]["format"] == "terminal"

    def test_loads_catalog_impossiblebench_rows(self, tmp_path: Path) -> None:
        _write(tmp_path, "impossiblebench.yaml", IB_YAML)
        resolved = resolve_benchmark(tmp_path, IMPOSSIBLEBENCH)
        assert len(resolved.entries) == 2
        assert resolved.catalog_only is True
        assert resolved.auditable == ()
        assert all(entry.status == "catalog" for entry in resolved.entries)

    def test_core_flat_terminal_wrench_by_source_url(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "task.yaml",
            "id: tw-5\nformat: terminal\ndomain: tool_use\n"
            "source: https://github.com/few-sh/terminal-wrench\ncommit: abc\n"
            "instructions: do it\n",
        )
        resolved = resolve_benchmark(tmp_path, TERMINAL_WRENCH)
        assert resolved.env_ids == ("tw-5",)
        assert resolved.auditable[0]["id"] == "tw-5"

    def test_missing_benchmark_is_an_error(self, tmp_path: Path) -> None:
        _write(tmp_path, "noise.yaml", "{}\n")
        with pytest.raises(ValueError, match="no terminal-wrench"):
            resolve_benchmark(tmp_path, TERMINAL_WRENCH)
