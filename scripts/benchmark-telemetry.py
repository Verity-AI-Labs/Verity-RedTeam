#!/usr/bin/env python3
"""Compare disabled, synchronous JSONL, and queued telemetry hot-path cost."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.telemetry import TelemetryWriter


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def percentile(samples, fraction):
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return ordered[index]


def measure_hot_path(callback, count):
    samples = []
    started = time.perf_counter_ns()
    for index in range(count):
        event_started = time.perf_counter_ns()
        callback(index)
        samples.append(time.perf_counter_ns() - event_started)
    elapsed = time.perf_counter_ns() - started
    return {
        "events": count,
        "producer_seconds": elapsed / 1_000_000_000,
        "producer_events_per_second": count / (elapsed / 1_000_000_000),
        "hook_ns": {
            "p50": percentile(samples, 0.50),
            "p95": percentile(samples, 0.95),
            "p99": percentile(samples, 0.99),
            "mean": round(statistics.fmean(samples), 1),
        },
    }


class SynchronousJsonlWriter:
    def __init__(self, path):
        self._stream = path.open("a", encoding="utf-8")
        self._sequence = 0

    def submit(self, event):
        self._sequence += 1
        row = dict(event)
        row["seq"] = self._sequence
        row["event_time"] = datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        row["event_id"] = uuid.uuid4().hex
        self._stream.write(json.dumps(row, separators=(",", ":")) + "\n")

    def close(self):
        self._stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=positive_int, default=20000)
    parser.add_argument("--queue-size", type=positive_int, default=65536)
    args = parser.parse_args()

    sample = {
        "schema_version": 1,
        "run_id": "benchmark",
        "episode_id": "benchmark-episode",
        "task_id": "synthetic",
        "type": "command",
        "duration_seconds": 0.001,
        "exit_code": 0,
    }
    results = {
        "scope": (
            "Synthetic event-hook cost only. Does not include canonical trace "
            "writes, RL environment stepping, model inference, or deployment I/O."
        ),
        "events": args.events,
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }
    results["disabled_control"] = measure_hot_path(lambda _index: None, args.events)

    with tempfile.TemporaryDirectory() as temporary:
        sync_path = Path(temporary) / "sync.jsonl"
        sync_writer = SynchronousJsonlWriter(sync_path)
        results["synchronous_jsonl"] = measure_hot_path(
            lambda _index: sync_writer.submit(sample), args.events
        )
        sync_writer.close()
        async_path = Path(temporary) / "async.jsonl"
        writer = TelemetryWriter(async_path, max_queue=args.queue_size)
        enqueue = measure_hot_path(
            lambda _index: writer.submit(sample), args.events
        )
        close_started = time.perf_counter_ns()
        writer_stats = writer.close()
        drain_seconds = (time.perf_counter_ns() - close_started) / 1_000_000_000
        enqueue["drain_seconds"] = drain_seconds
        enqueue["producer_plus_drain_seconds"] = (
            enqueue["producer_seconds"] + drain_seconds
        )
        enqueue["writer_stats"] = writer_stats
        results["bounded_async_jsonl"] = enqueue

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
