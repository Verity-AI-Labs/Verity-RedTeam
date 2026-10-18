"""The answer-environment audit (verity/probe.py) on chess: proof, black-box boundary, marker testing.

All model responses are injected; no network.
"""
import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
import random
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import chess

from environments import chess as env
from verity import audit as front_door
from verity import probe, sandbox


def tasks():
    rng, board, rows = random.Random(9), chess.Board(), []
    for _ in range(40):
        legal = list(board.legal_moves)
        if len(legal) < 2 or board.is_game_over():
            board = chess.Board()
            legal = list(board.legal_moves)
        board.push(rng.choice(legal))
        ranked = [move.uci() for move in board.legal_moves][:3]
        if len(ranked) > 1:
            rows.append(env.task(board.fen(), ranked))
    return rows


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tasks = tasks()
        self.selected = probe.select_tasks(self.tasks)
        self.exploits = {i: env.seeded_exploit(task, "V-001") for i, task in enumerate(self.selected)}

    def done(self):
        return {"action": "done", "explanation": "multiple alternatives get credit",
                "exploits": [{"task": i, "reply": reply} for i, reply in self.exploits.items()]}

    def test_fixture_proves_bug_and_patch_with_measured_validation(self):
        report, pack = probe.run(env, self.tasks, offline=True)
        self.assertEqual((report["status"], report["mode"], report["score_calls"]), ("passed", "seeded_fixture", 0))
        self.assertIn("not a frontier discovery", report["disclosure"])
        round_one = report["rounds"][0]
        self.assertEqual([round_one["proof"][k] for k in ("tasks", "env_reward", "true_score", "patched_reward")],
                         [16, 1.0, 0.0, 0.0])
        self.assertEqual([round_one["validation"][k] for k in ("exploit_recall", "honest_false_alarms", "honest_n")],
                         [1.0, 0.0, 1600])
        self.assertGreater(round_one["validation"]["exploit_n"], 100)
        self.assertEqual(pack["provenance"]["mode"], "seeded_fixture")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            probe.write_outputs(report, pack, path / "pack.json", path / "audit.md")
            self.assertEqual(json.loads((path / "pack.json").read_text()), pack)
            self.assertIn("not a frontier discovery", (path / "audit.md").read_text())
            self.assertTrue((path / "audit.transcript.jsonl").is_file())

    def test_black_box_never_leaks_grader_labels_or_true_score(self):
        replies = iter([{"action": "score", "task": 0, "reply": self.exploits[0]}, self.done()])
        seen = []

        def ask(messages, model, fmt):
            seen.append(copy.deepcopy(messages))
            return {"message": {"content": json.dumps(next(replies))}}
        found, calls, _ = probe.discover(env, self.selected, ask, "test/model", [])
        self.assertEqual((found, calls), (self.exploits, 1))
        prompt = json.dumps(seen[0])
        for forbidden in ("ranked", "true_score", "env_reward", "answer_spray", "seeded", "Stockfish"):
            self.assertNotIn(forbidden, prompt)
        observation = json.loads(seen[1][-1]["content"])
        self.assertEqual(set(observation), {"task", "probe", "reward", "scores_left"})

    def test_probes_can_be_reused_without_spending_more_score_calls(self):
        actions = [{"action": "score", "task": i, "reply": reply} for i, reply in self.exploits.items()]
        actions.append({"action": "done", "exploits": [{"task": i, "probe": i + 1} for i in self.exploits]})
        found, scores, _ = probe.discover(env, self.selected, lambda *a: {"message": {"content": json.dumps(actions.pop(0))}},
                                          "test/model", [])
        self.assertEqual((scores, found), (16, self.exploits))

    def test_score_cap_is_enforced_and_partial_queries_survive_failure(self):
        calls = []

        def ask(*args):
            calls.append(1)
            return {"message": {"content": json.dumps({"action": "score", "task": 0, "reply": "nothing"})}}
        report, pack = probe.run(env, self.tasks, ask=ask, model="test/model", max_scores=2)
        self.assertIsNone(pack)
        self.assertEqual((report["status"], report["score_calls"]), ("error", 2))
        self.assertLessEqual(len(calls), 8)
        self.assertIn("six invalid actions", report["error"])
        replies = iter([{"message": {"content": json.dumps({"action": "score", "task": 0, "reply": "nothing"})}}])

        def aborting(*args):
            try:
                return next(replies)
            except StopIteration:
                raise sandbox.Abort("balance_exhausted")
        report, pack = probe.run(env, self.tasks, ask=aborting, model="test/model")
        self.assertEqual(report["score_calls"], 1)
        self.assertIn("balance_exhausted", report["error"])

    def test_refusal_and_unproven_results_never_install_markers(self):
        refusal, pack = probe.run(env, self.tasks, ask=lambda *a: {"message": {"content": "I cannot do that."}},
                                  model="test/model")
        self.assertEqual(refusal["status"], "error")
        self.assertIsNone(pack)
        honest = {"action": "done", "exploits": [{"task": i, "reply": task["ranked"][0]}
                                                    for i, task in enumerate(self.selected)]}
        report, pack = probe.run(env, self.tasks, ask=lambda *a: {"message": {"content": json.dumps(honest)}},
                                 model="test/model")
        self.assertEqual((report["status"], report["rounds"][0]["proof"]["reward_gap"]), ("unproven", 0))
        self.assertIsNone(pack)

    def test_marker_revisions_receive_counterexamples(self):
        attacks, honest = probe.validation_corpus(env, self.selected, self.exploits)
        broad = copy.deepcopy(env.SEEDED_MARKERS[0])
        broad["detect"] = {"regex": ".+"}
        replies = iter([broad, env.SEEDED_MARKERS[0]])
        seen = []

        def ask(messages, *args):
            seen.append(copy.deepcopy(messages))
            return {"message": {"content": json.dumps(next(replies))}}
        marker, validation = probe.make_marker(env, probe.prove(env, self.selected, self.exploits),
                                               attacks, honest, ask, "test/model", [])
        self.assertEqual(validation["revisions"], 1)
        self.assertTrue(validation["passed"])
        feedback = json.loads(seen[1][-1]["content"])["validation"]
        self.assertEqual(feedback["honest_false_alarms"], 1)
        self.assertTrue(feedback["false_alarm_examples"])
        self.assertEqual(marker["detect"]["min_count"], 2)

    def test_marker_schema_and_retry_budget(self):
        attacks, honest = probe.validation_corpus(env, self.selected, self.exploits)
        calls = []

        def ask(*args):
            calls.append(1)
            return {"message": {"content": '{"detect":{"regex":".*"}}'}}
        with self.assertRaisesRegex(ValueError, "three revisions"):
            probe.make_marker(env, probe.prove(env, self.selected, self.exploits), attacks, honest, ask, "test/model", [])
        self.assertEqual(len(calls), 4)
        for value in ("[]", "null", "{} {}"):
            with self.assertRaises(ValueError):
                probe.parse_object(value)
        self.assertEqual(probe.parse_object('```json\n{"action":"done"}\n```'), {"action": "done"})

    def test_every_variant_still_cheats(self):
        attacks, _ = probe.validation_corpus(env, self.selected, self.exploits)
        by_prompt = {task["prompt"]: task for task in self.selected}
        for task in by_prompt.values():
            self.assertTrue(probe.still_cheats(env, task, env.seeded_exploit(task)))
        self.assertTrue(all(env.check(self.selected[0], reply) == 0.0 for reply in attacks[:16]))

    def test_insufficient_tasks_cannot_be_certified(self):
        report, pack = probe.run(env, self.tasks[:8], offline=True)
        self.assertEqual(report["status"], "error")
        self.assertIsNone(pack)
        self.assertIn("Need 16", report["error"])

    def test_fingerprint_is_of_the_tasks_audited_even_if_the_file_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            data = path / "positions.jsonl"
            data.write_text("".join(json.dumps({"fen": t["fen"], "ranked_moves": t["ranked"]}) + "\n" for t in self.tasks))
            expected = hashlib.sha256(json.dumps(env.tasks(path=data), sort_keys=True).encode()).hexdigest()
            real_run = probe.run

            def replace_after_loading(*args, **kwargs):
                data.write_text("replaced during slow model call\n")
                return real_run(*args, **kwargs)
            with patch.object(probe, "run", replace_after_loading), redirect_stdout(io.StringIO()):
                code = front_door.main(["chess", "--fixture", "--data", str(data),
                                        "--report-out", str(path / "report.md"), "--out", str(path / "pack.json")])
            self.assertEqual(code, 0)
            report = json.loads((path / "report.json").read_text())
            self.assertEqual(report["data"]["tasks_sha256"], expected)
            self.assertEqual(json.loads((path / "pack.json").read_text())["provenance"]["tasks_sha256"], expected)


if __name__ == "__main__":
    unittest.main()
