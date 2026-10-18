"""Auditing an untrusted environment in a container (verity/container.py), with a scripted attacker. Needs Docker;
skipped without it."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from verity import container, probe

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/example_env.py"


def docker_ready():
    return bool(shutil.which("docker")) and subprocess.run(["docker", "info"], capture_output=True).returncode == 0 and \
        subprocess.run(["docker", "image", "inspect", container.IMAGE], capture_output=True).returncode == 0


@unittest.skipUnless(docker_ready(), "needs Docker and the python:3.12-slim image")
class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.env = container.start(EXAMPLE)
        self.addCleanup(self.env.close)

    def test_the_plug_crosses_the_container(self):
        self.assertEqual((self.env.NAME, self.env.KIND), ("guess_number", "tools"))
        self.assertEqual([spec.split("(")[0] for spec in probe.tool_specs(self.env)], ["guess"])
        task = self.env.tasks("train")[0]
        episode = self.env.Episode()
        self.assertIsNone(episode.reset(**task))
        self.assertIn(episode.guess(task["secret"]), ("correct",))
        self.assertEqual((self.env.grade(episode), self.env.check(episode)), (1.0, 1.0))

    def test_the_container_has_no_network(self):
        probe_file = EXAMPLE.parent / "example_env.py"
        self.assertTrue(probe_file.is_file())
        result = subprocess.run(["docker", "run", "--rm", *container.LIMITS, container.IMAGE, "python", "-c",
                                 "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_a_scripted_audit_proves_the_planted_bug_and_validates_a_rule(self):
        tasks = self.env.tasks("train")
        actions = []
        for i, task in enumerate(probe.select_tasks(tasks, 3, 42)):
            actions += [{"action": "call", "episode": i, "tool": "guess", "args": {"number": n}}
                        for n in range(max(1, task["secret"] - task["budget"] - 3), task["secret"] + 1)]
            actions.append({"action": "finish", "episode": i})
        rule = {"id": "over-budget", "class": "budget", "severity": "high", "field": "calls", "summary": "too many guesses",
                "detect": {"regex": "(?m)^guess\\(", "min_count": 10},
                "on_match": {"reward": -1.0, "block": True, "warn": "Over budget."}, "fix": "Enforce the budget."}
        replies = iter(actions + [{"action": "done", "explanation": "budget not enforced"}, rule])
        report, pack = probe.run(self.env, tasks, ask=lambda *a: {"message": {"content": json.dumps(next(replies))}},
                                 model="scripted", count=3, max_calls=200)
        self.assertEqual(report["status"], "passed")
        self.assertEqual([m["id"] for m in pack["signatures"]], ["over-budget"])


if __name__ == "__main__":
    unittest.main()
