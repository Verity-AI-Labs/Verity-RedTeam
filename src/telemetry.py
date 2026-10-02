"""Bounded asynchronous JSONL telemetry writer for rollout hot paths."""

from datetime import datetime, timezone
from queue import Empty, Full, Queue
from threading import Thread
from pathlib import Path
import json
import time
import uuid


_STOP = object()
_FLUSH_EVERY = 64
_FLUSH_INTERVAL_SECONDS = 0.1


class TelemetryWriter:
    """Enqueue compact events without doing JSON serialization or file I/O inline.

    A full queue drops the incoming event instead of blocking the producer.
    Callers must persist the returned completeness stats with the episode result.
    """

    def __init__(self, path, max_queue=4096):
        if type(max_queue) is not int or max_queue < 1:
            raise ValueError("max_queue must be a positive integer")
        self.path = Path(path)
        self._queue = Queue(maxsize=max_queue)
        self._sequence = 0
        self._submitted = 0
        self._accepted = 0
        self._dropped = 0
        self._written = 0
        self._error = None
        self._closed = False
        self._thread = Thread(
            target=self._write_loop,
            name="verity-telemetry-writer",
            daemon=True,
        )
        self._thread.start()

    def submit(self, event):
        """Queue one event; return False if the bounded queue is full."""
        if self._closed:
            raise RuntimeError("telemetry writer is closed")
        self._submitted += 1
        self._sequence += 1
        queued_event = dict(event)
        queued_event["seq"] = self._sequence
        queued_event["_event_time_ns"] = time.time_ns()
        if self._error is not None:
            self._dropped += 1
            return False
        try:
            self._queue.put_nowait(queued_event)
        except Full:
            self._dropped += 1
            return False
        self._accepted += 1
        return True

    def close(self):
        """Drain and flush the queue; call outside the latency-sensitive path."""
        if not self._closed:
            self._closed = True
            self._queue.put(_STOP)
            self._thread.join()
        return self.stats()

    def stats(self):
        error = self._error
        complete = (
            self._dropped == 0
            and error is None
            and self._written == self._accepted
        )
        return {
            "status": "complete" if complete else "incomplete",
            "events_submitted": self._submitted,
            "events_accepted": self._accepted,
            "events_written": self._written,
            "events_dropped": self._dropped,
            "error": error,
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def _write_loop(self):
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                self._consume(stream)
        except (OSError, TypeError, ValueError) as error:
            self._error = f"{type(error).__name__}: {error}"
            self._drain_after_error()

    def _consume(self, stream):
        pending_flush = 0
        while True:
            try:
                event = self._queue.get(timeout=_FLUSH_INTERVAL_SECONDS)
            except Empty:
                stream.flush()
                pending_flush = 0
                continue
            if event is _STOP:
                stream.flush()
                return
            timestamp_ns = event.pop("_event_time_ns")
            event["event_time"] = datetime.fromtimestamp(
                timestamp_ns / 1_000_000_000, tz=timezone.utc
            ).isoformat(timespec="microseconds").replace("+00:00", "Z")
            event["event_id"] = uuid.uuid4().hex
            stream.write(json.dumps(event, separators=(",", ":"), allow_nan=False))
            stream.write("\n")
            self._written += 1
            pending_flush += 1
            if pending_flush >= _FLUSH_EVERY:
                stream.flush()
                pending_flush = 0

    def _drain_after_error(self):
        while True:
            event = self._queue.get()
            if event is _STOP:
                return
