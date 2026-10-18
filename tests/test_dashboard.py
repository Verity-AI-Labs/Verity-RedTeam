"""Artifact and request boundaries for the local dashboard; no ML dependency."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from dashboard.server import Demo, Handler, ROOT


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.demo = Demo(self.path, ROOT / "experiments/runs/full-audit-v51-20261012",
                         ROOT / "markers/chess.json", "test/model")

    def tearDown(self):
        self.demo.close()
        self.temporary.cleanup()

    def test_empty_training_is_not_a_curve(self):
        result = self.demo.training()
        self.assertEqual(result["source"], "awaiting_training")
        self.assertIsNone(result["arms"]["baseline"]["latest"])
        self.assertFalse(self.demo.status()["trained_ready"])

    def test_partial_telemetry_write_does_not_break_dashboard(self):
        directory = self.path / "baseline"
        directory.mkdir()
        row = {"event": "rollout", "step": 4, "env_reward": 1, "true_score": 0,
               "guarded_reward": 1, "caught": 2, "examples": []}
        (directory / "telemetry.jsonl").write_text(json.dumps(row) + '\n{"event":')
        (directory / "run.json").write_text(json.dumps({"status": "completed"}))
        result = self.demo.training()
        self.assertEqual(result["source"], "recorded_telemetry")
        self.assertEqual(result["arms"]["baseline"]["latest"]["step"], 4)
        self.assertEqual(result["arms"]["baseline"]["caught_total"], 2)

    def write_pair(self):
        config = {"model": "test/model", "max_steps": 5, "max_completion_length": 160, "environment": "chess@1"}
        for arm in ("baseline", "verity"):
            directory = self.path / arm
            (directory / "adapter").mkdir(parents=True)
            (directory / "adapter/adapter_config.json").write_text('{}')
            manifest = {"arm": arm, "status": "completed", "steps_completed": 5,
                        "config": config, "model_commit": "revision", "precision": "float16",
                        "packages": {"trl": "1.16.0"}, "source_sha256": {"env.py": "same"}}
            (directory / "run.json").write_text(json.dumps(manifest))

    def test_adapter_files_need_matched_completed_manifests(self):
        self.write_pair()
        self.assertTrue(self.demo.status()["trained_ready"])
        path = self.path / "verity/run.json"
        manifest = json.loads(path.read_text())
        manifest["config"]["max_completion_length"] = 80
        path.write_text(json.dumps(manifest))
        self.assertFalse(self.demo.status()["trained_ready"])
        with self.assertRaisesRegex(ValueError, "config"):
            self.demo.trained_config()

    def test_changed_environment_cannot_be_a_matched_comparison(self):
        self.write_pair()
        for arm in ("baseline", "verity"):
            path = self.path / arm / "run.json"
            manifest = json.loads(path.read_text())
            manifest["config"]["environment"] = "chess@0"
            path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "changed since training"):
            self.demo.trained_config()

    def test_final_report_verdict_includes_rescored_findings(self):
        directory = self.path / "reports/task"
        directory.mkdir(parents=True)
        (directory / "report.json").write_text(json.dumps({"hackability": {"value": "certified"},
            "redteam": {"attempts": [{"label": "honest"}], "mechanisms": [{"class": "answer_read"}]}}))
        self.demo.reports_dir = directory.parent
        result = self.demo.real_envs()
        self.assertEqual((result["total"], result["hackable"], result["certified"]), (1, 1, 1))
        self.assertEqual(result["tasks"][0]["hacks"], 0)

    def test_fixture_fallback_is_explicit(self):
        fixture = ROOT / "markers/chess-fixture.json"
        if not fixture.exists():
            self.skipTest("Seeded fixture artifact not generated")
        self.demo.signature = self.path / "chess.json"
        self.demo.signature.with_name("chess-fixture.json").write_bytes(fixture.read_bytes())
        result = self.demo.audit()
        self.assertTrue(result["source"].endswith("chess-fixture.json"))
        self.assertEqual(result["pack"]["provenance"]["mode"], "seeded_fixture")
        self.assertEqual(result["report"]["mode"], "seeded_fixture")

    def test_live_probe_reward_gap_and_guard(self):
        if not self.demo.stockfish:
            self.skipTest("Stockfish unavailable")
        self.demo.signature = ROOT / "markers/chess-fixture.json"
        if not self.demo.signature.exists():
            self.skipTest("Seeded fixture artifact not generated")
        spray = self.demo.probe({"kind": "spray"})
        honest = self.demo.probe({"kind": "honest"})
        self.assertEqual((spray["env_reward"], spray["true_score"], spray["guarded_reward"]), (1, 0, -1))
        self.assertEqual((honest["env_reward"], honest["true_score"], honest["guarded_reward"]), (1, 1, 1))
        self.assertEqual(spray["source"], "live_seeded_probe")

    def test_replay_halts_at_first_block(self):
        if not (self.demo.reports_dir / "blind-maze/report.json").exists():
            self.skipTest("Recorded blind-maze audit unavailable")
        result = self.demo.replay()
        self.assertEqual([(e["turn"], e["blocked"]) for e in result["events"]], [(1, False), (2, False), (3, True)])
        self.assertEqual(result["source"], "recorded_replay")
        self.assertNotIn("recorded_observation", result["events"][-1])

    def test_smoke_stage_is_disclosed(self):
        self.write_pair()
        result = self.demo.training()
        self.assertEqual(result["run_context"]["configured_steps"], 5)
        self.assertTrue(result["run_context"]["smoke"])
        self.assertIn("5 configured steps", result["run_context"]["label"])

    def test_http_rejects_foreign_origins_and_rebinding_hosts(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.demo = self.demo
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(url + "/api/status") as response:
                self.assertTrue(json.load(response)["local_only"])
            for headers in ({"Origin": "https://example.invalid"}, {"Host": "rebinding.invalid"}):
                request = urllib.request.Request(url + "/api/arena/reset", data=b'{}', headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request)
                self.assertEqual(raised.exception.code, 403)
            request = urllib.request.Request(url + "/api/arena/start", data=b'[]')
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request)
            self.assertEqual(raised.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()


class SiteTests(unittest.TestCase):
    def test_the_shop_case_builds_from_the_audit_and_runs(self):
        from dashboard import site
        with tempfile.TemporaryDirectory() as folder:
            runs = Path(folder)
            for arm in ("baseline", "verity"):
                (runs / arm).mkdir()
                rows = [{"event": "rollout", "step": step, "env_reward": .8, "true_score": .5, "success": .5,
                         "hack": .1 if arm == "baseline" else 0.0, "caught_rate": .2 if arm == "verity" else .1}
                        for step in range(3)]
                (runs / arm / "telemetry.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            data = site.shop_data(runs)
        self.assertEqual(data["environment"], "shop@8")
        self.assertEqual([len(data["training"][arm]) for arm in ("baseline", "verity")], [3, 3])
        if "audit" in data:  # the live audit's markers are in the repo
            self.assertTrue(any(step.get("flag") for step in data["audit"]["steps"]))
            demo = data["guard_demo"]
            self.assertEqual([s["blocked"] for s in demo["steps"]].count(True), 1)
            self.assertEqual(demo["true_score"], 1.0)
        page = site.document({"shop": data})
        self.assertIn('id="shop-viewer"', page)
