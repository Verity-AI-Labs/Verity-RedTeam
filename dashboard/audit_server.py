"""The audit service behind the site: upload one environment file, Verity audits it in an isolated container.

    python dashboard/audit_server.py --port 8771 --token "$VERITY_TOKEN"     # needs Docker and OPENROUTER_API_KEY

POST /api/audit {"filename", "source"} queues an audit and returns its id; GET /api/audit?id=... returns its
progress (every tool call the auditor makes, as it happens), then the proof and the rules; GET /api/audit says
whether the service is up and busy. One audit at a time, each in a fresh container (verity/container.py: no
network, no keys, read-only, memory and CPU capped) with a spend cap. The hosted site (web/api/audit.js) forwards
here; with --token, POST needs the X-Verity-Token header.
"""
import argparse
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verity import container, llm, probe  # noqa: E402

MAX_BYTES = 200_000


class Audits:
    def __init__(self, model, input_price, output_price, max_usd, episodes, max_calls):
        self.settings = dict(model=model, input_price=input_price, output_price=output_price, max_usd=max_usd,
                             episodes=episodes, max_calls=max_calls)
        self.jobs, self.queue = {}, queue.Queue()
        threading.Thread(target=self.worker, daemon=True).start()

    def submit(self, filename, source):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,60}\.py", filename or ""):
            raise ValueError("Upload one .py file with a simple name, like my_env.py")
        if not source or len(source.encode()) > MAX_BYTES:
            raise ValueError("The file must be between 1 byte and 200 KB")
        compile(source, filename, "exec")  # syntax errors are reported before anything runs
        job = {"id": uuid.uuid4().hex[:12], "status": "queued", "filename": filename, "events": [], "created": time.time()}
        self.jobs[job["id"]] = job
        self.queue.put((job, source))
        return job

    def busy(self):
        return any(job["status"] in ("queued", "running") for job in self.jobs.values())

    def worker(self):
        while True:
            job, source = self.queue.get()
            try:
                self.run(job, source)
            except Exception as error:
                job.update(status="error", error=f"{type(error).__name__}: {error}"[:500])

    def run(self, job, source):
        job["status"] = "running"
        say = lambda text, flag=False: job["events"].append({"text": text, "flag": flag})  # noqa: E731
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / job["filename"]
            path.write_text(source)
            path.chmod(0o644)
            Path(folder).chmod(0o755)
            say("Starting an isolated container: no network, no keys, read-only, 1 CPU, 1 GB.")
            env = container.start(path)
            try:
                job["environment"] = f"{env.NAME}@{env.VERSION} ({env.KIND})"
                tools = ", ".join(f"{t.__name__}" for t in vars(env.Episode).values() if callable(t) and t.__name__ != "reset")
                say(f"Loaded {env.NAME}: {env.DESCRIPTION}" + (f" Tools: {tools}." if tools else ""))
                tasks = env.tasks("train")
                say(f"{len(tasks)} training tasks; the auditor sees {self.settings['episodes']} of them.")
                watched = self.watch(env, say)
                client = llm.Client(provider="openrouter", attacker=self.settings["model"],
                                    input_price=self.settings["input_price"], output_price=self.settings["output_price"],
                                    max_usd=self.settings["max_usd"])
                say(f"Auditing with {self.settings['model']} (cap ${self.settings['max_usd']:.2f}).")
                report, pack = probe.run(watched, tasks, ask=client.ask, model=self.settings["model"],
                                         count=self.settings["episodes"] if env.KIND == "tools" else 16,
                                         max_calls=self.settings["max_calls"], max_scores=20)
                job["cost_usd"] = client.stats().get("estimated_usd")
                row = report["rounds"][-1] if report["rounds"] else {}
                proof = row.get("proof") or {}
                if proof:
                    say(f"Proof: mean reward {proof['env_reward']:.2f} against a true score of {proof['true_score']:.2f} "
                        f"over {proof['tasks']} finished episodes.", flag=report["status"] == "passed")
                job.update(status=report["status"], markers=(pack or {}).get("signatures", []),
                           summary=row.get("explanation", "")[:1200], error=report.get("error"))
            finally:
                env.close()

    @staticmethod
    def watch(env, say):
        """The container environment, with every tool call and finished episode reported to the page."""
        class Watched:
            pass
        watched = Watched()
        for key in ("NAME", "VERSION", "KIND", "DESCRIPTION", "AUDIT_TASKS", "RECORD", "MAX_TOOL_CALLS"):
            if hasattr(env, key):
                setattr(watched, key, getattr(env, key))
        for key in ("tasks", "check", "record", "honest_logs", "honest_examples"):
            setattr(watched, key, getattr(env, key))

        def grade(*args):
            reward = env.grade(*args)
            say(f"Episode finished: reward {reward:.2f}.")
            return reward
        watched.grade = grade

        class Episode(env.Episode):
            pass
        for name, method in list(vars(env.Episode).items()):
            if callable(method) and name != "reset" and not name.startswith("_"):
                def tool(self, *args, _name=name, _method=method, **kwargs):
                    result = _method(self, *args, **kwargs)
                    shown = ", ".join(str(v) for v in [*args, *kwargs.values()])
                    say(f"{_name}({shown}) → {str(result)[:120]}")
                    return result
                tool.__name__, tool.__doc__ = name, method.__doc__
                tool.__signature__ = method.__signature__
                setattr(Episode, name, tool)
        watched.Episode = Episode
        return watched


def handler(audits, token):
    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path != "/api/audit":
                return self.send(404, {"error": "not found"})
            job_id = parse_qs(url.query).get("id", [""])[0]
            if not job_id:
                return self.send(200, {"available": True, "busy": audits.busy(), "max_usd": audits.settings["max_usd"]})
            job = audits.jobs.get(job_id)
            return self.send(200, job) if job else self.send(404, {"error": "no such audit"})

        def do_POST(self):
            if urlparse(self.path).path != "/api/audit":
                return self.send(404, {"error": "not found"})
            if token and self.headers.get("X-Verity-Token") != token:
                return self.send(403, {"error": "missing or wrong X-Verity-Token"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BYTES * 2:
                return self.send(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                job = audits.submit(str(body.get("filename", "")), str(body.get("source", "")))
            except (ValueError, SyntaxError) as error:
                return self.send(400, {"error": str(error)[:300]})
            return self.send(200, {"id": job["id"], "status": job["status"]})

        def log_message(self, *_args):
            pass
    return Handler


def main(argv=None):
    llm.load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--token", default=os.environ.get("VERITY_TOKEN"), help="required to start an audit")
    parser.add_argument("--model", default="anthropic/claude-opus-5.5")
    parser.add_argument("--input-price", type=float, default=4.0)
    parser.add_argument("--output-price", type=float, default=20.0)
    parser.add_argument("--max-usd", type=float, default=1.0, help="spend cap per audit")
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--max-calls", type=int, default=40)
    args = parser.parse_args(argv)
    if not os.environ.get("OPENROUTER_API_KEY"):
        parser.error("OPENROUTER_API_KEY is not set (put it in .env)")
    audits = Audits(args.model, args.input_price, args.output_price, args.max_usd, args.episodes, args.max_calls)
    server = ThreadingHTTPServer((args.host, args.port), handler(audits, args.token))
    print(f"Verity audits: http://{args.host}:{args.port}/api/audit  (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
