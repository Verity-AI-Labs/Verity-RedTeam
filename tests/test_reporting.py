"""Tests for corpus-wide RedTeam reporting."""

from __future__ import annotations

from pathlib import Path

from verity_core.scorecard import Scorecard
from verity_corpus.models.vrc import VRCEntry

from verity_redteam.reporting import build_redteam_report, load_all_vrc_entries


def _card(
    env_id: str,
    *,
    v1: float | None,
    domain: str = "code",
    n_trials: int = 8,
    n_successes: int = 0,
    categories: list[str] | None = None,
    serious_alpha: float | None = None,
    precheck_failed: bool = False,
) -> Scorecard:
    metadata = {"domain": domain, "precheck_failed": precheck_failed}
    card = Scorecard(env_id=env_id, metadata=metadata)
    if v1 is None:
        return card
    card.set_axis(
        "V1",
        v1,
        "verity-redteam",
        evidence={
            "n_trials": n_trials,
            "n_successes": n_successes,
            "strategy": "freeform",
            "serious_alpha": serious_alpha if serious_alpha is not None else v1,
            "observed_categories": categories or [],
        },
    )
    return card


class TestBuildRedTeamReport:
    def test_ranks_by_alpha_and_skips_unscored(self) -> None:
        report = build_redteam_report(
            [
                _card("low", v1=0.25, domain="code"),
                _card("high", v1=0.75, domain="tool_use"),
                _card("skip", v1=None, domain="code"),
            ],
            results_dir="results",
        )
        assert [row["env_id"] for row in report.ranked] == ["high", "low"]
        assert report.alpha["n"] == 2
        assert report.alpha["mean"] == 0.5
        assert report.alpha["median"] == 0.5
        assert report.alpha["min"] == 0.25
        assert report.alpha["max"] == 0.75
        assert report.alpha["buckets"]["(0, 0.25]"] == 1
        assert report.alpha["buckets"]["(0.5, 0.75]"] == 1
        assert report.domains["code"]["n"] == 2
        assert report.domains["code"]["n_scored"] == 1
        assert report.scorecards[-1]["env_id"] == "skip"

    def test_category_table_includes_every_tw_name(self) -> None:
        report = build_redteam_report(
            [
                _card(
                    "e",
                    v1=1.0,
                    categories=["output-spoofing", "hollow-implementation", "not-a-real"],
                )
            ],
            results_dir="results",
        )
        assert report.categories["output-spoofing"] == 1
        assert report.categories["hollow-implementation"] == 1
        assert report.categories["keyword-gaming"] == 0
        assert "uncategorized" in report.categories

    def test_unscored_is_not_a_zero_in_the_distribution(self) -> None:
        report = build_redteam_report([_card("e", v1=None)], results_dir="results")
        assert report.alpha["n"] == 0
        assert report.alpha["mean"] is None
        assert report.ranked == []


class TestVrcStats:
    def test_counts_entries_per_env_and_categories(self, tmp_path: Path) -> None:
        first = VRCEntry(
            env_id="env-a",
            exploit_type="output-spoofing",
            trajectory=[{"role": "user", "content": "hack"}],
            model_id="m",
            notes="trial_id=1 outcome=serious_hack strategy=freeform categories=output-spoofing",
        )
        second = VRCEntry(
            env_id="env-a",
            exploit_type="freeform",
            trajectory=[{"role": "user", "content": "hack2"}],
            model_id="m",
            notes="trial_id=2 outcome=serious_hack strategy=freeform categories=",
        )
        first.save(tmp_path)
        second.save(tmp_path)
        loaded = load_all_vrc_entries(tmp_path)
        assert len(loaded) == 2
        report = build_redteam_report([], results_dir="results", vrc_dir=tmp_path)
        assert report.vrc["n_entries"] == 2
        assert report.vrc["n_environments"] == 1
        assert report.vrc["entries_per_env"]["env-a"] == 2
        assert report.vrc["categories"]["output-spoofing"] == 1
        assert report.vrc["categories"]["uncategorized"] == 1

    def test_to_text_includes_the_corpus_sections(self) -> None:
        text = build_redteam_report(
            [_card("env-a", v1=0.5, domain="code", categories=["metric-spoofing"])],
            results_dir="/tmp/results",
        ).to_text()
        assert "Alpha distribution" in text
        assert "By domain" in text
        assert "ranked by hackability" in text
        assert "Exploit categories" in text
        assert "metric-spoofing" in text
        assert "VRC" in text
        assert "env-a" in text
        assert "0.500" in text
