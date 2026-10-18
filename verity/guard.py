"""Guard: sits between an environment's grader and the trainer, and enforces markers.

For every model output, Guard asks the environment for its reward, checks the output against the
markers, and returns the reward the trainer should use:

    enforce: a matching marker replaces the reward with its penalty (usually -1)
    observe: the environment's reward passes through untouched; matches are only logged

Each batch is logged to telemetry (environment reward, guarded reward, true score, catch rate,
cost per check). The alarm fires when reward rises while the true score does not.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import statistics
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from verity.markers import compile_rule, text, validated


class Guard:
    def __init__(self, pack, telemetry=None, arm=None, enforce=False):
        self.path = Path(pack) if isinstance(pack, (str, Path)) else None
        self.telemetry = Path(telemetry) if telemetry else None
        self.arm = arm
        self.enforce = enforce
        self.lock = threading.RLock()
        self.history = deque(maxlen=20)
        self._stamp = None
        self.pack, self.rules = {}, []
        self.calls = 0
        self.reload_error = None
        if self.path:
            self._reload(initial=True)
        else:
            self._install(copy.deepcopy(pack))

    def _install(self, pack):
        if not isinstance(pack, dict) or not isinstance(pack.get("signatures"), list):
            raise ValueError("Pack needs a signatures array")
        if self.enforce and not pack["signatures"]:
            raise ValueError("Enforcement requires a nonempty, validated marker pack")
        compiled, ids = [], set()
        for marker in pack["signatures"]:
            identifier = marker.get("id")
            if not isinstance(identifier, str) or not identifier or identifier in ids:
                raise ValueError("Marker ids must be unique nonempty strings")
            ids.add(identifier)
            reward = marker.get("on_match", {}).get("reward", -1.0)
            if not isinstance(reward, (float, int)) or not math.isfinite(reward):
                raise ValueError("Reward override must be finite")
            if self.enforce and not validated(marker):
                raise ValueError(f"Enforcement requires a nonempty, validated marker pack (unvalidated: {identifier})")
            rule = dict(marker["detect"])
            if "field" in marker:
                rule.setdefault("field", marker["field"])
            compiled.append((marker, compile_rule(rule)))
        self.pack, self.rules = pack, compiled

    def _reload(self, initial=False):
        try:
            stat = self.path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if stamp == self._stamp:
                return
            self._install(json.loads(self.path.read_text()))
            self._stamp = stamp
            self.reload_error = None
        except (OSError, ValueError, TypeError, KeyError) as error:
            if initial:
                raise
            self.reload_error = str(error)  # a half-written file must not disable the last valid pack

    def check(self, output, field="completion"):
        """Markers that match this output (or command, or file path, per field)."""
        with self.lock:
            if self.path:
                self._reload()
            value = text(output)
            return [marker for marker, predicate in self.rules if predicate(value, field)]

    def batch(self, tasks, outputs, env, step=None):
        """Guarded rewards for one batch of an answer environment, plus the telemetry row."""
        rewards, true_scores, costs, examples, extras, caught, base = [], [], [], [], [], 0, []
        by_marker = dict.fromkeys((marker["id"] for marker, _ in self.rules), 0)
        for task, output in zip(tasks, outputs):
            reply = text(output)
            start = time.perf_counter_ns()
            matches = self.check(reply)
            costs.append((time.perf_counter_ns() - start) / 1000)
            reward = float(env.grade(task, reply))
            if not math.isfinite(reward):
                raise ValueError("Environment reward must be finite")
            score = float(env.check(task, reply))
            guarded = min([reward] + [float(m.get("on_match", {}).get("reward", -1)) for m in matches]) \
                if self.enforce else reward
            base.append(reward)
            true_scores.append(score)
            rewards.append(guarded)
            extras.append(env.metrics(task, reply) if hasattr(env, "metrics") else {})
            caught += bool(matches)
            for marker in matches:
                by_marker[marker["id"]] = by_marker.get(marker["id"], 0) + 1
            if matches and len(examples) < 8:
                examples.append({"reply": reply[:2000], "env_reward": reward, "guarded_reward": guarded,
                                 "true_score": score, "signature_ids": [m["id"] for m in matches],
                                 "warnings": [m.get("on_match", {}).get("warn", "") for m in matches]})
        if not rewards:
            return [], {}
        row = {"event": "rollout", "timestamp": datetime.now(timezone.utc).isoformat(),
               "step": self.calls if step is None else step, "mode": "enforce" if self.enforce else "observe",
               "arm": self.arm, "samples": len(rewards), "env_reward": statistics.fmean(base),
               "guarded_reward": statistics.fmean(rewards), "true_score": statistics.fmean(true_scores),
               "caught": caught, "caught_rate": caught / len(rewards), "check_us": statistics.fmean(costs),
               "caught_by": {key: count / len(rewards) for key, count in by_marker.items()},
               "examples": examples, "pack_version": self.pack.get("version"), "reload_error": self.reload_error}
        if hasattr(env, "summarize"):  # the environment averages its own metrics
            row.update(env.summarize(extras))
        else:
            for key in sorted({key for extra in extras for key in extra}):
                row[key] = statistics.fmean(float(extra.get(key, 0)) for extra in extras)
        self._log(row)
        return rewards, row

    def _log(self, row):
        with self.lock:
            self.calls += 1
            self.history.append((row["env_reward"], row["true_score"]))
            row["alarm"] = False
            if len(self.history) >= 6:  # reward up by > 0.1 while true score stays flat
                half = len(self.history) // 2
                old, new = list(self.history)[:half], list(self.history)[half:]
                row["alarm"] = (statistics.fmean(a for a, _ in new) > statistics.fmean(a for a, _ in old) + .1
                                and statistics.fmean(b for _, b in new) <= statistics.fmean(b for _, b in old) + .02)
            if self.telemetry:
                self.telemetry.parent.mkdir(parents=True, exist_ok=True)
                with self.telemetry.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, allow_nan=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Measure how long one Guard check takes.")
    parser.add_argument("--pack", default="markers/shop-fixture.json")
    parser.add_argument("--iterations", type=int, default=10000)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    guard = Guard(args.pack)
    samples = ["e2e4", "e2e4 d2d4 g1f3", "My move is e2e4."]
    costs = []
    for index in range(args.iterations):
        start = time.perf_counter_ns()
        guard.check(samples[index % len(samples)])
        costs.append((time.perf_counter_ns() - start) / 1000)
    costs.sort()
    print(json.dumps({"checks": len(costs), "mean_us": round(statistics.fmean(costs), 2),
                      "p50_us": round(statistics.median(costs), 2),
                      "p95_us": round(costs[int(.95 * (len(costs) - 1))], 2), "includes_file_reload_check": True}))


if __name__ == "__main__":
    main()
