"""DeepSeek adapter and batch driver tests: fakes only, no network, Docker or model calls."""

from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src import audit, deepseek, runner
from tests.test_audit import FakeDocker

KEY = "sk-test-never-write-this-0123456789"
ENV = {"DEEPSEEK_API_KEY": KEY}
SATURDAY = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)  # off-peak all day
ACTION = json.dumps({"command": "ls", "done": False, "explanation": "look"})
DONE = json.dumps({"command": "", "done": True, "explanation": "nothing found"})
USAGE = {"prompt_tokens": 1000, "completion_tokens": 200, "prompt_cache_hit_tokens": 800,
         "prompt_cache_miss_tokens": 200, "prompt_tokens_details": {"cached_tokens": 800}}
FORBIDDEN = {"temperature", "top_p", "frequency_penalty", "presence_penalty", "tools", "tool_choice", "user", "user_id"}

spec = importlib.util.spec_from_file_location("run_batch", audit.ROOT / "scripts/run-batch.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def ok(content=ACTION, finish="stop", usage=USAGE):
    body = {"choices": [{"message": {"role": "assistant", "content": content, "reasoning_content": "hmm"},
                         "finish_reason": finish}], "usage": usage, "system_fingerprint": "fp_test"}
    return 200, {}, json.dumps(body).encode()


def error(status, message="no", headers=None):
    return status, headers or {}, json.dumps({"error": {"message": message}}).encode()


class FakePost:
    """Plays back responses (or raises exceptions) and records every request."""
    def __init__(self, *responses, default=None):
        self.responses, self.default, self.requests = list(responses), default, []
        self.lock = threading.Lock()

    def __call__(self, url, body, headers, timeout):
        with self.lock:
            self.requests.append({"url": url, "body": json.loads(body), "headers": headers, "timeout": timeout})
            r = self.responses.pop(0) if self.responses else self.default
        if r is None:
            raise AssertionError("unexpected API call")
        if isinstance(r, BaseException):
            raise r
        return r


class AdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir, self.sleeps = Path(temporary.name), []
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, post, **kw):
        kw = {"log": self.dir / "api_calls.jsonl", "post": post, "sleep": self.sleeps.append,
              "clock": lambda: SATURDAY, **kw}
        return deepseek.Client(**kw)

    def log(self):
        return [json.loads(line) for line in (self.dir / "api_calls.jsonl").read_text().splitlines()]

    def test_routes_by_model_name(self):
        post = FakePost()
        client = self.client(post, host="http://ollama:1")
        with patch.object(runner, "chat", return_value={"message": {"content": "x"}}) as chat, \
                patch.object(audit, "model_digest", return_value="sha256:q") as digest:
            self.assertEqual(client.ask([{"role": "user", "content": "hi"}], "qwen3-coder:30b", runner.SCHEMA),
                             {"message": {"content": "x"}})
            self.assertEqual(client.digest("qwen3-coder:30b"), "sha256:q")
        chat.assert_called_once_with([{"role": "user", "content": "hi"}], "qwen3-coder:30b", "http://ollama:1",
                                     fmt=runner.SCHEMA)
        digest.assert_called_once_with("qwen3-coder:30b", "http://ollama:1")
        self.assertEqual(self.client(post, run_date="2026-10-05").digest("deepseek-flash"),
                         "deepseek-api:deepseek-flash@2026-10-05")
        self.assertEqual(post.requests, [])  # neither the Ollama path nor the pinned digest touches the API

    def test_request_bodies_and_reply_shape(self):
        post = FakePost(ok(), ok(json.dumps({"outcome": "legitimate_solve"})))
        client = self.client(post, effort="max")
        messages = [{"role": "system", "content": runner.SYSTEM}, {"role": "user", "content": "TASK"},
                    {"role": "user", "content": "[1 earlier turns omitted]"},
                    {"role": "assistant", "content": ACTION}, {"role": "user", "content": "OBS"}]
        before = json.dumps(messages)
        reply = client.ask(messages, "deepseek-flash", runner.SCHEMA)
        self.assertEqual(json.dumps(messages), before)  # the caller's messages are never mutated
        self.assertEqual(reply, {"message": {"role": "assistant", "content": ACTION}, "eval_count": 200,
                                 "prompt_eval_count": 1000, "done_reason": "stop"})
        runner.save_json(self.dir / "reply.json", reply)  # serializable like any other evidence
        attacker = post.requests[0]
        body = attacker["body"]
        self.assertEqual(set(body), {"model", "messages", "max_tokens", "thinking", "reasoning_effort",
                                     "response_format"})
        self.assertEqual([body[k] for k in ("model", "max_tokens", "thinking", "reasoning_effort", "response_format")],
                         ["deepseek-flash", 8192, {"type": "enabled"}, "max", {"type": "json_object"}])
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user", "assistant", "user"])
        self.assertEqual(body["messages"][1]["content"], "TASK\n\n[1 earlier turns omitted]")
        self.assertTrue(body["messages"][0]["content"].endswith(deepseek.JSON_SUFFIX))
        self.assertIn("json", body["messages"][0]["content"])
        self.assertNotIn("reasoning_content", json.dumps(body))
        self.assertEqual((attacker["url"], attacker["timeout"], attacker["headers"]["Authorization"]),
                         (deepseek.API_URL, 300, "Bearer " + KEY))
        client.ask([{"role": "system", "content": "JUDGE"}, {"role": "user", "content": "TEXT"}], "deepseek-flash", None)
        judge = post.requests[1]["body"]
        self.assertNotIn("response_format", judge)
        self.assertEqual((judge["thinking"], judge["reasoning_effort"], judge["max_tokens"], judge["messages"]),
                         ({"type": "enabled"}, "max", 8192,
                          [{"role": "system", "content": "JUDGE"}, {"role": "user", "content": "TEXT"}]))
        self.assertEqual([FORBIDDEN & set(r["body"]) for r in post.requests], [set(), set()])
        self.assertEqual([(e["role"], e["reasoning_content"], e["system_fingerprint"]) for e in self.log()],
                         [("attacker", "hmm", "fp_test"), ("judge", "hmm", "fp_test")])

    def test_backoff_retry_after_and_give_up(self):
        post = FakePost(error(429, "slow down", {"Retry-After": "7"}), (503, {}, b"busy"),
                        socket.timeout("timed out"), ConnectionResetError("reset"), ok())
        client = self.client(post)
        self.assertEqual(client.ask([{"role": "user", "content": "x"}], "deepseek-flash", None)["eval_count"], 200)
        self.assertEqual(self.sleeps[0], 7.0)  # Retry-After is honored
        for delay, (low, high) in zip(self.sleeps[1:], [(2, 4), (4, 8), (8, 16)]):
            self.assertTrue(low <= delay <= high, self.sleeps)
        self.assertEqual(client.stats()["retries_by_status"], {"429": 1, "503": 1, "timeout": 1, "connection": 1})
        self.assertEqual([e["retries"] for e in self.log()], [0, 1, 2, 3, 4])
        self.sleeps.clear()
        post = FakePost(default=(500, {}, b"oops"))
        with self.assertRaisesRegex(RuntimeError, "gave up after 8 tries"):  # a normal exception: attempt error
            self.client(post).ask([{"role": "user", "content": "x"}], "deepseek-flash", None)
        self.assertEqual((len(post.requests), len(self.sleeps)), (8, 7))
        self.assertTrue(30 <= max(self.sleeps) <= 60, self.sleeps)  # capped at 60 s

    def test_400_and_422_raise_immediately(self):
        for status in (400, 422):
            post = FakePost(error(status, f"bad request near {KEY}"))
            with self.assertRaises(RuntimeError) as caught:
                self.client(post).ask([{"role": "user", "content": "x"}], "deepseek-flash", runner.SCHEMA)
            self.assertIn(f"HTTP {status}: bad request near ***", str(caught.exception))
            self.assertEqual((len(post.requests), self.sleeps), (1, []))

    def test_empty_content_retry_and_length(self):
        post = FakePost(ok(""), ok("  "), ok(ACTION))
        client = self.client(post)
        self.assertEqual(client.ask([{"role": "user", "content": "x"}], "deepseek-flash", runner.SCHEMA)
                         ["message"]["content"], ACTION)
        self.assertEqual((len(post.requests), client.stats()["retries_by_status"]), (3, {"empty": 2}))
        post = FakePost(ok(""), ok(""), ok(""))  # still empty after 2 retries: returned for the format-error path
        self.assertEqual(self.client(post).ask([{"role": "user", "content": "x"}], "deepseek-flash", runner.SCHEMA)
                         ["message"]["content"], "")
        self.assertEqual(len(post.requests), 3)
        post = FakePost(ok('{"command": "ec', finish="length"))  # truncated: returned as is, no retry
        reply = self.client(post).ask([{"role": "user", "content": "x"}], "deepseek-flash", runner.SCHEMA)
        self.assertEqual((reply["message"]["content"], reply["done_reason"], len(post.requests)),
                         ('{"command": "ec', "length", 1))

    def test_401_402_abort_is_sticky_and_not_an_exception(self):
        self.assertFalse(issubclass(audit.Abort, Exception))
        for status, reason in ((401, "invalid_api_key"), (402, "balance_exhausted")):
            post = FakePost(error(status, "Insufficient Balance"))
            client = self.client(post)
            for _ in range(2):  # the second call aborts without touching the API
                with self.assertRaisesRegex(audit.Abort, reason):
                    client.ask([{"role": "user", "content": "x"}], "deepseek-flash", runner.SCHEMA)
            self.assertEqual((len(post.requests), self.sleeps), (1, []))

    def test_peak_boundaries_and_cost(self):
        monday = lambda h, m: datetime(2026, 10, 5, h, m, tzinfo=timezone.utc)  # noqa: E731
        cases = {(0, 59): False, (1, 0): True, (3, 59): True, (4, 0): False, (6, 0): True, (9, 59): True,
                 (10, 0): False, (5, 59): False, (20, 0): False}
        for (h, m), peak in cases.items():
            self.assertEqual(deepseek.is_peak(monday(h, m)), peak, (h, m))
            for day in (10, 11):  # Saturday and Sunday are never peak
                self.assertFalse(deepseek.is_peak(datetime(2026, 10, day, h, m, tzinfo=timezone.utc)))
        self.assertEqual(deepseek.next_peak(monday(20, 0)), datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(deepseek.next_peak(datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)),
                         datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc))
        million = {"prompt_cache_hit_tokens": 10 ** 6, "prompt_cache_miss_tokens": 10 ** 6, "completion_tokens": 10 ** 6}
        self.assertAlmostEqual(deepseek.call_cost(million, False), 0.003 + 0.15 + 0.60)
        self.assertAlmostEqual(deepseek.call_cost(million, True), 0.006 + 0.30 + 1.20)
        fallback = {"prompt_tokens": 3 * 10 ** 6, "prompt_tokens_details": {"cached_tokens": 10 ** 6}}
        self.assertAlmostEqual(deepseek.call_cost(fallback, False), 0.003 + 2 * 0.15)
        self.assertAlmostEqual(deepseek.call_cost({"prompt_tokens": 10 ** 6}, False), 0.15)  # unknown = miss
        client = self.client(FakePost(ok()), clock=lambda: monday(2, 0))
        client.ask([{"role": "user", "content": "x"}], "deepseek-flash", None)
        [entry] = self.log()
        self.assertEqual((entry["peak"], entry["cost_usd"]),
                         (True, round((800 * 0.006 + 200 * 0.30 + 200 * 1.20) / 1e6, 8)))

    def test_spend_cap_deadline_and_missing_key_abort(self):
        post = FakePost(ok())
        client = self.client(post, max_usd=0.0001)
        client.ask([{"role": "user", "content": "x"}], "deepseek-flash", None)  # bills ~$0.00016
        with self.assertRaisesRegex(audit.Abort, "max_usd_exceeded"):
            client.ask([{"role": "user", "content": "x"}], "deepseek-flash", None)
        self.assertEqual(len(post.requests), 1)
        with self.assertRaisesRegex(audit.Abort, "hard_deadline"):
            self.client(FakePost(), deadline=10.0, monotonic=lambda: 11.0).ask(
                [{"role": "user", "content": "x"}], "deepseek-flash", None)
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(audit.Abort, "missing_api_key"):
            self.client(FakePost()).ask([{"role": "user", "content": "x"}], "deepseek-flash", None)

    def test_threads_share_one_total_and_tag_their_task(self):
        client = self.client(FakePost(default=ok()))

        def run(task):
            deepseek.set_task(task)
            for _ in range(5):
                client.ask([{"role": "user", "content": "x"}], "deepseek-flash", runner.SCHEMA)

        threads = [threading.Thread(target=run, args=(f"t{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        entries = self.log()
        self.assertEqual(sorted({e["task"] for e in entries}), ["t0", "t1", "t2", "t3"])
        self.assertEqual(len(entries), 20)
        self.assertAlmostEqual(client.stats()["estimated_usd"], sum(e["cost_usd"] for e in entries), places=6)
        self.assertAlmostEqual(deepseek.logged_spend(self.dir / "api_calls.jsonl"), client.spent, places=6)
        stats = client.stats()
        self.assertEqual((stats["calls"], stats["tokens"]["cache_hit"], stats["cache_hit_ratio"]), (20, 16000, 0.8))
        for e in entries:
            self.assertEqual({"utc", "task", "role", "latency_s", "retries", "status", "finish_reason", "usage",
                              "system_fingerprint", "cost_usd", "peak", "reasoning_content"} - set(e), set())


def tracer_docker(*args, **kwargs):
    return SimpleNamespace(returncode=0, stdout="sha256:tracer", stderr="")


class DriverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)
        self.out = self.dir / "run"

    def main(self, *argv, env=ENV, **kw):
        stdout = io.StringIO()
        with patch.dict(os.environ, env), redirect_stdout(stdout):
            if not env:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            code = batch.main(["--out", str(self.out), *argv], sleep=lambda s: None, **kw)
        return code, stdout.getvalue()

    def assert_no_key_written(self, *texts):
        for path in self.dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(KEY.encode(), path.read_bytes(), path)
        for text in texts:
            self.assertNotIn(KEY, text)

    def test_defaults_match_the_qwen_run(self):
        status = batch.QWEN_RUN / "status.tsv"
        if status.exists():
            self.assertEqual(batch.QWEN_TASKS, [line.split("\t")[0] for line in status.read_text().splitlines()[1:]])
        _, args = batch.parse([], "20261005")
        self.assertEqual((args.out.name, args.tasks, args.concurrency, args.reasoning_effort, args.max_usd,
                          args.deadline_hours, args.hard_deadline_hours, args.resume, args.dry_run),
                         ("full-deepseek-v4.1-flash-20261005", batch.QWEN_TASKS, 3, "high", 4.25, 4.0, 6.0, False, False))
        for argv in (["--out", str(batch.QWEN_RUN)], ["--out", str(batch.QWEN_RUN / "x"), "--resume"],
                     ["--tasks", "462", "462"], ["--deadline-hours", "7"]):
            with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                batch.parse(argv, "20261005")

    def test_dry_run_makes_no_calls_and_writes_nothing(self):
        def docker(*args, **kwargs):
            raise AssertionError("docker called")
        code, out = self.main("--dry-run", env={}, docker=docker, post=FakePost(),
                              audit_fn=lambda *a, **k: self.fail("audit called"))
        self.assertEqual(code, 0)
        self.assertFalse(self.out.exists())
        for text in ("Tasks to run (10): blind-maze", "cache_miss", "0.150 / 0.300", "Expected protocol_id",
                     "DEEPSEEK_API_KEY: NOT SET", '"reasoning_effort": "high"'):
            self.assertIn(text, out)

    def fake_audit(self, calls):
        def run(task_id, folder, models, request_config, **kw):
            calls.append((task_id, models))
            folder.mkdir()
            report = {"provenance": {"protocol_id": "p"}, "redteam": {}, "validity": {"status": "error"}}
            runner.save_json(folder / "report.json", report)
            return folder / "report.json"
        return run

    def test_resume_skips_complete_reports_and_moves_partial_folders_aside(self):
        client = deepseek.Client(run_date="2026-10-05")
        protocol = batch.expected_protocol(client)
        self.out.mkdir()
        runner.save_json(self.out / "run_manifest.json", {"run_date": "2026-10-05"})
        (self.out / "blind-maze").mkdir()
        done = {"provenance": {"protocol_id": protocol, "models": {"attacker": "deepseek-flash"}}, "redteam": {}}
        runner.save_json(self.out / "blind-maze" / "report.json", done)
        original = (self.out / "blind-maze" / "report.json").read_bytes()
        (self.out / "462").mkdir()  # crashed before writing a report
        (self.out / "481").mkdir()
        runner.save_json(self.out / "481" / "report.json", {"redteam": {"status": "error", "aborted": True}})
        argv = ("--tasks", "blind-maze", "462", "481")
        with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            self.main(*argv)  # without --resume an existing report is refused
        calls = []
        code, out = self.main(*argv, "--resume", post=FakePost(ok()), docker=tracer_docker,
                              audit_fn=self.fake_audit(calls))
        self.assertEqual((code, sorted(t for t, _ in calls)), (0, ["462", "481"]))
        self.assertTrue(all(m == {"attacker": "deepseek-flash", "judge": "deepseek-flash"} for _, m in calls))
        self.assertEqual((self.out / "blind-maze" / "report.json").read_bytes(), original)  # never overwritten
        self.assertEqual(sorted(p.name.split("-")[0] for p in (self.out / "superseded").iterdir()),
                         ["462", "481", "run_manifest.json"])
        manifest = json.loads((self.out / "run_manifest.json").read_text())
        self.assertEqual((manifest["status"], manifest["run_date"], [s["task"] for s in manifest["skipped"]],
                          sorted(c["task"] for c in manifest["completed"]), manifest["completed"][0]["section_errors"]),
                         ("complete", "2026-10-05", ["blind-maze"], ["462", "481"], ["validity"]))
        runner.save_json(self.out / "blind-maze" / "report.json", {**done, "provenance": {"protocol_id": "other"}})
        with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()) as err, self.assertRaises(SystemExit):
            self.main("--tasks", "462", "--resume")  # resuming would mix protocols in one folder
        self.assertIn("mix protocols", err.getvalue())

    def test_preflight_abort_stops_before_docker(self):
        def docker(*args, **kwargs):
            raise AssertionError("docker called")
        code, out = self.main(post=FakePost(error(402, "Insufficient Balance")), docker=docker,
                              audit_fn=lambda *a, **k: self.fail("audit called"))
        manifest = json.loads((self.out / "run_manifest.json").read_text())
        self.assertEqual((code, manifest["status"], len(manifest["skipped"]), manifest["calls"]), (1, "aborted", 10, 0))
        self.assertTrue(manifest["abort_reason"].startswith("balance_exhausted"))
        self.assert_no_key_written(out)

    def test_402_mid_run_writes_the_report_and_skips_the_rest(self):
        post = FakePost(ok("x", usage={"prompt_tokens": 5, "completion_tokens": 1}), ok(DONE), ok(DONE),
                        error(402, "Insufficient Balance"))
        code, out = self.main("--tasks", "blind-maze", "462", "--concurrency", "1", post=post, docker=FakeDocker())
        manifest = json.loads((self.out / "run_manifest.json").read_text())
        self.assertEqual((code, manifest["status"], [e["task"] for e in manifest["errored"]],
                          [e["task"] for e in manifest["skipped"]]), (1, "aborted", ["blind-maze"], ["462"]))
        self.assertTrue(manifest["abort_reason"].startswith("balance_exhausted"), manifest)
        self.assertIn("batch aborted", manifest["skipped"][0]["reason"])
        report = json.loads((self.out / "blind-maze" / "report.json").read_text())
        self.assertEqual((report["redteam"]["aborted"], report["validity"]["verdict"],
                          report["provenance"]["models"]["judge"], report["provenance"]["request_config"]["thinking"]),
                         (True, "valid", "deepseek-flash", "enabled"))
        self.assertEqual(report["provenance"]["protocol_id"], manifest["protocol_id"])
        self.assertIn("[blind-maze] a2 turns=0 label=fail cost=$", out)  # one progress line per finished attempt
        self.assertEqual([e["role"] for e in json.loads("[" + ",".join(
            (self.out / "api_calls.jsonl").read_text().splitlines()) + "]")],
            ["preflight", "attacker", "attacker", "attacker"])
        self.assertFalse((self.out / "462").exists())
        self.assert_no_key_written(out)


if __name__ == "__main__":
    unittest.main()
