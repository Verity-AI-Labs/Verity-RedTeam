"""Tests for RedTeamConfig and the nested ``redteam:`` YAML block."""

from __future__ import annotations

from pathlib import Path

import pytest
from verity_core.config import DEFAULT_MODEL_NAME, VerityConfig

from verity_redteam.config import (
    DEFAULT_CACHE_DIR,
    DEFAULT_MAX_EPISODES,
    DEFAULT_MODEL_TIMEOUT,
    DEFAULT_N_TRIALS,
    DEFAULT_STRATEGIES,
    DEFAULT_TEMPERATURE,
    RedTeamConfig,
    load_redteam_config,
)


def write_yaml(directory: Path, body: str, name: str = "verity.yaml") -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


class TestDefaults:
    def test_uses_defaults_when_nothing_is_configured(self) -> None:
        config = load_redteam_config(env={})
        assert config.n_trials == DEFAULT_N_TRIALS
        assert config.temperature == DEFAULT_TEMPERATURE
        assert config.strategies == list(DEFAULT_STRATEGIES)
        assert config.max_episodes == DEFAULT_MAX_EPISODES
        assert config.cache_dir == DEFAULT_CACHE_DIR
        assert config.judge_model is None
        assert config.n_perturbations == 4
        assert config.model_timeout == DEFAULT_MODEL_TIMEOUT
        assert config.core.model_name == DEFAULT_MODEL_NAME
        assert isinstance(config.core, VerityConfig)

    def test_rejects_a_non_positive_trial_count(self) -> None:
        with pytest.raises(ValueError, match="n_trials"):
            RedTeamConfig(n_trials=0)

    def test_rejects_a_non_positive_episode_cap(self) -> None:
        with pytest.raises(ValueError, match="max_episodes"):
            RedTeamConfig(max_episodes=0)

    def test_rejects_an_empty_strategy_list(self) -> None:
        with pytest.raises(ValueError, match="strategies"):
            RedTeamConfig(strategies=[])

    def test_rejects_a_non_positive_perturbation_count(self) -> None:
        with pytest.raises(ValueError, match="n_perturbations"):
            RedTeamConfig(n_perturbations=0)

    def test_rejects_a_non_positive_model_timeout(self) -> None:
        with pytest.raises(ValueError, match="model_timeout"):
            RedTeamConfig(model_timeout=0)


class TestFileLoading:
    def test_nested_redteam_block_does_not_break_core_fields(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            """
            model_name: Qwen/Qwen3-32B
            results_dir: /tmp/verity-results
            redteam:
              n_trials: 16
              temperature: 0.4
              strategies: [freeform]
              vrc_dir: /tmp/vrc
              max_submission_length: 1024
              corpus_dir: /tmp/manifests
              cache_dir: /tmp/verity-cache
              max_episodes: 10
              judge_model: judge-model
              n_perturbations: 6
              model_timeout: 900
            """,
        )
        config = load_redteam_config(path, env={})
        assert config.core.model_name == "Qwen/Qwen3-32B"
        assert config.core.results_dir == Path("/tmp/verity-results")
        assert config.n_trials == 16
        assert config.temperature == 0.4
        assert config.vrc_dir == Path("/tmp/vrc")
        assert config.max_submission_length == 1024
        assert config.corpus_dir == Path("/tmp/manifests")
        assert config.cache_dir == Path("/tmp/verity-cache")
        assert config.max_episodes == 10
        assert config.judge_model == "judge-model"
        assert config.n_perturbations == 6
        assert config.model_timeout == 900
        assert config.model_name == "Qwen/Qwen3-32B"

    def test_a_file_without_redteam_block_keeps_redteam_defaults(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, "model_name: only-this\n")
        config = load_redteam_config(path, env={})
        assert config.model_name == "only-this"
        assert config.n_trials == DEFAULT_N_TRIALS

    def test_unknown_redteam_keys_are_rejected(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, "redteam:\n  n_tirals: 4\n")
        with pytest.raises(ValueError, match="unknown redteam key"):
            load_redteam_config(path, env={})

    def test_explicit_missing_path_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="config file not found"):
            load_redteam_config(tmp_path / "absent.yaml", env={})

    def test_to_dict_nests_the_redteam_block(self) -> None:
        payload = RedTeamConfig().to_dict()
        assert "redteam" in payload
        assert payload["redteam"]["n_trials"] == DEFAULT_N_TRIALS
        assert payload["redteam"]["max_episodes"] == DEFAULT_MAX_EPISODES
        assert payload["redteam"]["judge_model"] is None
        assert payload["redteam"]["n_perturbations"] == 4
        assert payload["redteam"]["model_timeout"] == DEFAULT_MODEL_TIMEOUT
        assert payload["redteam"]["cache_dir"] == str(DEFAULT_CACHE_DIR)
        assert payload["redteam"]["strategies"] == list(DEFAULT_STRATEGIES)
        assert payload["model_name"] == DEFAULT_MODEL_NAME
