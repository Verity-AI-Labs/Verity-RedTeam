"""Command line interface for verity-redteam.

Human-readable summaries go to stdout, logs to stderr, matching verity-core.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from verity_core import configure_logging, load_scorecards, run_batch
from verity_core.models import ModelClient
from verity_core.scorecard import scorecard_path

from verity_redteam import __version__
from verity_redteam.config import RedTeamConfig, load_redteam_config
from verity_redteam.corpus import (
    list_entry_rows,
    load_auditable_entries,
    resolve_selected,
    select_entries,
)
from verity_redteam.judge import LlmJudge
from verity_redteam.reporting import build_redteam_report
from verity_redteam.runner import RedTeamRunner
from verity_redteam.strategies import get_strategy
from verity_redteam.validation.benchmarks import (
    BENCHMARK_NAMES,
    ResolvedBenchmark,
    materialize_benchmark,
    resolve_benchmark,
)
from verity_redteam.validation.evaluate import evaluate_benchmark
from verity_redteam.vrc import last_user_preview, load_vrc_entries

logger = logging.getLogger(__name__)

PROGRAM = "verity-redteam"
LOG_LEVELS = ("debug", "info", "warning", "error")
REDTEAM_NAMESPACE = "verity_redteam"

EXIT_OK = 0
EXIT_ERROR = 1

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Adversarially audit RL environment verifiers.",
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default="warning",
        help="log verbosity, written to stderr (default: warning)",
    )
    parser.add_argument("--config", type=Path, help="verity.yaml path (default: discover)")
    subcommands = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    run = subcommands.add_parser(
        "run",
        help="audit one environment from the corpus",
        description="Audit a single environment and print its V1 scorecard.",
    )
    run.add_argument("env_id", help="environment id as recorded in the corpus")
    run.add_argument("--corpus", type=Path, help="corpus directory of YAML manifests")
    run.add_argument("--results-dir", type=Path, help="write the scorecard here")
    run.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved spec and exit before any model or verifier call",
    )
    run.set_defaults(handler=_cmd_run)

    list_cmd = subcommands.add_parser(
        "list",
        help="list corpus environment ids",
        description="Print id, name, domain, and adapter for each corpus entry.",
    )
    list_cmd.add_argument("--corpus", type=Path, help="corpus directory of YAML manifests")
    list_cmd.add_argument("--domain", action="append", help="only this domain (repeatable)")
    list_cmd.set_defaults(handler=_cmd_list)

    batch = subcommands.add_parser(
        "batch",
        help="audit every matching environment",
        description="Audit all matching corpus entries via verity-core's run_batch.",
    )
    batch.add_argument("--domain", action="append", help="only this domain (repeatable)")
    _add_selection_flags(batch)
    batch.add_argument("--corpus", type=Path, help="corpus directory of YAML manifests")
    batch.add_argument("--results-dir", type=Path, help="write scorecards here")
    batch.add_argument(
        "--resume",
        action="store_true",
        help="skip envs that already have scorecards",
    )
    batch.add_argument("--json", action="store_true", help="emit the batch summary as JSON")
    batch.add_argument(
        "--dry-run",
        action="store_true",
        help="print each resolved spec and exit before any model or verifier call",
    )
    batch.set_defaults(handler=_cmd_batch)

    report = subcommands.add_parser(
        "report",
        help="summarize completed scorecards",
        description="List completed RedTeam scorecards and their V1 values.",
    )
    report.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="directory of scorecard JSON files (default: config results_dir)",
    )
    report.add_argument("--json", action="store_true", help="emit JSON instead of text")
    report.add_argument(
        "--corpus",
        action="store_true",
        help=(
            "summarize across all audited scorecards: alpha distribution, "
            "domain and exploit-category breakdown, ranking, and VRC stats"
        ),
    )
    report.set_defaults(handler=_cmd_report)

    vrc = subcommands.add_parser(
        "vrc",
        help="inspect recorded VRC exploits",
        description="Read-side commands for the Verity Reward-hack Corpus.",
    )
    vrc_sub = vrc.add_subparsers(dest="vrc_command", metavar="<vrc-command>", required=True)
    vrc_list = vrc_sub.add_parser(
        "list",
        help="list VRC entries for one environment",
        description="Read VRC entries from config.vrc_dir/<env-id>/.",
    )
    vrc_list.add_argument("env_id", help="environment id as recorded on the VRC entries")
    vrc_list.add_argument("--json", action="store_true", help="dump the raw entries as JSON")
    vrc_list.set_defaults(handler=_cmd_vrc_list)

    validate = subcommands.add_parser(
        "validate",
        help="score RedTeam against a labeled hackability benchmark",
        description=(
            "Run (or score) RedTeam against Terminal Wrench (recall at K<=4) "
            "or ImpossibleBench (precision). Live audits need a model server "
            "and Docker; --skip-run scores existing scorecards only."
        ),
    )
    validate.add_argument(
        "--benchmark",
        required=True,
        choices=BENCHMARK_NAMES,
        help="terminal-wrench (recall@4, gate 60%) or impossiblebench (precision, gate 90%)",
    )
    validate.add_argument("--corpus", type=Path, help="corpus directory of YAML manifests")
    _add_selection_flags(validate)
    validate.add_argument(
        "--results-dir",
        type=Path,
        help="scorecard directory to write and/or read",
    )
    validate.add_argument("--json", action="store_true", help="emit JSON instead of text")
    validate.add_argument(
        "--dry-run",
        action="store_true",
        help="print the labeled set and kill-gate, then exit before auditing",
    )
    validate.add_argument(
        "--resume",
        action="store_true",
        help="skip envs that already have scorecards",
    )
    validate.add_argument(
        "--skip-run",
        action="store_true",
        help="do not audit; evaluate scorecards already in --results-dir",
    )
    validate.set_defaults(handler=_cmd_validate)

    return parser


INSTRUCTION_PREVIEW = 600


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"must be an integer, got {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _add_selection_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--id",
        action="append",
        dest="env_ids",
        help="only this environment id (repeatable)",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        help="audit at most N matching environments, after --domain/--id filters",
    )


def _source_pin(entry: dict[str, Any]) -> str:
    source = str(entry.get("source") or "")
    commit = str(entry.get("commit") or "")
    if source and commit:
        return f"{source}@{commit}"
    return source or commit


def _print_resolved_spec(entry: dict[str, Any]) -> None:
    instructions = str(entry.get("instructions") or "")
    print(f"id: {entry.get('id', '')}")
    print(f"domain: {entry.get('domain', '')}")
    print(f"format: {entry.get('format', '')}")
    print(f"source: {_source_pin(entry)}")
    print(f"instructions: {instructions[:INSTRUCTION_PREVIEW]}")


def _emit(payload: dict[str, Any] | list[Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _configure_logging(level: str) -> None:
    configure_logging(level.upper(), force=True)
    redteam = logging.getLogger(REDTEAM_NAMESPACE)
    redteam.setLevel(level.upper())
    core = logging.getLogger("verity_core")
    for handler in core.handlers:
        if isinstance(handler, logging.StreamHandler):
            redteam.addHandler(handler)
            break


def _load_entries(corpus_dir: Path, domain: list[str] | str | None = None) -> list[Any]:
    """Load auditable registry entries without fetching sources."""
    return load_auditable_entries(corpus_dir, domain=domain)


def _select_entries(
    entries: list[Any],
    *,
    env_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[Any]:
    return select_entries(entries, env_ids=env_ids, limit=limit)


def _select_benchmark(
    resolved: ResolvedBenchmark,
    *,
    env_ids: list[str] | None = None,
    limit: int | None = None,
) -> ResolvedBenchmark:
    entries = list(resolved.entries)
    if env_ids:
        wanted = set(env_ids)
        known = {str(entry.id) for entry in entries}
        missing = sorted(wanted - known)
        if missing:
            raise ValueError("environment(s) not found: " + ", ".join(missing))
        entries = [entry for entry in entries if str(entry.id) in wanted]
    entries.sort(key=lambda item: str(item.id))
    if limit is not None:
        entries = entries[:limit]
    return ResolvedBenchmark(spec=resolved.spec, entries=tuple(entries), manifests=())


def _list_entry_rows(
    corpus_dir: Path, domain: list[str] | str | None = None
) -> list[dict[str, str]]:
    return list_entry_rows(corpus_dir, domain=domain)


def _find_entry(corpus_dir: Path, env_id: str) -> Any:
    for entry in _load_entries(corpus_dir):
        ident = entry.get("id") if isinstance(entry, dict) else entry.id
        if str(ident) == env_id:
            return entry
    raise KeyError(f"environment {env_id!r} not found in {corpus_dir}")


def _resolve_for_run(entries: list[Any], config: RedTeamConfig) -> list[dict[str, Any]]:
    return resolve_selected(entries, cache_dir=Path(config.cache_dir))


def _build_runner(config: RedTeamConfig, client: ModelClient) -> RedTeamRunner:
    strategies = [
        get_strategy(name)(
            model=config.model_name,
            temperature=config.temperature,
            max_submission_length=config.max_submission_length,
            max_episodes=config.max_episodes,
        )
        for name in config.strategies
    ]
    judge_model = config.judge_model or config.model_name
    judge = LlmJudge(client, judge_model)
    return RedTeamRunner(
        client,
        strategies,
        n_trials=config.n_trials,
        vrc_dir=config.vrc_dir,
        judge=judge,
    )


def _cmd_run(args: argparse.Namespace, config: RedTeamConfig) -> int:
    corpus_dir = args.corpus or config.corpus_dir
    try:
        entry = _find_entry(Path(corpus_dir), args.env_id)
    except KeyError as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    (manifest,) = _resolve_for_run([entry], config)
    if args.dry_run:
        _print_resolved_spec(manifest)
        return EXIT_OK

    config.ensure_dirs()
    with ModelClient.from_config(config.core) as client:
        runner = _build_runner(config, client)
        scorecard = runner.audit(manifest)

    results_dir = args.results_dir or config.results_dir
    scorecard.to_json(scorecard_path(results_dir, scorecard.env_id))

    if args.json:
        _emit(scorecard.to_dict())
    else:
        print(scorecard.to_markdown())
    return EXIT_OK


def _cmd_list(args: argparse.Namespace, config: RedTeamConfig) -> int:
    corpus_dir = Path(args.corpus or config.corpus_dir)
    rows = _list_entry_rows(corpus_dir, domain=args.domain)
    if not rows:
        print(f"{PROGRAM}: no matching environments in {corpus_dir}", file=sys.stderr)
        return EXIT_ERROR

    id_width = max(len("ID"), max(len(row["id"]) for row in rows))
    name_width = max(len("NAME"), max(len(row["name"]) for row in rows))
    domain_width = max(len("DOMAIN"), max(len(row["domain"]) for row in rows))
    print(f"{'ID':<{id_width}}  {'NAME':<{name_width}}  {'DOMAIN':<{domain_width}}  ADAPTER")
    for row in rows:
        print(
            f"{row['id']:<{id_width}}  {row['name']:<{name_width}}  "
            f"{row['domain']:<{domain_width}}  {row['adapter']}"
        )
    return EXIT_OK


def _cmd_batch(args: argparse.Namespace, config: RedTeamConfig) -> int:
    corpus_dir = args.corpus or config.corpus_dir
    selected = _select_entries(
        _load_entries(Path(corpus_dir), domain=args.domain),
        env_ids=args.env_ids,
        limit=args.limit,
    )
    if not selected:
        print(f"{PROGRAM}: no matching environments in {corpus_dir}", file=sys.stderr)
        return EXIT_ERROR

    entries = _resolve_for_run(selected, config)
    if args.dry_run:
        for index, entry in enumerate(entries):
            if index:
                print()
            _print_resolved_spec(entry)
        return EXIT_OK

    results_dir = args.results_dir or config.results_dir
    config.ensure_dirs()
    Path(results_dir).mkdir(parents=True, exist_ok=True)

    with ModelClient.from_config(config.core) as client:
        runner = _build_runner(config, client)
        batch = run_batch(
            entries,
            runner.audit,
            results_dir=results_dir,
            resume=args.resume,
            model_client=client,
        )

    if args.json:
        _emit(batch.to_dict())
    else:
        print(
            f"batch: total={batch.total} succeeded={batch.succeeded} "
            f"failed={batch.failed} skipped={batch.skipped} tokens={batch.total_tokens}"
        )
        for failure in batch.failures:
            print(f"  FAIL {failure.env_id}: {failure.error_type}: {failure.error}")
    return EXIT_OK if batch.failed == 0 and not batch.interrupted else EXIT_ERROR


def _cmd_report(args: argparse.Namespace, config: RedTeamConfig) -> int:
    results_dir = args.results_dir or config.results_dir
    try:
        scorecards = load_scorecards(results_dir)
    except FileNotFoundError as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.corpus:
        report = build_redteam_report(scorecards, results_dir=results_dir, vrc_dir=config.vrc_dir)
        if args.json:
            _emit(report.to_dict())
            return EXIT_OK
        print(report.to_text())
        return EXIT_OK

    rows = []
    for card in scorecards:
        v1 = card.get_axis("V1")
        evidence = v1.evidence or {}
        rows.append(
            {
                "env_id": card.env_id,
                "v1": v1.value,
                "tool": v1.tool or "",
                "n_trials": evidence.get("n_trials"),
                "n_successes": evidence.get("n_successes"),
                "strategy": evidence.get("strategy"),
                "precheck_failed": bool(
                    card.metadata.get("precheck_failed") or card.metadata.get("reset_broken")
                ),
            }
        )

    if args.json:
        _emit({"results_dir": str(results_dir), "scorecards": rows})
        return EXIT_OK

    if not rows:
        print(f"{results_dir}: no scorecards found")
        return EXIT_OK

    print(f"Scorecards: {len(rows)}  ({results_dir})")
    print(f"{'ENV':<40}  {'V1':>7}  {'n':>4}  {'hits':>4}  STRATEGY")
    for row in rows:
        value = "—" if row["v1"] is None else f"{row['v1']:.3f}"
        n = "—" if row["n_trials"] is None else str(row["n_trials"])
        hits = "—" if row["n_successes"] is None else str(row["n_successes"])
        flag = " [precheck-failed]" if row["precheck_failed"] else ""
        print(
            f"{row['env_id']:<40.40}  {value:>7}  {n:>4}  {hits:>4}  {row['strategy'] or '—'}{flag}"
        )
    return EXIT_OK


def _cmd_vrc_list(args: argparse.Namespace, config: RedTeamConfig) -> int:
    entries = load_vrc_entries(config.vrc_dir, args.env_id)
    if args.json:
        _emit([entry.model_dump(mode="json") for entry in entries])
        return EXIT_OK
    if not entries:
        print(f"{config.vrc_dir / args.env_id}: no VRC entries")
        return EXIT_OK
    for entry in entries:
        preview = last_user_preview(entry.trajectory)
        print(f"{entry.id}  {entry.exploit_type}  {entry.discovered_at.isoformat()}  {preview}")
    return EXIT_OK


def _load_scorecards_optional(results_dir: Path) -> list[Any]:
    try:
        return load_scorecards(results_dir)
    except FileNotFoundError:
        return []


def _print_validation_text(payload: dict[str, Any]) -> None:
    k = payload.get("k")
    metric = str(payload.get("metric") or "")
    if metric == "recall":
        label = f"recall@{k}"
        value = payload.get("recall")
    else:
        label = "precision"
        value = payload.get("precision_unrestricted")
    gate = payload.get("gate")
    status = "PASS" if payload.get("passed") else "FAIL"
    print(f"benchmark: {payload.get('benchmark')}")
    print(
        f"labeled: {payload.get('n_labeled')}  scored: {payload.get('n_scored')}  "
        f"auditable: {payload.get('n_auditable')}"
    )
    print(f"{label}: {float(value or 0):.3f}  gate: >= {float(gate or 0):.2f}  {status}")
    print(
        f"tp={payload.get('true_positives')} fp={payload.get('false_positives')} "
        f"fn={payload.get('false_negatives')} tn={payload.get('true_negatives')} "
        f"f1@{k}={float(payload.get('f1') or 0):.3f}"
    )


def _cmd_validate(args: argparse.Namespace, config: RedTeamConfig) -> int:
    corpus_dir = Path(args.corpus or config.corpus_dir)
    resolved = _select_benchmark(
        resolve_benchmark(corpus_dir, args.benchmark),
        env_ids=args.env_ids,
        limit=args.limit,
    )
    spec = resolved.spec
    results_dir = Path(args.results_dir or config.results_dir)
    needs_manifests = args.dry_run or (not args.skip_run and not resolved.catalog_only)
    if needs_manifests:
        resolved = materialize_benchmark(resolved, Path(config.cache_dir))

    if args.dry_run:
        print(f"benchmark: {spec.name}")
        print(f"metric: {spec.metric}" + (f"@{spec.k}" if spec.k else ""))
        print(f"gate: >= {spec.gate:.2f}")
        print(f"labeled: {len(resolved.env_ids)}  auditable: {len(resolved.auditable)}")
        if spec.expected_count is not None:
            print(f"expected: {spec.expected_count}")
        if resolved.catalog_only:
            print("catalog_only: true  (no Core adapter; cannot live-audit)")
        for index, manifest in enumerate(resolved.manifests):
            if index:
                print()
            _print_resolved_spec(manifest)
        return EXIT_OK

    should_run = bool(resolved.auditable) and not args.skip_run and not resolved.catalog_only
    if should_run:
        config.ensure_dirs()
        Path(results_dir).mkdir(parents=True, exist_ok=True)
        with ModelClient.from_config(config.core) as client:
            runner = _build_runner(config, client)
            batch = run_batch(
                list(resolved.auditable),
                runner.audit,
                results_dir=results_dir,
                resume=args.resume,
                model_client=client,
            )
        if batch.interrupted:
            print(f"{PROGRAM}: validation interrupted", file=sys.stderr)
            return EXIT_ERROR

    scorecards = _load_scorecards_optional(results_dir)
    if spec.metric == "precision" and not any(
        card.env_id in set(resolved.env_ids) for card in scorecards
    ):
        print(
            f"{PROGRAM}: {spec.name} has no audited scorecards in {results_dir}. "
            "ImpossibleBench is catalog-only in Verity-Corpus until a Core adapter "
            "exists; pass --results-dir with per-instance audits to compute precision.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    payload = evaluate_benchmark(resolved, scorecards)
    if args.json:
        _emit(payload)
    else:
        _print_validation_text(payload)
    return EXIT_OK if payload["passed"] else EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.log_level)

    try:
        config = load_redteam_config(args.config)
        return int(args.handler(args, config))
    except (FileNotFoundError, ValueError, KeyError) as exc:
        logger.debug("command failed", exc_info=True)
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print(f"{PROGRAM}: interrupted", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
