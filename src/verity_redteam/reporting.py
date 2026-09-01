"""Corpus-wide RedTeam scorecard and VRC summaries."""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verity_core.scorecard import Scorecard
from verity_corpus.models.vrc import VRCEntry

from verity_redteam.outcomes import OBSERVED_CATEGORIES, normalize_categories

V1_AXIS = "V1"

ALPHA_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0", 0.0, 0.0),
    ("(0, 0.25]", 0.0, 0.25),
    ("(0.25, 0.5]", 0.25, 0.5),
    ("(0.5, 0.75]", 0.5, 0.75),
    ("(0.75, 1)", 0.75, 1.0),
    ("1", 1.0, 1.0),
)

__all__ = [
    "ALPHA_BUCKETS",
    "RedTeamCorpusReport",
    "build_redteam_report",
    "load_all_vrc_entries",
]


def load_all_vrc_entries(vrc_dir: Path | str | None) -> list[VRCEntry]:
    """Load every VRC JSON file under ``vrc_dir``, skipping files that will not parse."""
    if vrc_dir is None:
        return []
    root = Path(vrc_dir)
    if not root.is_dir():
        return []
    entries: list[VRCEntry] = []
    for path in sorted(root.rglob("*.json")):
        try:
            entries.append(VRCEntry.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return entries


def _v1_value(card: Scorecard) -> float | None:
    return card.get_axis(V1_AXIS).value


def _v1_evidence(card: Scorecard) -> dict[str, Any]:
    return dict(card.get_axis(V1_AXIS).evidence or {})


def _bucket_alpha(alpha: float) -> str:
    if alpha == 0.0:
        return "0"
    if alpha == 1.0:
        return "1"
    for label, lo, hi in ALPHA_BUCKETS:
        if label in {"0", "1"}:
            continue
        if lo < alpha <= hi or (label == "(0.75, 1)" and lo < alpha < hi):
            return label
    return "(0.75, 1)"


def _alpha_distribution(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
            "buckets": {label: 0 for label, _lo, _hi in ALPHA_BUCKETS},
        }
    ordered = sorted(values)
    buckets = {label: 0 for label, _lo, _hi in ALPHA_BUCKETS}
    for alpha in ordered:
        buckets[_bucket_alpha(alpha)] += 1
    return {
        "n": len(ordered),
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "buckets": buckets,
    }


def _categories_from_evidence(evidence: dict[str, Any]) -> list[str]:
    return normalize_categories(evidence.get("observed_categories"))


def _categories_from_vrc(entry: VRCEntry) -> list[str]:
    notes = entry.notes or ""
    marker = "categories="
    if marker in notes:
        raw = notes.split(marker, 1)[1]
        token = raw.split()[0] if raw.split() else raw
        found = normalize_categories(token.split(","))
        if found:
            return found
    return normalize_categories([entry.exploit_type])


def _category_table(counts: Counter[str]) -> dict[str, int]:
    table = {name: int(counts.get(name, 0)) for name in OBSERVED_CATEGORIES}
    extras = sum(count for name, count in counts.items() if name not in table)
    table["uncategorized"] = extras
    return table


@dataclass(slots=True)
class RedTeamCorpusReport:
    """Aggregate view of audited scorecards plus the VRC on disk."""

    results_dir: str
    scorecards: list[dict[str, Any]] = field(default_factory=list)
    alpha: dict[str, Any] = field(default_factory=dict)
    domains: dict[str, Any] = field(default_factory=dict)
    categories: dict[str, int] = field(default_factory=dict)
    ranked: list[dict[str, Any]] = field(default_factory=list)
    vrc: dict[str, Any] = field(default_factory=dict)
    n_precheck_failed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "results_dir": self.results_dir,
            "scorecards": list(self.scorecards),
            "alpha": dict(self.alpha),
            "domains": dict(self.domains),
            "categories": dict(self.categories),
            "ranked": list(self.ranked),
            "vrc": dict(self.vrc),
            "n_precheck_failed": self.n_precheck_failed,
        }

    def to_text(self) -> str:
        lines: list[str] = []
        n = len(self.scorecards)
        lines.append(f"Scorecards: {n}  ({self.results_dir})")
        if not self.scorecards:
            lines.append("no scorecards found")
            return "\n".join(lines)

        lines.append(f"{'ENV':<40}  {'V1':>7}  {'n':>4}  {'hits':>4}  STRATEGY")
        for row in self.scorecards:
            value = "—" if row["v1"] is None else f"{row['v1']:.3f}"
            trials = "—" if row["n_trials"] is None else str(row["n_trials"])
            hits = "—" if row["n_successes"] is None else str(row["n_successes"])
            flag = " [precheck-failed]" if row["precheck_failed"] else ""
            lines.append(
                f"{row['env_id']:<40.40}  {value:>7}  {trials:>4}  {hits:>4}  "
                f"{row['strategy'] or '—'}{flag}"
            )

        dist = self.alpha
        lines.append("")
        lines.append("## Alpha distribution (scored V1)")
        if not dist.get("n"):
            lines.append("no scored V1 axes")
        else:
            lines.append(
                f"n={dist['n']}  mean={dist['mean']:.3f}  median={dist['median']:.3f}  "
                f"min={dist['min']:.3f}  max={dist['max']:.3f}"
            )
            lines.append("bucket                  count")
            for label, _lo, _hi in ALPHA_BUCKETS:
                lines.append(f"{label:<22}  {dist['buckets'][label]:>5}")

        lines.append("")
        lines.append("## By domain")
        lines.append(f"{'DOMAIN':<16}  {'n':>4}  {'scored':>6}  {'mean V1':>8}")
        for domain, stats in self.domains.items():
            mean = "—" if stats["mean_alpha"] is None else f"{stats['mean_alpha']:.3f}"
            lines.append(f"{domain:<16.16}  {stats['n']:>4}  {stats['n_scored']:>6}  {mean:>8}")

        lines.append("")
        lines.append("## Environments ranked by hackability")
        if not self.ranked:
            lines.append("no scored environments")
        else:
            lines.append(f"{'ENV':<40}  {'alpha':>7}  {'serious':>7}  DOMAIN")
            for row in self.ranked:
                lines.append(
                    f"{row['env_id']:<40.40}  {row['alpha']:.3f}  "
                    f"{row['serious_alpha']:.3f}  {row['domain']}"
                )

        lines.append("")
        lines.append("## Exploit categories (Terminal Wrench vocabulary)")
        lines.append(f"{'CATEGORY':<32}  {'count':>5}")
        for name in OBSERVED_CATEGORIES:
            lines.append(f"{name:<32}  {self.categories.get(name, 0):>5}")
        uncategorized = int(self.categories.get("uncategorized") or 0)
        lines.append(f"{'uncategorized':<32}  {uncategorized:>5}")

        vrc = self.vrc
        lines.append("")
        lines.append("## VRC")
        lines.append(
            f"entries: {vrc.get('n_entries', 0)}  environments: {vrc.get('n_environments', 0)}"
        )
        per_env = vrc.get("entries_per_env") or {}
        if per_env:
            lines.append(f"{'ENV':<40}  {'entries':>7}")
            for env_id, count in per_env.items():
                lines.append(f"{env_id:<40.40}  {count:>7}")
        vrc_cats = vrc.get("categories") or {}
        lines.append("VRC category counts")
        for name in OBSERVED_CATEGORIES:
            lines.append(f"{name:<32}  {vrc_cats.get(name, 0):>5}")
        return "\n".join(lines)


def _scorecard_row(card: Scorecard) -> dict[str, Any]:
    v1 = card.get_axis(V1_AXIS)
    evidence = v1.evidence or {}
    return {
        "env_id": card.env_id,
        "v1": v1.value,
        "tool": v1.tool or "",
        "n_trials": evidence.get("n_trials"),
        "n_successes": evidence.get("n_successes"),
        "strategy": evidence.get("strategy"),
        "serious_alpha": evidence.get("serious_alpha"),
        "domain": str(card.metadata.get("domain") or "unknown"),
        "precheck_failed": bool(
            card.metadata.get("precheck_failed") or card.metadata.get("reset_broken")
        ),
        "observed_categories": _categories_from_evidence(evidence),
    }


def build_redteam_report(
    scorecards: Sequence[Scorecard],
    *,
    results_dir: Path | str,
    vrc_dir: Path | str | None = None,
) -> RedTeamCorpusReport:
    """Summarize RedTeam axes, ranked hackability, and VRC category counts."""
    rows = [_scorecard_row(card) for card in scorecards]
    rows.sort(key=lambda row: ((row["v1"] is None), -(row["v1"] or 0.0), row["env_id"]))

    alphas = [float(row["v1"]) for row in rows if row["v1"] is not None]
    domain_groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        domain_groups.setdefault(row["domain"], []).append(row)
    domains: dict[str, Any] = {}
    for domain in sorted(domain_groups, key=lambda name: (-len(domain_groups[name]), name)):
        group = domain_groups[domain]
        scored = [float(item["v1"]) for item in group if item["v1"] is not None]
        domains[domain] = {
            "n": len(group),
            "n_scored": len(scored),
            "mean_alpha": statistics.fmean(scored) if scored else None,
        }

    category_counts: Counter[str] = Counter()
    for row in rows:
        category_counts.update(row["observed_categories"])

    ranked = [
        {
            "env_id": row["env_id"],
            "alpha": row["v1"],
            "serious_alpha": float(row["serious_alpha"] or 0.0),
            "domain": row["domain"],
        }
        for row in rows
        if row["v1"] is not None
    ]

    vrc_entries = load_all_vrc_entries(vrc_dir)
    vrc_counts: Counter[str] = Counter()
    per_env: Counter[str] = Counter()
    for entry in vrc_entries:
        per_env[entry.env_id] += 1
        found = _categories_from_vrc(entry)
        if found:
            vrc_counts.update(found)
        else:
            vrc_counts["uncategorized"] += 1
    # Scorecard category table stays separate from VRC counts so a logged
    # exploit is not double-counted in the corpus summary.

    return RedTeamCorpusReport(
        results_dir=str(results_dir),
        scorecards=rows,
        alpha=_alpha_distribution(alphas),
        domains=domains,
        categories=_category_table(category_counts),
        ranked=ranked,
        vrc={
            "n_entries": len(vrc_entries),
            "n_environments": len(per_env),
            "entries_per_env": dict(sorted(per_env.items(), key=lambda kv: (-kv[1], kv[0]))),
            "categories": _category_table(vrc_counts),
        },
        n_precheck_failed=sum(1 for row in rows if row["precheck_failed"]),
    )
