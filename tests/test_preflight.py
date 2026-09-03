"""Container-image preflight without a Docker daemon."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.test_cli import _stub_fetch, _write_registry_manifest

from verity_redteam.cli import EXIT_ERROR, EXIT_OK, main
from verity_redteam.preflight import BUILD_IMAGES_HINT, preflight_images


def test_image_preflight_fails_cleanly_on_a_missing_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("verity_redteam.preflight.docker_image_exists", lambda tag: False)
    with pytest.raises(ValueError, match="verity-tw:5") as caught:
        preflight_images([{"id": "881806b02177", "format": "terminal", "image": "verity-tw:5"}])
    message = str(caught.value)
    assert "881806b02177" in message
    assert "verity-tw:5" in message
    assert "build_images.py" in message
    assert BUILD_IMAGES_HINT in message


def test_image_preflight_skips_non_container_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(tag: str) -> bool:
        raise AssertionError(f"must not inspect {tag}")

    monkeypatch.setattr("verity_redteam.preflight.docker_image_exists", boom)
    preflight_images([{"id": "x", "format": "verifiers", "image": "should-be-ignored"}])


def test_image_preflight_passes_when_the_tag_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("verity_redteam.preflight.docker_image_exists", lambda tag: True)
    preflight_images([{"id": "abc", "format": "terminal", "image": "verity-tw:5"}])
    preflight_images([{"id": "abc", "format": "docker_test", "image": "verity-tw:8"}])


def test_image_preflight_fails_when_container_manifest_has_no_image() -> None:
    with pytest.raises(ValueError, match="no image tag") as caught:
        preflight_images([{"id": "abc", "format": "terminal"}])
    assert "abc" in str(caught.value)
    assert "build_images.py" in str(caught.value)


def test_run_without_dry_run_preflights_before_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    corpus = tmp_path / "manifests"
    env_id = _write_registry_manifest(
        corpus,
        name="Task Five",
        relpath="task-5",
        adapter="terminal",
        adapter_config={"image": "verity-tw:5"},
    )
    _stub_fetch(monkeypatch, tmp_path)
    inspected: list[str] = []

    def missing(tag: str) -> bool:
        inspected.append(tag)
        return False

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("preflight must run before opening a model client")

    monkeypatch.setattr("verity_redteam.preflight.docker_image_exists", missing)
    monkeypatch.setattr("verity_redteam.cli.ModelClient.from_config", boom)

    code = main(["run", env_id, "--corpus", str(corpus)])
    captured = capsys.readouterr()
    assert code == EXIT_ERROR
    assert inspected == ["verity-tw:5"]
    assert env_id in captured.err
    assert "verity-tw:5" in captured.err
    assert "build_images.py" in captured.err


def test_dry_run_does_not_preflight_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    corpus = tmp_path / "manifests"
    env_id = _write_registry_manifest(
        corpus,
        name="Task Five",
        relpath="task-5",
        adapter="terminal",
        adapter_config={"image": "verity-tw:5"},
        instructions="Redirect stdout into output1.txt.",
    )
    _stub_fetch(monkeypatch, tmp_path)

    def boom(tag: str) -> bool:
        raise AssertionError("dry-run must not inspect Docker images")

    def boom_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not open a model client")

    monkeypatch.setattr("verity_redteam.preflight.docker_image_exists", boom)
    monkeypatch.setattr("verity_redteam.cli.ModelClient.from_config", boom_client)

    code = main(["run", env_id, "--corpus", str(corpus), "--dry-run"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert f"id: {env_id}" in captured.out
    assert "Redirect stdout into output1.txt." in captured.out
