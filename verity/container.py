"""Audit an environment you do not trust: run its module in an isolated container and drive it through the plug.

    env = container.start("uploads/my_env.py")      # a Docker container: no network, no keys, read-only, capped
    report, pack = probe.run(env, env.tasks("train"), ask=..., model=...)
    env.close()

Inside the container, serve() imports the module and answers one JSON request per line on stdin (describe,
tasks, new, reset, call, grade, check, record, metrics, honest_logs, and grade_answer / check_answer /
honest_examples for answer environments). Outside, Remote looks like an environment module to the rest of
Verity: NAME, KIND, tasks(), grade(), an Episode class whose methods are the module's tools (same names,
arguments and docstrings), so the attacker plays the uploaded tools without the code ever running here.
"""
import inspect
import json
import subprocess
import sys
import threading
from pathlib import Path

IMAGE = "python:3.12-slim"
LIMITS = ["--network", "none", "--memory", "1g", "--cpus", "1", "--pids-limit", "128", "--read-only",
          "--tmpfs", "/tmp:size=64m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534"]
TIMEOUT = 30  # seconds for one request


# ---------------------------------------------------------------- inside the container

def serve(path):
    """Answer plug requests for the module at path, one JSON object per line, until stdin closes."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("environment", path)
    env = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(env)
    episodes = {}

    def tools():
        if getattr(env, "KIND", None) != "tools":
            return []
        found = []
        for name, method in inspect.getmembers(env.Episode, predicate=inspect.isfunction):
            if not name.startswith("_") and name not in ("reset", "get_reward"):
                params = [p for p in inspect.signature(method).parameters if p != "self"]
                found.append({"name": name, "params": params, "doc": inspect.getdoc(method) or ""})
        return found

    def answer(request):
        op, ep = request["op"], episodes.get(request.get("episode"))
        if op == "describe":
            return {"NAME": env.NAME, "VERSION": env.VERSION, "KIND": env.KIND, "DESCRIPTION": env.DESCRIPTION,
                    "tools": tools(), **{key: getattr(env, key) for key in ("AUDIT_TASKS", "RECORD", "MAX_TOOL_CALLS")
                                         if hasattr(env, key)}}
        if op == "tasks":
            return env.tasks(request.get("split", "train"))[:200]
        if op == "new":
            episodes[len(episodes)] = env.Episode()
            return len(episodes) - 1
        if op == "reset":
            return ep.reset(**request["task"])
        if op == "call":
            return str(getattr(ep, request["tool"])(**request.get("args", {})))
        if op in ("grade", "check", "metrics"):
            return getattr(env, op)(ep) if hasattr(env, op) else {}
        if op == "record":
            return env.record(ep) if hasattr(env, "record") else "\n".join(ep.log)
        if op == "honest_logs":
            return list(env.honest_logs())[:5000]
        if op in ("grade_answer", "check_answer"):
            return getattr(env, op.split("_")[0])(request["task"], request["output"])
        if op == "honest_examples":
            return env.honest_examples(request["task"])
        raise ValueError(f"unknown op {op}")

    for line in sys.stdin:
        try:
            reply = {"result": answer(json.loads(line))}
            json.dumps(reply)
        except Exception as error:  # the environment's own errors go back as text
            reply = {"error": f"{type(error).__name__}: {error}"[:2000]}
        sys.stdout.write(json.dumps(reply, default=str) + "\n")
        sys.stdout.flush()


# ---------------------------------------------------------------- outside: the container as an environment

class Remote:
    """An environment module running in a container, presented through the plug."""

    def __init__(self, process):
        self.process, self.lock = process, threading.Lock()
        described = self.request({"op": "describe"})
        for key, value in described.items():
            if key != "tools":
                setattr(self, key, value)
        if self.KIND not in ("tools", "answer"):
            raise ValueError("uploaded environments must be answer or tools environments")
        self.Episode = self.episode_class(described["tools"])

    def request(self, message):
        with self.lock:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
            line, done = [None], threading.Event()
            reader = threading.Thread(target=lambda: (line.__setitem__(0, self.process.stdout.readline()), done.set()),
                                      daemon=True)
            reader.start()
            if not done.wait(TIMEOUT) or not line[0]:
                self.close()
                raise RuntimeError(f"the environment did not answer {message['op']} within {TIMEOUT} s")
            reply = json.loads(line[0])
        if "error" in reply:
            raise ValueError(reply["error"])
        return reply["result"]

    def episode_class(self, specs):
        remote = self

        class Episode:
            def reset(self, **task):
                self._id = remote.request({"op": "new"})
                return remote.request({"op": "reset", "episode": self._id, "task": task})

        def tool(spec):
            def method(self, *args, **kwargs):
                return remote.request({"op": "call", "episode": self._id, "tool": spec["name"],
                                       "args": {**dict(zip(spec["params"], args)), **kwargs}})
            method.__name__ = method.__qualname__ = spec["name"]
            method.__doc__ = spec["doc"]
            method.__signature__ = inspect.Signature(
                [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
                + [inspect.Parameter(p, inspect.Parameter.POSITIONAL_OR_KEYWORD) for p in spec["params"]])
            return method
        for spec in specs:
            setattr(Episode, spec["name"], tool(spec))
        return Episode

    # the plug
    def tasks(self, split="train", path=None, minimum=1):
        tasks = self.request({"op": "tasks", "split": split})
        if len(tasks) < minimum:
            raise ValueError(f"Need at least {minimum} tasks; found {len(tasks)}")
        return tasks

    def grade(self, episode_or_task, output=None):
        if self.KIND == "answer":
            return float(self.request({"op": "grade_answer", "task": episode_or_task, "output": output}))
        return float(self.request({"op": "grade", "episode": episode_or_task._id}))

    def check(self, episode_or_task, output=None):
        if self.KIND == "answer":
            return float(self.request({"op": "check_answer", "task": episode_or_task, "output": output}))
        return float(self.request({"op": "check", "episode": episode_or_task._id}))

    def record(self, episode):
        return self.request({"op": "record", "episode": episode._id})

    def honest_logs(self):
        return self.request({"op": "honest_logs"})

    def honest_examples(self, task):
        return self.request({"op": "honest_examples", "task": task})

    def close(self):
        if self.process.poll() is None:
            self.process.kill()


def start(path, image=IMAGE, docker="docker"):
    """Start the module at path in a fresh, isolated container and return it as an environment."""
    path, code = Path(path).resolve(), Path(__file__).resolve().parent
    command = [docker, "run", "--rm", "-i", *LIMITS, "-v", f"{code}:/app/verity:ro", "-v", f"{path.parent}:/env:ro",
               "-w", "/app", image, "python", "-I", "-c",
               f"import sys; sys.path.insert(0, '/app'); from verity.container import serve; serve('/env/{path.name}')"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, bufsize=1)
    try:
        return Remote(process)
    except Exception:
        process.kill()
        raise
