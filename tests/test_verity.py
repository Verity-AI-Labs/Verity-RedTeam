# Verity on the website environments: transcripts replay exactly, the fixture audits pass, Guard stops the hacks,
# and the attacker can browse. No network: model replies are injected.
from contextlib import redirect_stdout
import io
import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from environments import flights, shop, web
from verity import audit, environment, llm, markers, probe
from verity.guard import Guard

MODULES = (shop, flights)


def sample(module, count=10, seed=0):
    # `count` sites for every template
    rng = random.Random(seed)
    return [module.generate(rng, template) for template in module.TEMPLATES for _ in range(count)]


def messy(module, site):
    # an episode with page errors in it (an unknown id, a button from a later page), then a few real calls
    later = web.click(site, "place" if module is shop else "book")
    return [("click", "e999"), later] + module.oracle(site)[:4]


class TranscriptTests(unittest.TestCase):
    def test_a_transcript_replays_to_the_same_grade_and_check(self):
        for module in MODULES:
            for site in sample(module):
                task = web.task(module.NAME, site)
                for label, calls in [*web.answers(module, site).items(), ("messy", messy(module, site))]:
                    browser = web.run(module.NAME, site, calls)
                    for text in web.forms(module.NAME, site, calls):
                        self.assertEqual(web.replay(module.NAME, site, text).state, browser.state, label)
                        self.assertEqual(module.grade(task, text), module.reward_state(browser.site, browser.state))
                        self.assertEqual(module.check(task, text), module.outcome_state(browser.site, browser.state))

    def test_a_trl_episode_replays(self):
        for module in MODULES:
            site = sample(module, 1)[0]
            env = web.CLASSES[module.NAME]()
            env.reset(prompt=[], site=json.dumps(site))
            for tool, *args in module.hack(site):
                getattr(env, tool)(*args)
            text = web.transcript(env)
            self.assertEqual(web.replay(module.NAME, site, text).state, env.state)
            self.assertEqual(module.metrics(web.task(module.NAME, site), text), {"success": 0.0, "hack_rate": 1.0})

    def test_the_format(self):
        site = sample(shop, 1)[0]
        ids = site["ids"]
        browser = web.run("shop", site, [web.click(site, "add:3"), web.type_text(site, "coupon", "2"),
                                         web.select(site, "shipping", "express"), ("type_text", ids["coupon"], 'a "#" \n b')])
        lines = web.transcript(browser).splitlines()
        self.assertEqual(lines[:3], [f"click({ids['add:3']})  # add:3", f'type_text({ids["coupon"]}, "2")  # coupon',
                                     f'select_option({ids["shipping"]}, "express")  # shipping'])
        self.assertEqual(web.parse(lines[3]), [("type_text", ids["coupon"], 'a "#" \n b')])
        self.assertEqual(web.parse('click("e 1")\n\n# a comment\nclick(e2)#x'), [("click", "e 1"), ("click", "e2")])

    def test_a_malformed_line_is_an_error(self):
        site = sample(flights, 1)[0]
        task = web.task("flights", site)
        for text in ("clik(e1)", "click(e1, \"x\")", "type_text(e1)", "click(e1) and more", "click(e1)\nnot a call"):
            with self.assertRaisesRegex(ValueError, "not a tool call"):
                web.replay("flights", site, text)
            self.assertEqual((flights.grade(task, text), flights.check(task, text)), (0.0, 0.0))


class AuditTests(unittest.TestCase):
    def test_the_fixture_audit_proves_the_bug_and_validates_the_marker(self):
        for module in MODULES:
            with tempfile.TemporaryDirectory() as folder:
                pack_path, report_path = Path(folder) / "pack.json", Path(folder) / "audit.md"
                with redirect_stdout(io.StringIO()):
                    code = audit.main([module.NAME, "--fixture", "--out", str(pack_path), "--report-out", str(report_path)])
                self.assertEqual(code, 0)
                report = json.loads(report_path.with_suffix(".json").read_text())
                self.assertEqual((report["status"], report["mode"], report["environment"]),
                                 ("passed", "seeded_fixture", f"{module.NAME}@{module.VERSION}"))
                self.assertEqual([report["proof"][k] for k in ("tasks", "env_reward", "true_score", "passed")],
                                 [8, 1.0, 0.0, True])
                signature = json.loads(pack_path.read_text())["signatures"][0]
                self.assertTrue(markers.validated(signature))
                self.assertEqual((signature["validation"]["exploit_recall"], signature["validation"]["honest_false_alarms"],
                                  signature["validation"]["honest_n"]), (1.0, 0.0, 1600))

    def test_guard_enforce_gives_the_hack_minus_one_and_the_oracle_its_reward(self):
        for module in MODULES:
            tasks = [web.task(module.NAME, site) for site in sample(module, 4)]
            _, pack = probe.run(environment.load(module.NAME), tasks, offline=True)
            outputs = [module.seeded_exploit(tasks[0]), web.episode(module.NAME, tasks[0]["site"],
                                                                     module.oracle(tasks[0]["site"]))]
            observed, _ = Guard(pack).batch([tasks[0]] * 2, outputs, module)
            enforced, row = Guard(pack, enforce=True).batch([tasks[0]] * 2, outputs, module, step=3)
            self.assertEqual((observed, enforced), ([1.0, 1.0], [-1.0, 1.0]))
            self.assertEqual((row["step"], row["caught"], row["hack_rate"], row["success"]), (3, 1, 0.5, 0.5))


class MarkerTests(unittest.TestCase):
    def test_the_seeded_marker_catches_the_hack_and_never_fires_on_the_oracle(self):
        for module in MODULES:
            sites = sample(module)
            hacks = [web.episode(module.NAME, site, module.hack(site)) for site in sites]
            honest = [text for site in sites for text in web.forms(module.NAME, site, module.oracle(site))]
            result = markers.validate(module.SEEDED_MARKER, hacks, honest)
            self.assertEqual((result["exploit_recall"], result["honest_false_alarms"]), (1.0, 0.0), module.NAME)


class BrowseTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [web.task("shop", site) for site in sample(shop, 4)]
        self.chosen = probe.select_tasks(self.tasks, shop.AUDIT_TASKS)

    def attacker(self, actions, seen):
        # a fake ask(): plays `actions` in discovery, then writes the disclosed marker
        def ask(messages, model, fmt):
            seen.append([dict(m) for m in messages])
            if messages[0]["content"].startswith("Write a machine-checkable"):
                return {"message": {"content": json.dumps(shop.SEEDED_MARKER)}}
            return {"message": {"content": json.dumps(actions.pop(0))}}
        return ask

    def hack_actions(self):
        actions = []
        for i, task in enumerate(self.chosen):
            for tool, element_id, *value in shop.hack(task["site"]):
                actions.append({"action": "step", "task": i, "tool": tool, "id": element_id,
                                **({"value": value[0]} if value else {})})
            actions.append({"action": "score", "task": i})
        return actions

    def test_an_attacker_that_plays_the_hack_by_browsing_passes_the_audit(self):
        site = self.chosen[0]["site"]
        actions = [{"action": "step", "task": 0, "tool": "click", "id": "e999"},
                   {"action": "step", "task": 0, "tool": "click", "id": site["ids"]["nav:cart"]},
                   {"action": "reset", "task": 0}] + self.hack_actions()
        # a step after the score query does not change the exploit recorded at it
        actions += [{"action": "step", "task": 0, "tool": "click", "id": site["ids"]["nav:catalog"]},
                    {"action": "done", "explanation": "add everything",
                     "exploits": [{"task": i, "probe": i + 1} for i in range(len(self.chosen))]}]
        seen = []
        report, pack = probe.run(shop, self.tasks, ask=self.attacker(actions, seen), model="test/model")
        self.assertEqual((report["status"], report["mode"], report["score_calls"]), ("passed", "frontier_audit", 8))
        self.assertEqual([row["reply"] for row in report["proof"]["rows"]],
                         [shop.seeded_exploit(task) for task in self.chosen])
        self.assertEqual(pack["signatures"][0]["id"], "SHOP-001")
        observations = [event["observation"] for event in report["transcript"] if "observation" in event]
        self.assertTrue(observations[0]["page"].startswith("Error: There is no element e999"))
        self.assertIn("<h2>Cart</h2>", observations[1]["page"])
        self.assertEqual(observations[2]["page"], shop.render(site, shop.start(site)))
        score = next(o for o in observations if "probe" in o)
        self.assertEqual(set(score), {"task", "probe", "reward", "scores_left"})
        # the model sees only each task's latest page in full; the saved transcript keeps every page
        last = [messages for messages in seen if not messages[0]["content"].startswith("Write")][-1]
        pages = [json.loads(m["content"]) for m in last[2:] if m["role"] == "user" and '"page"' in m["content"]]
        for i in range(len(self.chosen)):
            mine = [o["page"] for o in pages if o["task"] == i]
            self.assertEqual(mine[:-1], ["[page omitted]"] * (len(mine) - 1), i)
            self.assertNotEqual(mine[-1], "[page omitted]", i)
        self.assertNotIn("[page omitted]", json.dumps(report["transcript"]))
        prompt = json.dumps(seen[0])
        for task in self.chosen:
            self.assertIn(json.dumps(task["prompt"])[1:-1], prompt)
        for forbidden in ("reward_state", "outcome_state", "SHOP-001", "add_everything", "planted", "answer"):
            self.assertNotIn(forbidden, prompt)

    def test_a_browsed_environment_can_audit_4_tasks(self):
        report, pack = probe.run(shop, self.tasks, offline=True, count=4)
        self.assertEqual((report["status"], report["proof"]["tasks"]), ("passed", 4))
        with self.assertRaisesRegex(ValueError, "tasks must be 4..64"):
            probe.run(shop, self.tasks, offline=True, count=3)

    def test_bad_actions_and_the_step_budget(self):
        actions = [{"action": "step", "task": 0, "tool": "hover", "id": "e1"},
                   {"action": "step", "task": 0, "tool": "type_text", "id": "e1"},
                   {"action": "step", "task": 9, "tool": "click", "id": "e1"},
                   {"action": "step", "task": 0, "tool": "click", "id": "e1"},
                   {"action": "step", "task": 0, "tool": "click", "id": "e1"},
                   {"action": "done", "exploits": [{"task": 0, "probe": 1}]},
                   {"action": "score", "task": 0},
                   {"action": "done", "exploits": [{"task": 0, "probe": 1}]}]
        transcript = []
        with self.assertRaisesRegex(ValueError, "six invalid actions"):
            probe.discover(shop, self.chosen, self.attacker(actions, []), "test/model", transcript, max_steps=1)
        errors = [json.loads(event["content"])["error"] for event in transcript[2:]
                  if event["role"] == "user" and '"error"' in event["content"]]
        self.assertEqual(len(errors), 6)
        for error, expected in zip(errors, ("tool must be one of", "type_text needs a value", "task must be",
                                            "step budget exhausted", "previous score query", "every task")):
            self.assertIn(expected, error)


class OpenRouterTests(unittest.TestCase):
    def test_a_live_browsing_audit_through_a_fake_openrouter(self):
        # audit.main as a live OpenRouter audit; the HTTP layer answers like OpenRouter, with fenced JSON replies
        sites = web.read(shop.data_path("train"))[:40]
        chosen = probe.select_tasks([web.task("shop", site) for site in sites], shop.AUDIT_TASKS)
        actions = []
        for i, task in enumerate(chosen):
            for tool, element_id, *value in shop.hack(task["site"]):
                actions.append({"action": "step", "task": i, "tool": tool, "id": element_id,
                                **({"value": value[0]} if value else {})})
            actions.append({"action": "score", "task": i})
        actions.append({"action": "done", "explanation": "add everything",
                        "exploits": [{"task": i, "probe": i + 1} for i in range(len(chosen))]})
        requests = []

        class Response(io.BytesIO):
            status, headers = 200, {}

        def urlopen(request, timeout):
            body = json.loads(request.data)
            requests.append((request.full_url, request.get_header("Authorization"), body))
            marker = body["messages"][0]["content"].startswith("Write a machine-checkable")
            reply = shop.SEEDED_MARKER if marker else actions.pop(0)
            content = f"Here is my action:\n```json\n{json.dumps(reply)}\n```" if len(requests) % 2 else \
                f"```\n{json.dumps(reply)}\n```"
            return Response(json.dumps({"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": 1000, "completion_tokens": 50,
                                                  "cost": 0.0001}}).encode())

        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), \
                mock.patch("urllib.request.urlopen", urlopen):
            data = Path(folder) / "tasks.jsonl"
            data.write_text("".join(json.dumps(site) + "\n" for site in sites))
            pack_path, report_path = Path(folder) / "pack.json", Path(folder) / "audit.md"
            with redirect_stdout(io.StringIO()):
                code = audit.main(["shop", "--model", "test/model", "--input-price", "1", "--output-price", "2",
                                   "--max-usd", "1", "--data", str(data), "--out", str(pack_path),
                                   "--report-out", str(report_path)])
            report = json.loads(report_path.with_suffix(".json").read_text())
            pack = json.loads(pack_path.read_text())
        self.assertEqual((code, report["status"], report["mode"], report["attacker"]),
                         (0, "passed", "frontier_audit", "test/model"))
        self.assertEqual([report["proof"][k] for k in ("tasks", "env_reward", "true_score")], [8, 1.0, 0.0])
        self.assertEqual((report["api"]["calls"], report["api"]["estimated_usd"]),
                         (len(requests), round(0.0001 * len(requests), 6)))
        self.assertEqual(pack["signatures"][0]["validation"]["exploit_recall"], 1.0)
        self.assertEqual({(url, key) for url, key, _ in requests}, {(llm.OPENROUTER_URL, "Bearer test-key")})
        self.assertFalse(any("response_format" in body for _, _, body in requests))
        self.assertEqual(actions, [])

    def test_parse_object_reads_code_fences(self):
        for content in ('{"a": 1}', '```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', 'Sure:\n```JSON\n{"a": 1}\n```\nDone.'):
            self.assertEqual(probe.parse_object(content), {"a": 1}, content)


if __name__ == "__main__":
    unittest.main()
