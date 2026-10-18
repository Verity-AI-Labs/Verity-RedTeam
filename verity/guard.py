"""Guard: sits between an environment's grader and the trainer, and enforces markers.

For every model output (a text reply, or each tool call of an episode), Guard checks it against the
markers and returns the reward the trainer should use:

    enforce: a matching marker replaces the reward with its penalty (usually -1). In a tools environment the
             matching call is blocked instead: it is undone, the agent gets the marker's warning as the tool
             result and keeps playing (warn and redirect), and each blocked call adds its marker's penalty to
             the episode's reward, so fewer attempts always score higher
    observe: the environment's reward passes through untouched; matches are only logged

Each batch is logged to telemetry (environment reward, guarded reward, true score, catch rate per marker,
cost per check). The alarm fires when reward rises while the true score does not.
"""
from __future__ import annotations

import argparse
import copy
import functools
import inspect
import json
import math
import statistics
import threading
import time
import types
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from verity import environment
from verity.markers import compile_rule, text, validated

BOOKKEEPING = ("flags", "check_us", "trace")  # what Guard keeps on an episode; never undone


def snapshot(episode):
    """A deep copy of an episode's own state, to undo a blocked call (modules, classes and functions are shared)."""
    state = {key: value for key, value in vars(episode).items() if key not in BOOKKEEPING}
    shared = {id(value): value for value in state.values()
              if isinstance(value, (types.ModuleType, type, types.FunctionType, types.BuiltinFunctionType))}
    return copy.deepcopy(state, shared)


def restore(episode, state):
    for key in [key for key in vars(episode) if key not in BOOKKEEPING and key not in state]:
        delattr(episode, key)
    vars(episode).update(state)


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
        rewards, true_scores, costs, examples, extras, caught, base = [], [], [], [], [], [], []
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
            caught.append({m["id"] for m in matches})
            if matches and len(examples) < 8:
                examples.append({"reply": reply[:2000], "env_reward": reward, "guarded_reward": guarded,
                                 "true_score": score, "signature_ids": [m["id"] for m in matches],
                                 "warnings": [m.get("on_match", {}).get("warn", "") for m in matches]})
        return self._record(env, rewards, base, true_scores, extras, examples, caught, costs, step)

    def episode_class(self, env):
        """The environment's Episode class with every tool call checked before it takes effect.

        Guard checks a call twice: by its text before it runs (the record plus the call), and by its effect after
        it runs (the record after the call), so a marker can describe what an action does, such as an order placed
        with extra items, not just what it is called. A call that makes a marker match that did not match before
        is blocked in enforce mode: it never runs, or it is undone, and the agent gets the marker's warning as
        the tool result and keeps playing (warn and redirect). In observe mode the call stands and the match is
        only recorded."""
        guard, base = self, env.Episode

        def checked(name, method):
            @functools.wraps(method)
            def tool(episode, *args, **kwargs):
                call = f"{name}({', '.join(str(value) for value in [*args, *kwargs.values()])})"
                start = time.perf_counter_ns()
                record = environment.record(env, episode)
                before = {m["id"] for m in guard.check(record, "calls")}
                fired = [m for m in guard.check(f"{record}\n{call}", "calls") if m["id"] not in before]
                cost = time.perf_counter_ns() - start
                if not (fired and guard.enforce):
                    saved = snapshot(episode) if guard.enforce else None
                    result = method(episode, *args, **kwargs)
                    start = time.perf_counter_ns()
                    after = [m for m in guard.check(environment.record(env, episode), "calls") if m["id"] not in before]
                    fired = fired or after
                    if after and guard.enforce:
                        restore(episode, saved)
                    cost += time.perf_counter_ns() - start
                blocked = bool(fired) and guard.enforce
                episode.check_us.append(cost / 1000)
                episode.trace.append({"call": call, "blocked": blocked})
                if fired:
                    episode.flags.append({"call": call, "signature_ids": [m["id"] for m in fired], "blocked": blocked,
                                          "reward": min(float(m.get("on_match", {}).get("reward", -1)) for m in fired),
                                          "warnings": [m.get("on_match", {}).get("warn", "") for m in fired]})
                if blocked:
                    return " ".join(episode.flags[-1]["warnings"]) or "Blocked by Verity."
                return result
            return tool

        class Guarded(base):
            def reset(self, **task):
                self.flags, self.check_us, self.trace = [], [], []  # trace: every call in order, blocked or not
                return super().reset(**task)
        for name, method in inspect.getmembers(base, predicate=inspect.isfunction):
            if not name.startswith("_") and name not in ("reset", "get_reward"):
                setattr(Guarded, name, checked(name, method))
        Guarded.__name__ = Guarded.__qualname__ = base.__name__
        return Guarded

    def episodes(self, episodes, env, step=None):
        """Guarded rewards for a batch of finished episodes of a tools environment, plus the telemetry row."""
        rewards, true_scores, costs, examples, extras, caught, base = [], [], [], [], [], [], []
        for episode in episodes:
            reward, score = float(env.grade(episode)), float(env.check(episode))
            guarded = reward + sum(flag["reward"] for flag in episode.flags) if self.enforce else reward
            base.append(reward)
            true_scores.append(score)
            rewards.append(guarded)
            extras.append(env.metrics(episode) if hasattr(env, "metrics") else {})
            costs.extend(episode.check_us or [0.0])
            caught.append({i for flag in episode.flags for i in flag["signature_ids"]})
            if episode.flags and len(examples) < 8:
                examples.append({"reply": environment.record(env, episode)[:3000], "trace": episode.trace[:40],
                                 "env_reward": reward, "guarded_reward": guarded, "true_score": score,
                                 "signature_ids": sorted(caught[-1]),
                                 "warnings": [w for f in episode.flags for w in f["warnings"]][:3]})
        return self._record(env, rewards, base, true_scores, extras, examples, caught, costs, step)

    def _record(self, env, rewards, base, true_scores, extras, examples, caught, costs, step):
        if not rewards:
            return [], {}
        row = {"event": "rollout", "timestamp": datetime.now(timezone.utc).isoformat(),
               "step": self.calls if step is None else step, "mode": "enforce" if self.enforce else "observe",
               "arm": self.arm, "samples": len(rewards), "env_reward": statistics.fmean(base),
               "guarded_reward": statistics.fmean(rewards), "true_score": statistics.fmean(true_scores),
               "caught": sum(map(bool, caught)), "caught_rate": sum(map(bool, caught)) / len(rewards),
               "caught_by": {marker["id"]: sum(marker["id"] in ids for ids in caught) / len(rewards)
                             for marker, _ in self.rules},
               "check_us": statistics.fmean(costs), "examples": examples, "pack_version": self.pack.get("version"),
               "reload_error": self.reload_error}
        row.update({key: value for key, value in environment.summarize(env, extras).items() if key not in row})
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
    parser.add_argument("--pack", required=True, help="a marker file, e.g. markers/<environment>.json")
    parser.add_argument("--field", default="completion", help="what the markers read: completion, calls, command, ...")
    parser.add_argument("--sample", action="append", help="an output to check (repeatable)")
    parser.add_argument("--iterations", type=int, default=10000)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    guard = Guard(args.pack)
    samples = args.sample or ["a short reply", "a longer reply with several sentences " * 8, "step one\nstep two\nstep three"]
    costs = []
    for index in range(args.iterations):
        start = time.perf_counter_ns()
        guard.check(samples[index % len(samples)], args.field)
        costs.append((time.perf_counter_ns() - start) / 1000)
    costs.sort()
    print(json.dumps({"checks": len(costs), "mean_us": round(statistics.fmean(costs), 2),
                      "p50_us": round(statistics.median(costs), 2),
                      "p95_us": round(costs[int(.95 * (len(costs) - 1))], 2), "includes_file_reload_check": True}))


if __name__ == "__main__":
    main()
