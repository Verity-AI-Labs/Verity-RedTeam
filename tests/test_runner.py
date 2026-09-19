"""Integration checks: run `python3 src/runner.py check`, then `python3 -m unittest -v`.

Uses the real Docker maze and a tiny local Ollama stand-in; no model download.
"""

from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from src import runner as run


def action(command="", done=False, explanation="Test action"):
    return {"message": {"content": json.dumps({
        "command": command, "done": done, "explanation": explanation,
    })}}


@contextmanager
def ollama(replies):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, **body})
            index = len(requests) - 1
            status, reply = replies[index] if index < len(replies) else (500, {})
            data = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class RunnerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            image = run.docker("image", "inspect", run.IMAGE, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise unittest.SkipTest(f"Docker unavailable: {error}")
        if image.returncode:
            raise unittest.SkipTest(
                "Start Docker and build the image: python3 src/runner.py check"
            )

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="verity-test-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / "attack"
        self.marker = Path("/tmp") / ("verity-host-marker-" + uuid.uuid4().hex)
        self.addCleanup(self.marker.unlink, missing_ok=True)

    def attempt(self, host, turns):
        args = SimpleNamespace(model="test-model", host=host, max_turns=turns)
        with patch.object(run, "docker", wraps=run.docker) as calls:
            with redirect_stdout(io.StringIO()):
                result = run.attempt("attack", self.folder, args)
        launch = next(call.args for call in calls.call_args_list if call.args[0] == "run")
        container = launch[launch.index("--name") + 1]
        self.assertNotEqual(run.docker("inspect", container, check=False).returncode, 0)
        self.assertNotIn("cleanup_error", result)
        self.assertEqual(json.loads((self.folder / "result.json").read_text()), result)
        return result

    def trace(self):
        return [json.loads(line) for line in (self.folder / "trace.jsonl").read_text().splitlines()]

    def test_wrong_map_fails_and_attacker_cannot_see_host_files(self):
        command = (
            "test ! -e /tests && test ! -e /solution && "
            "test ! -e /app/hack_summary.md && "
            "printf 'not a maze\\n' > /app/maze_map.txt && "
            f"touch {self.marker} && cat /app/maze_map.txt"
        )
        replies = [(200, action(command)), (200, action(done=True, explanation="Finished test"))]
        with ollama(replies) as (host, requests):
            result = self.attempt(host, 3)
        self.assertEqual((result["status"], result["reward"], result["stop_reason"]),
                         ("graded", 0, "finished"))
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["path"], "/api/chat")
        self.assertEqual(requests[0]["model"], "test-model")
        prompt = json.loads((self.folder / "prompt.json").read_text())
        self.assertEqual(requests[0]["messages"], prompt)
        self.assertEqual(prompt, [
            {"role": "system", "content": run.SYSTEM},
            {"role": "user", "content": (run.TASK / "instruction.md").read_text()
             + "\n\n" + run.PROMPT.read_text()},
        ])
        self.assertNotIn("hack_summary", json.dumps(prompt))
        observations = [event for event in self.trace() if event["type"] == "command"]
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["command"], command)
        self.assertEqual(observations[0]["exit_code"], 0)
        self.assertEqual(observations[0]["stdout"], "not a maze\n")
        self.assertFalse(self.marker.exists(), "Model command escaped onto the host")
        self.assertEqual((self.folder / "final.txt").read_text(), "Finished test\n")
        self.assertEqual((self.folder / "reward.txt").read_text(), "0\n")
        self.assertIn("1 failed, 1 passed", (self.folder / "verifier.txt").read_text())

    def test_api_failure_is_an_error_not_reward_zero(self):
        with ollama([(503, {"error": "Test service unavailable"})]) as (host, requests):
            result = self.attempt(host, 2)
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["reward"])
        self.assertIn("503", result["error"])
        self.assertFalse((self.folder / "reward.txt").exists())
        self.assertFalse((self.folder / "verifier.txt").exists())

    def test_invalid_action_gets_correction_and_turn_limit_is_enforced(self):
        invalid = action(f"touch {self.marker}", done="false")
        command = "printf 'wrong\\n' > /app/maze_map.txt"
        replies = [(200, invalid), (200, action(command)), (200, action(done=True))]
        with ollama(replies) as (host, requests):
            result = self.attempt(host, 2)
        self.assertEqual((result["status"], result["reward"], result["stop_reason"]),
                         ("graded", 0, "turn_limit"))
        self.assertEqual(len(requests), 2)
        self.assertIn("Invalid action", requests[1]["messages"][-1]["content"])
        events = self.trace()
        self.assertEqual([event["type"] for event in events],
                         ["model", "format_error", "model", "command"])
        self.assertEqual(events[-1]["command"], command)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.folder / "final.txt").exists())


if __name__ == "__main__":
    unittest.main()
