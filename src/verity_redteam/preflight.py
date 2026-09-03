"""Fail fast when a container-backed environment is missing its Docker image.

Core's ``ContainerEnv`` cannot build from a Dockerfile; it requires a prebuilt
tag. Terminal Wrench entries name ``verity-tw:<task_id>`` images that Corpus's
``scripts/build_images.py`` produces. Checking before ``ModelClient`` or
``verify`` keeps a missing tag from surfacing as an opaque mid-run Docker error.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any

try:
    from verity_corpus.resolver import IMAGE_REQUIRED_ADAPTERS
except ImportError:
    IMAGE_REQUIRED_ADAPTERS = frozenset({"terminal", "docker_test"})

__all__ = ["BUILD_IMAGES_HINT", "docker_image_exists", "preflight_images"]

_INSPECT_TIMEOUT_SEC = 30

BUILD_IMAGES_HINT = (
    "Core cannot build from a Dockerfile; build the tag with Corpus's "
    "scripts/build_images.py --repo-root <fetched clone>."
)


def docker_image_exists(tag: str) -> bool:
    """Return True if Docker has ``tag`` locally.

    Tests patch this so CI never needs a daemon. A missing ``docker`` binary
    or a hung inspect is a ``ValueError``, not a silent False.
    """
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--", tag],
            capture_output=True,
            text=True,
            timeout=_INSPECT_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            f"docker is not installed; cannot verify image {tag!r}. {BUILD_IMAGES_HINT}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"timed out inspecting Docker image {tag!r}. {BUILD_IMAGES_HINT}") from exc
    return result.returncode == 0


def _required_image(manifest: dict[str, Any]) -> str | None:
    """Return the image tag, ``""`` if the field is missing, or ``None`` if not required."""
    fmt = str(manifest.get("format") or "").lower()
    if fmt not in IMAGE_REQUIRED_ADAPTERS:
        return None
    image = manifest.get("image")
    if not image:
        return ""
    return str(image)


def preflight_images(manifests: Sequence[dict[str, Any]]) -> None:
    """Raise ``ValueError`` if any container-backed manifest's image is missing locally.

    Non-container formats are skipped. ``--dry-run`` callers should not invoke
    this: dry-run is for confirming resolved instructions, not Docker state.
    """
    missing_field: list[str] = []
    missing_tag: list[tuple[str, str]] = []
    for manifest in manifests:
        tag = _required_image(manifest)
        if tag is None:
            continue
        env_id = str(manifest.get("id") or "<unknown>")
        if not tag:
            missing_field.append(env_id)
            continue
        if not docker_image_exists(tag):
            missing_tag.append((env_id, tag))

    parts: list[str] = []
    if missing_field:
        named = ", ".join(missing_field)
        parts.append(
            f"environment(s) {named} are container-backed but the manifest has no "
            f"image tag. {BUILD_IMAGES_HINT}"
        )
    if len(missing_tag) == 1:
        env_id, tag = missing_tag[0]
        parts.append(
            f"environment {env_id} requires Docker image {tag!r}, which is not "
            f"present locally. {BUILD_IMAGES_HINT}"
        )
    elif missing_tag:
        listing = "\n".join(f"  {env_id}: {tag}" for env_id, tag in missing_tag)
        parts.append(
            f"{len(missing_tag)} container image(s) are not present locally:\n"
            f"{listing}\n{BUILD_IMAGES_HINT}"
        )
    if parts:
        raise ValueError(" ".join(parts) if len(parts) == 1 else "\n".join(parts))
