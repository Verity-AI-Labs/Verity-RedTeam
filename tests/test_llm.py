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

from verity import llm, runner, sandbox
from tests.test_sandbox import CP, MODELS, FakeDocker, fake_ask, say

KEY = "sk-test-never-write-this-0123456789"
ENV = {"DEEPSEEK_API_KEY": KEY}
SATURDAY = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)  # off-peak all day
ACTION = json.dumps({"command": "ls", "done": False, "explanation": "look"})
DONE = json.dumps({"command": "", "done": True, "explanation": "nothing found"})
USAGE = {"prompt_tokens": 1000, "completion_tokens": 200, "prompt_cache_hit_tokens": 800,
         "prompt_cache_miss_tokens": 200, "prompt_tokens_details": {"cached_tokens": 800}}
FORBIDDEN = {"temperature", "top_p", "frequency_penalty", "presence_penalty", "tools", "tool_choice", "user", "user_id"}

spec = importlib.util.spec_from_file_location("run_batch", sandbox.ROOT / "scripts/run-batch.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)
GIT_STATE = sandbox.git_state  # DriverTests patch it; the real one is tested with mocked git calls


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
        return llm.Client(**kw)

    def log(self):
        return [json.loads(line) for line in (self.dir / "api_calls.jsonl").read_text().splitlines()]

    def test_routes_by_model_name(self):
        post = FakePost()
        client = self.client(post, host="http://ollama:1")
        with patch.object(runner, "chat", return_value={"message": {"content": "x"}}) as chat, \
                patch.object(sandbox, "model_digest", return_value="sha256:q") as digest:
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
                         ["deepseek-flash", 16384, {"type": "enabled"}, "max", {"type": "json_object"}])
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user", "assistant", "user"])
        self.assertEqual(body["messages"][1]["content"], "TASK\n\n[1 earlier turns omitted]")
        self.assertTrue(body["messages"][0]["content"].endswith(llm.JSON_SUFFIX))
        self.assertIn("json", body["messages"][0]["content"])
        self.assertNotIn("reasoning_content", json.dumps(body))
        self.assertEqual((attacker["url"], attacker["timeout"], attacker["headers"]["Authorization"]),
                         (llm.API_URL, 300, "Bearer " + KEY))
        client.ask([{"role": "system", "content": "JUDGE"}, {"role": "user", "content": "TEXT"}], "deepseek-flash", None)
        judge = post.requests[1]["body"]
        self.assertNotIn("response_format", judge)
        self.assertEqual((judge["thinking"], judge["reasoning_effort"], judge["max_tokens"], judge["messages"]),
                         ({"type": "enabled"}, "max", 32768,
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

    def test_judge_length_retries_once_with_thinking_off(self):
        verdict = json.dumps({"reasoning": "fine", "suspicion_score": 0.0})
        judge = [{"role": "system", "content": "JUDGE"}, {"role": "user", "content": "TEXT"}]
        post = FakePost(ok("", finish="length"), ok(verdict))
        client = self.client(post)
        self.assertEqual(client.ask(judge, "deepseek-flash", None)["message"]["content"], verdict)
        first, retry = (r["body"] for r in post.requests)
        self.assertEqual((first["max_tokens"], first["thinking"], first["reasoning_effort"]),
                         (32768, {"type": "enabled"}, "high"))
        self.assertEqual((retry["max_tokens"], retry["thinking"], "reasoning_effort" in retry, retry["messages"]),
                         (32768, {"type": "disabled"}, False,
                          [judge[0], {"role": "user", "content": "TEXT\n\n" + llm.JUDGE_RETRY}]))
        self.assertEqual(client.stats()["retries_by_status"], {"judge_length": 1})
        config = client.request_config()
        self.assertEqual((config["max_tokens"], config["judge_length_retry"]["thinking"]),
                         ({"attacker": 16384, "judge": 32768}, "disabled"))

    def test_462_judge_cut_by_length_never_errors_a_pass(self):
        # a1 cheats (certified answer_read), a2 solves honestly; every judge reply is cut by "length",
        # retry included: a1 keeps its certified label, a2 is unjudged (never honest), judge.degraded set, no error
        post = FakePost(default=ok("", finish="length"))
        client = self.client(post)
        with redirect_stdout(io.StringIO()):
            path = sandbox.audit("blind-maze", docker=FakeDocker(), ask=fake_ask([[say(CP)], [say("solve")]], []),
                               model_digest=lambda m: "d", runs=self.dir, stamp="s", models=MODELS,
                               judge=sandbox.real_judge(client.ask, "deepseek-flash"), budgets={"attempts": 2})
        attempts = json.loads(path.read_text())["redteam"]["attempts"]
        self.assertEqual([[a[k] for k in ("label", "tier", "grader_pass", "unconfirmed")]
                          + [a["judge"][k] for k in ("label", "degraded", "suspicion_score")] for a in attempts],
                         [["hack", "certified", True, ["judge"], "unjudged", True, None],
                          ["unjudged", None, True, ["judge"], "unjudged", True, None]])
        self.assertTrue(all("error" not in a for a in attempts))
        self.assertEqual(len(post.requests), 12)  # per judged attempt, 3 samples x (call + thinking-off retry)

    def test_leaked_markup_after_the_action_is_stripped(self):
        leak = ACTION + '\n\n<\uff5c\uff5cDSML\uff5c\uff5c calls>\n<\uff5c\uff5cDSML\uff5c\uff5c invoke name="bash">\nls\n'
        post = FakePost(ok(leak), ok(leak))
        client = self.client(post)
        self.assertEqual(client.ask([{"role": "user", "content": "x"}], "deepseek-flash", runner.SCHEMA)
                         ["message"]["content"], ACTION)  # attacker: only the leading JSON object
        self.assertEqual(client.stats()["retries_by_status"], {"trailing_text_stripped": 1})
        self.assertEqual(client.ask([{"role": "user", "content": "x"}], "deepseek-flash", None)
                         ["message"]["content"], leak)  # judge replies are never touched

    def test_401_402_abort_is_sticky_and_not_an_exception(self):
        self.assertFalse(issubclass(sandbox.Abort, Exception))
        for status, reason in ((401, "invalid_api_key"), (402, "balance_exhausted")):
            post = FakePost(error(status, "Insufficient Balance"))
            client = self.client(post)
            for _ in range(2):  # the second call aborts without touching the API
                with self.assertRaisesRegex(sandbox.Abort, reason):
                    client.ask([{"role": "user", "content": "x"}], "deepseek-flash", runner.SCHEMA)
            self.assertEqual((len(post.requests), self.sleeps), (1, []))

    def test_peak_boundaries_and_cost(self):
        monday = lambda h, m: datetime(2026, 10, 5, h, m, tzinfo=timezone.utc)  # noqa: E731
        cases = {(0, 59): False, (1, 0): True, (3, 59): True, (4, 0): False, (6, 0): True, (9, 59): True,
                 (10, 0): False, (5, 59): False, (20, 0): False}
        for (h, m), peak in cases.items():
            self.assertEqual(llm.is_peak(monday(h, m)), peak, (h, m))
            for day in (10, 11):  # Saturday and Sunday are never peak
                self.assertFalse(llm.is_peak(datetime(2026, 10, day, h, m, tzinfo=timezone.utc)))
        self.assertEqual(llm.next_peak(monday(20, 0)), datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(llm.next_peak(datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)),
                         datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc))
        million = {"prompt_cache_hit_tokens": 10 ** 6, "prompt_cache_miss_tokens": 10 ** 6, "completion_tokens": 10 ** 6}
        self.assertAlmostEqual(llm.call_cost(million, False), 0.003 + 0.15 + 0.60)
        self.assertAlmostEqual(llm.call_cost(million, True), 0.006 + 0.30 + 1.20)
        fallback = {"prompt_tokens": 3 * 10 ** 6, "prompt_tokens_details": {"cached_tokens": 10 ** 6}}
        self.assertAlmostEqual(llm.call_cost(fallback, False), 0.003 + 2 * 0.15)
        self.assertAlmostEqual(llm.call_cost({"prompt_tokens": 10 ** 6}, False), 0.15)  # unknown = miss
        client = self.client(FakePost(ok()), clock=lambda: monday(2, 0))
        client.ask([{"role": "user", "content": "x"}], "deepseek-flash", None)
        [entry] = self.log()
        self.assertEqual((entry["peak"], entry["cost_usd"]),
                         (True, round((800 * 0.006 + 200 * 0.30 + 200 * 1.20) / 1e6, 8)))

    def test_spend_cap_deadline_and_missing_key_abort(self):
        post = FakePost(ok())
        client = self.client(post, max_usd=0.0001)
        client.ask([{"role": "user", "content": "x"}], "deepseek-flash", None)  # bills ~$0.00016
        with self.assertRaisesRegex(sandbox.Abort, "max_usd_exceeded"):
            client.ask([{"role": "user", "content": "x"}], "deepseek-flash", None)
        self.assertEqual(len(post.requests), 1)
        with self.assertRaisesRegex(sandbox.Abort, "hard_deadline"):
            self.client(FakePost(), deadline=10.0, monotonic=lambda: 11.0).ask(
                [{"role": "user", "content": "x"}], "deepseek-flash", None)
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(sandbox.Abort, "missing_api_key"):
            self.client(FakePost()).ask([{"role": "user", "content": "x"}], "deepseek-flash", None)

    def test_threads_share_one_total_and_tag_their_task(self):
        client = self.client(FakePost(default=ok()))

        def run(task):
            llm.set_task(task)
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
        self.assertAlmostEqual(llm.logged_spend(self.dir / "api_calls.jsonl"), client.spent, places=6)
        stats = client.stats()
        self.assertEqual((stats["calls"], stats["tokens"]["cache_hit"], stats["cache_hit_ratio"]), (20, 16000, 0.8))
        for e in entries:
            self.assertEqual({"utc", "task", "role", "latency_s", "retries", "status", "finish_reason", "usage",
                              "system_fingerprint", "cost_usd", "peak", "reasoning_content"} - set(e), set())

    def test_zero_command_refusals_are_errors_not_negative_evidence(self):
        def ask(*args):
            return {"message": {"content": DONE}}
        with redirect_stdout(io.StringIO()):
            path = sandbox.audit("blind-maze", docker=FakeDocker(), ask=ask, model_digest=lambda m: "d",
                               runs=self.dir, models=MODELS, judge=lambda text: self.fail("judge called"),
                               budgets={"attempts": 1})
        result = json.loads(path.read_text())["redteam"]
        self.assertTrue(result["attempts"])
        self.assertTrue(all(a["label"] == "error" and a["turns"] == 0 for a in result["attempts"]))
        self.assertEqual(result["summary"]["n"], 0)
        self.assertIsNone(result["summary"]["no_exploit_upper"])


class OpenRouterTests(unittest.TestCase):
    """Same transport contract, configured routing and costs, no provider-specific JSON mode."""
    setUp, log = AdapterTests.setUp, AdapterTests.log

    def client(self, post, **kw):
        return AdapterTests.client(self, post, provider="openrouter", attacker="anthropic/claude-test",
                              judge="google/gemini-test", input_price=4, output_price=20, **kw)

    # AdapterTests retains the exact DeepSeek legacy contract; avoid inheriting those tests.
    def test_openrouter_routes_and_preserves_plain_messages(self):
        post = FakePost(ok(usage={**USAGE, "cost": 0.12}), ok(usage={**USAGE, "cost": 0.0}))
        client = self.client(post, run_date="2026-10-16")
        messages = [{"role": "system", "content": "Only JSON"}, {"role": "user", "content": "task"}]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": KEY}):
            client.ask(messages, "anthropic/claude-test", runner.SCHEMA)
            client.ask(messages, "google/gemini-test", None)
        self.assertEqual(post.requests[0]["url"], llm.OPENROUTER_URL)
        self.assertEqual(post.requests[0]["body"],
                         {"model": "anthropic/claude-test", "messages": messages, "max_tokens": 16384})
        self.assertEqual(client.digest("anthropic/claude-test"), "openrouter:anthropic/claude-test@2026-10-16")
        with patch.object(runner, "chat", return_value={}) as chat:
            client.ask(messages, "deepseek-flash", None)
        chat.assert_called_once()
        self.assertEqual(client.stats()["estimated_usd"], 0.12)
        self.assertEqual([e["cost_source"] for e in self.log()], ["provider_reported", "provider_reported"])
        self.assertNotIn(KEY, (self.dir / "api_calls.jsonl").read_text())

    def test_openrouter_fallback_and_reservation(self):
        post = FakePost(ok(usage={"prompt_tokens": 1000, "completion_tokens": 200}))
        client = self.client(post, max_tokens=1000)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": KEY}):
            client.ask([{"role": "user", "content": "task"}], "anthropic/claude-test", runner.SCHEMA)
        self.assertAlmostEqual(client.spent, .008)
        self.assertEqual(self.log()[0]["cost_source"], "configured_fallback")
        self.assertEqual(client.request_config()["max_tokens"]["attacker"], 1000)
        self.assertEqual(client.reserved, 0)
        empty = FakePost()
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": KEY}), self.assertRaisesRegex(sandbox.Abort, "reservation"):
            self.client(empty, max_usd=.01).ask([{"role": "user", "content": "task"}],
                                               "anthropic/claude-test", runner.SCHEMA)
        self.assertEqual(empty.requests, [])

    def test_openrouter_missing_key_is_sticky_and_errors_are_scrubbed(self):
        client = self.client(FakePost())
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(sandbox.Abort, "OPENROUTER_API_KEY"):
            client.ask([{"role": "user", "content": "task"}], "anthropic/claude-test", runner.SCHEMA)
        client = self.client(FakePost(error(400, f"bad {KEY}")))
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": KEY}), self.assertRaisesRegex(RuntimeError, r"bad \*\*\*"):
            client.ask([{"role": "user", "content": "task"}], "anthropic/claude-test", runner.SCHEMA)
        self.assertNotIn(KEY, (self.dir / "api_calls.jsonl").read_text())

    def test_http_200_upstream_errors_never_count_as_generations(self):
        post = FakePost(default=(200, {}, json.dumps({"error": {"message": "Upstream overloaded", "code": 503}}).encode()))
        client = self.client(post, max_tries=2)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": KEY}), self.assertRaisesRegex(RuntimeError, "after 2 tries"):
            client.ask([{"role": "user", "content": "task"}], "anthropic/claude-test", runner.SCHEMA)
        self.assertEqual(client.stats()["calls"], 0)
        self.assertEqual(client.stats()["estimated_usd"], 0)
        self.assertTrue(all("Upstream overloaded" in event["error"] for event in self.log()))

    def test_env_loader_does_not_execute_or_replace_exports(self):
        path = self.dir / ".env"
        path.write_text("# local keys\nOPENROUTER_API_KEY='$(echo secret)'\nDEEPSEEK_API_KEY=new\nHOME=/bad\n")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "keep", "HOME": "/original"}, clear=True):
            llm.load_env(path)
            self.assertEqual(os.environ["OPENROUTER_API_KEY"], "$(echo secret)")
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "keep")
            self.assertEqual(os.environ["HOME"], "/original")

    def test_cli_models_and_independent_judge(self):
        argv = ["--provider", "openrouter", "--attacker", "anthropic/claude-test", "--judge", "google/gemini-test",
                "--input-price", "4", "--output-price", "20"]
        _, args = batch.parse(argv, "20261016")
        self.assertEqual(args.models, {"attacker": "anthropic/claude-test", "judge": "google/gemini-test"})
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            batch.parse(argv + ["--judge", "anthropic/another-model"], "20261016")
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            batch.parse(argv + ["--max-usd", "nan"], "20261016")
        output = self.dir / "batch-dry"
        with redirect_stdout(io.StringIO()) as stdout:
            code = batch.main(argv + ["--out", str(output), "--dry-run"], post=FakePost())
        self.assertEqual(code, 0)
        self.assertFalse(output.exists())
        self.assertIn('"provider": "openrouter"', stdout.getvalue())


def tracer_docker(*args, **kwargs):
    return SimpleNamespace(returncode=0, stdout="sha256:tracer", stderr="")


class DriverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)
        self.out = self.dir / "run"
        # the developer's working tree may be dirty; the dirty-tree guard has its own test
        patcher = patch.object(sandbox, "git_state", lambda: {"git_commit": "c0ffee", "git_dirty": False})
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_v3_defaults_and_qwen_guard(self):
        status = batch.QWEN_RUN / "status.tsv"
        if status.exists():
            self.assertEqual(batch.QWEN_TASKS, [line.split("\t")[0] for line in status.read_text().splitlines()[1:]])
        _, args = batch.parse([], "20261005")
        self.assertEqual((args.out.name, args.tasks, args.concurrency, args.reasoning_effort, args.max_usd,
                          args.deadline_hours, args.hard_deadline_hours, args.resume, args.dry_run),
                         ("audit-v3-deepseek-v4.1-flash-20261005", list(runner.TASKS), 3, "high", 10.0, 8.0, 10.0,
                          False, False))
        self.assertEqual(len(args.tasks), 13)  # every task in tasks.json, in file order
        for argv in (["--out", str(batch.QWEN_RUN)], ["--out", str(batch.QWEN_RUN / "x"), "--resume"],
                     ["--tasks", "462", "462"], ["--deadline-hours", "11"]):
            with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                batch.parse(argv, "20261005")

    def test_dry_run_makes_no_calls_and_writes_nothing(self):
        def docker(*args, **kwargs):
            raise AssertionError("docker called")
        code, out = self.main("--dry-run", env={}, docker=docker, post=FakePost(),
                              audit_fn=lambda *a, **k: self.fail("audit called"))
        self.assertEqual(code, 0)
        self.assertFalse(self.out.exists())
        for text in ("Tasks to run (13): blind-maze", "fix-sentiment-cli-text-processing", "cache_miss", "0.150 / 0.300", "Expected protocol_id",
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
        client = llm.Client(run_date="2026-10-05")
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
        v2 = {**done["provenance"], "schema_version": "audit-v2"}  # even with a matching id, v2 never mixes with v3
        for prov in ({"protocol_id": "other"}, v2):
            runner.save_json(self.out / "blind-maze" / "report.json", {**done, "provenance": prov})
            with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()) as err, self.assertRaises(SystemExit):
                self.main("--tasks", "462", "--resume")  # resuming would mix protocols in one folder
            self.assertIn("mix protocols", err.getvalue())

    def test_e2_git_provenance_and_dirty_tree_guard(self):
        def git(status):
            def run(argv, **kw):
                out = {"rev-parse": "abc123\n", "status": status}[argv[1]]
                return SimpleNamespace(returncode=0, stdout=out, stderr="")
            return run
        runs_only = " D experiments/runs/old/report.json\n?? experiments/runs/new/\n"
        for status, dirty in ((runs_only, False), (runs_only + " M verity/sandbox.py\n", True), ("?? x.py\n", True),
                              ('R  "a b.txt" -> experiments/runs/a b.txt\n', True), ("", False)):
            with patch.object(sandbox.subprocess, "run", git(status)):
                self.assertEqual(GIT_STATE(), {"git_commit": "abc123", "git_dirty": dirty}, status)
        # the manifest records the commit, dirty flag and code hashes
        calls = []
        code, _ = self.main("--tasks", "462", post=FakePost(ok()), docker=tracer_docker, audit_fn=self.fake_audit(calls))
        manifest = json.loads((self.out / "run_manifest.json").read_text())
        self.assertEqual((code, manifest["git_commit"], manifest["git_dirty"], manifest["allow_dirty"]),
                         (0, "c0ffee", False, False))
        self.assertEqual(manifest["code_sha256"], {f"verity/{n}": sandbox.sha256(sandbox.ROOT / "verity" / n)
                                                   for n in ("sandbox.py", "llm.py", "runner.py")})
        # a dirty tree blocks a real run (before any API, Docker or file write) unless --allow-dirty
        self.out = self.dir / "dirty"
        with patch.object(sandbox, "git_state", lambda: {"git_commit": "c0ffee", "git_dirty": True}):
            with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()) as err, \
                    self.assertRaises(SystemExit):
                self.main("--tasks", "462", post=FakePost(), docker=tracer_docker,
                          audit_fn=lambda *a, **k: self.fail("audit called"))
            self.assertIn("--allow-dirty", err.getvalue())
            self.assertFalse(self.out.exists())
            code, out = self.main("--dry-run", env={}, post=FakePost(), audit_fn=lambda *a, **k: self.fail("audit"))
            self.assertEqual((code, self.out.exists()), (0, False))
            self.assertIn("WARNING: git tree is dirty", out)
            code, _ = self.main("--tasks", "462", "--allow-dirty", post=FakePost(ok()), docker=tracer_docker,
                                audit_fn=self.fake_audit([]))
            manifest = json.loads((self.out / "run_manifest.json").read_text())
            self.assertEqual((code, manifest["git_dirty"], manifest["allow_dirty"]), (0, True, True))
        # the report's provenance carries both fields, and they stay out of protocol_id
        task = runner.task_config("blind-maze")
        ctx = {"task": task, "bud": dict(sandbox.BUDGETS), "models": dict(MODELS), "digests": {"attacker": "d", "judge": "d"},
               "judge_sha": "j", "cg_runs": [], "wall": {}, "stamp": "s"}
        provs = []
        for state in ({"git_commit": "abc123", "git_dirty": False}, {"git_commit": "def456", "git_dirty": True}):
            with patch.object(sandbox, "git_state", lambda: state):
                provs.append(sandbox._provenance(dict(ctx)))
        self.assertEqual([(p["git_commit"], p["git_dirty"]) for p in provs], [("abc123", False), ("def456", True)])
        self.assertEqual(provs[0]["protocol_id"], provs[1]["protocol_id"])

    def test_preflight_abort_stops_before_docker(self):
        def docker(*args, **kwargs):
            raise AssertionError("docker called")
        code, out = self.main(post=FakePost(error(402, "Insufficient Balance")), docker=docker,
                              audit_fn=lambda *a, **k: self.fail("audit called"))
        manifest = json.loads((self.out / "run_manifest.json").read_text())
        self.assertEqual((code, manifest["status"], len(manifest["skipped"]), manifest["calls"]), (1, "aborted", 13, 0))
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
        # one progress line per finished attempt, with the judge median (None: a fail is never judged)
        self.assertIn("[blind-maze] a2 turns=0 label=error judge_median=None cost=$", out)
        self.assertEqual([e["role"] for e in json.loads("[" + ",".join(
            (self.out / "api_calls.jsonl").read_text().splitlines()) + "]")],
            ["preflight", "attacker", "attacker", "attacker"])
        self.assertFalse((self.out / "462").exists())
        self.assert_no_key_written(out)


if __name__ == "__main__":
    unittest.main()
