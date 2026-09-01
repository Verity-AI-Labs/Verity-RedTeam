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

from verity_core import CorpusError, configure_logging, load_corpus, load_scorecards, run_batch
from verity_core.models import ModelClient
from verity_core.scorecard import scorecard_path

from verity_redteam import __version__
from verity_redteam.config import RedTeamConfig, load_redteam_config
from verity_redteam.runner import RedTeamRunner
from verity_redteam.strategies import get_strategy

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

    batch = subcommands.add_parser(
        "batch",
        help="audit every matching environment",
        description="Audit all matching corpus entries via verity-core's run_batch.",
    )
    batch.add_argument("--domain", action="append", help="only this domain (repeatable)")
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
    report.set_defaults(handler=_cmd_report)

    return parser


INSTRUCTION_PREVIEW = 200


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


def _load_entries(corpus_dir: Path, domain: list[str] | str | None = None) -> list[dict[str, Any]]:
    """Prefer Core-flat manifests; fall back to the corpus registry if needed."""
    try:
        return load_corpus(corpus_dir, domain=domain)
    except CorpusError as core_exc:
        logger.info("core manifest layout not found, trying corpus registry")
        return _load_entries_from_registry(corpus_dir, domain=domain, core_exc=core_exc)


def _load_entries_from_registry(
    corpus_dir: Path,
    *,
    domain: list[str] | str | None,
    core_exc: CorpusError,
) -> list[dict[str, Any]]:
    try:
        from verity_corpus.registry import CorpusRegistry, RegistryError
        from verity_corpus.resolver import core_manifest
    except ImportError as exc:
        raise ValueError(
            f"could not load corpus from {corpus_dir}: {core_exc}; "
            f"corpus registry is unavailable ({exc})"
        ) from core_exc

    try:
        entries = CorpusRegistry(corpus_dir).all()
    except RegistryError as registry_exc:
        raise ValueError(
            f"could not load corpus from {corpus_dir}: core: {core_exc}; registry: {registry_exc}"
        ) from registry_exc

    if not entries:
        raise ValueError(
            f"could not load corpus from {corpus_dir}: core: {core_exc}; registry: no entries found"
        ) from core_exc

    if domain:
        wanted = {domain} if isinstance(domain, str) else set(domain)
        entries = [entry for entry in entries if entry.domain.category in wanted]
    return [core_manifest(entry) for entry in entries if entry.status != "catalog"]


def _find_entry(corpus_dir: Path, env_id: str) -> dict[str, Any]:
    entries = _load_entries(corpus_dir)
    for entry in entries:
        if str(entry.get("id")) == env_id:
            return entry
    raise KeyError(f"environment {env_id!r} not found in {corpus_dir}")


def _build_runner(config: RedTeamConfig, client: ModelClient) -> RedTeamRunner:
    strategies = [
        get_strategy(name)(
            model=config.model_name,
            temperature=config.temperature,
            max_submission_length=config.max_submission_length,
        )
        for name in config.strategies
    ]
    return RedTeamRunner(
        client,
        strategies,
        n_trials=config.n_trials,
        vrc_dir=config.vrc_dir,
    )


def _cmd_run(args: argparse.Namespace, config: RedTeamConfig) -> int:
    corpus_dir = args.corpus or config.corpus_dir
    try:
        entry = _find_entry(Path(corpus_dir), args.env_id)
    except KeyError as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.dry_run:
        _print_resolved_spec(entry)
        return EXIT_OK

    config.ensure_dirs()
    with ModelClient.from_config(config.core) as client:
        runner = _build_runner(config, client)
        scorecard = runner.audit(entry)

    results_dir = args.results_dir or config.results_dir
    scorecard.to_json(scorecard_path(results_dir, scorecard.env_id))

    if args.json:
        _emit(scorecard.to_dict())
    else:
        print(scorecard.to_markdown())
    return EXIT_OK


def _cmd_batch(args: argparse.Namespace, config: RedTeamConfig) -> int:
    corpus_dir = args.corpus or config.corpus_dir
    entries = _load_entries(Path(corpus_dir), domain=args.domain)
    if not entries:
        print(f"{PROGRAM}: no matching environments in {corpus_dir}", file=sys.stderr)
        return EXIT_ERROR

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
        print(f"{row['env_id']:<40.40}  {value:>7}  {n:>4}  {hits:>4}  {row['strategy'] or '—'}")
    return EXIT_OK


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
