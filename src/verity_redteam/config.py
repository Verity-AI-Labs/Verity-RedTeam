"""RedTeam-specific configuration, nested under a ``redteam:`` YAML key.

Core fields still resolve through :class:`~verity_core.config.VerityConfig`. The
``redteam:`` block is stripped before that resolution so an unknown-key error
from core does not fire on settings it does not own.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml
from verity_core.config import (
    CONFIG_FILENAME,
    CONFIG_PATH_ENV_VAR,
    VerityConfig,
)

logger = logging.getLogger(__name__)

DEFAULT_N_TRIALS = 8
DEFAULT_TEMPERATURE = 0.7
DEFAULT_STRATEGIES: tuple[str, ...] = (
    "freeform",
    "isomorphic",
    "leakage",
    "isolation",
    "goodhart",
)
DEFAULT_VRC_DIR = Path("vrc")
DEFAULT_MAX_SUBMISSION_LENGTH = 32768
DEFAULT_CORPUS_DIR = Path("manifests")
DEFAULT_CACHE_DIR = Path("cache")
DEFAULT_MAX_EPISODES = 15
DEFAULT_N_PERTURBATIONS = 4
DEFAULT_MODEL_TIMEOUT = 600

__all__ = [
    "DEFAULT_CACHE_DIR",
    "DEFAULT_CORPUS_DIR",
    "DEFAULT_MAX_EPISODES",
    "DEFAULT_MAX_SUBMISSION_LENGTH",
    "DEFAULT_MODEL_TIMEOUT",
    "DEFAULT_N_PERTURBATIONS",
    "DEFAULT_N_TRIALS",
    "DEFAULT_STRATEGIES",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_VRC_DIR",
    "RedTeamConfig",
    "load_redteam_config",
]


def _parse_path(value: Any) -> Path:
    return Path(str(value)).expanduser()


def _parse_int(value: Any, *, source: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source}: cannot interpret {value!r} as an integer") from exc


def _parse_float(value: Any, *, source: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source}: cannot interpret {value!r} as a float") from exc


@dataclass(slots=True)
class RedTeamConfig:
    """Core settings plus the RedTeam probe knobs."""

    core: VerityConfig = field(default_factory=VerityConfig)
    n_trials: int = DEFAULT_N_TRIALS
    temperature: float = DEFAULT_TEMPERATURE
    strategies: list[str] = field(default_factory=lambda: list(DEFAULT_STRATEGIES))
    vrc_dir: Path = field(default_factory=lambda: DEFAULT_VRC_DIR)
    max_submission_length: int = DEFAULT_MAX_SUBMISSION_LENGTH
    corpus_dir: Path = field(default_factory=lambda: DEFAULT_CORPUS_DIR)
    cache_dir: Path = field(default_factory=lambda: DEFAULT_CACHE_DIR)
    max_episodes: int = DEFAULT_MAX_EPISODES
    judge_model: str | None = None
    n_perturbations: int = DEFAULT_N_PERTURBATIONS
    model_timeout: float = DEFAULT_MODEL_TIMEOUT

    def __post_init__(self) -> None:
        self.vrc_dir = _parse_path(self.vrc_dir)
        self.corpus_dir = _parse_path(self.corpus_dir)
        self.cache_dir = _parse_path(self.cache_dir)
        if self.n_trials < 1:
            raise ValueError(f"n_trials must be >= 1, got {self.n_trials}")
        if self.max_submission_length < 1:
            raise ValueError(
                f"max_submission_length must be >= 1, got {self.max_submission_length}"
            )
        if not self.strategies:
            raise ValueError("strategies must be a non-empty list of strategy names")
        if self.max_episodes < 1:
            raise ValueError(f"max_episodes must be >= 1, got {self.max_episodes}")
        if self.n_perturbations < 1:
            raise ValueError(f"n_perturbations must be >= 1, got {self.n_perturbations}")
        if self.model_timeout <= 0:
            raise ValueError(f"model_timeout must be > 0, got {self.model_timeout}")

    @property
    def model_name(self) -> str:
        return self.core.model_name

    @property
    def model_base_url(self) -> str:
        return self.core.model_base_url

    @property
    def results_dir(self) -> Path:
        return self.core.results_dir

    def ensure_dirs(self) -> None:
        self.core.ensure_dirs()
        self.vrc_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.core.to_dict(),
            "redteam": {
                "n_trials": self.n_trials,
                "temperature": self.temperature,
                "strategies": list(self.strategies),
                "vrc_dir": str(self.vrc_dir),
                "max_submission_length": self.max_submission_length,
                "corpus_dir": str(self.corpus_dir),
                "cache_dir": str(self.cache_dir),
                "max_episodes": self.max_episodes,
                "judge_model": self.judge_model,
                "n_perturbations": self.n_perturbations,
                "model_timeout": self.model_timeout,
            },
        }


_REDTEAM_FIELDS = {f.name for f in fields(RedTeamConfig) if f.name != "core"}


def _coerce_redteam(data: dict[str, Any], *, source: str) -> dict[str, Any]:
    coerced: dict[str, Any] = {}
    unknown = sorted(set(data) - _REDTEAM_FIELDS)
    if unknown:
        raise ValueError(
            f"{source}: unknown redteam key(s): {', '.join(unknown)}; "
            f"expected any of {', '.join(sorted(_REDTEAM_FIELDS))}"
        )
    for key, value in data.items():
        if value is None:
            continue
        if key in ("vrc_dir", "corpus_dir", "cache_dir"):
            coerced[key] = _parse_path(value)
        elif key in {
            "n_trials",
            "max_submission_length",
            "max_episodes",
            "n_perturbations",
        }:
            coerced[key] = _parse_int(value, source=f"{source}.{key}")
        elif key in {"temperature", "model_timeout"}:
            coerced[key] = _parse_float(value, source=f"{source}.{key}")
        elif key == "judge_model":
            coerced[key] = None if value in ("", None) else str(value)
        elif key == "strategies":
            if isinstance(value, str):
                coerced[key] = [value]
            else:
                coerced[key] = [str(item) for item in value]
        else:
            coerced[key] = value
    return coerced


def _discover_config_path(env: dict[str, str] | None) -> Path | None:
    source = os.environ if env is None else env
    from_env = source.get(CONFIG_PATH_ENV_VAR)
    if from_env:
        return Path(from_env).expanduser()
    candidate = Path.cwd() / CONFIG_FILENAME
    return candidate if candidate.is_file() else None


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(
            f"{path}: expected a YAML mapping at the top level, got {type(loaded).__name__}"
        )
    return loaded


def _core_from_layers(file_core: dict[str, Any], env: dict[str, str] | None) -> VerityConfig:
    """Fold defaults <- VERITY_* env <- file, without handing ``redteam:`` to core."""
    values = VerityConfig().to_dict()
    source = os.environ if env is None else env
    for f in fields(VerityConfig):
        raw = source.get(f"VERITY_{f.name.upper()}")
        if raw is not None and raw != "":
            values[f.name] = raw
    values.update(file_core)
    return VerityConfig.from_dict(values)


def load_redteam_config(
    path: Path | str | None = None, *, env: dict[str, str] | None = None
) -> RedTeamConfig:
    """Load core + ``redteam:`` settings.

    An explicit ``path`` that does not exist is an error. When ``path`` is omitted
    we honour ``$VERITY_CONFIG`` and then ``./verity.yaml``, matching core.
    """
    if path is None:
        config_path = _discover_config_path(env)
    else:
        config_path = Path(path).expanduser()
        if not config_path.is_file():
            raise FileNotFoundError(f"config file not found: {config_path}")

    file_core: dict[str, Any] = {}
    file_redteam: dict[str, Any] = {}
    if config_path is not None:
        raw = _read_yaml(config_path)
        file_redteam = dict(raw.pop("redteam", {}) or {})
        file_core = raw

    core = _core_from_layers(file_core, env)
    redteam = _coerce_redteam(file_redteam, source="redteam")
    config = RedTeamConfig(core=core, **redteam)
    logger.debug(
        "loaded redteam config path=%s n_trials=%d strategies=%s",
        config_path,
        config.n_trials,
        config.strategies,
    )
    return config
