# The website environments: the oracle passes, each planted bug is real, mistakes fail.
import json
import random
import unittest

from environments import flights, shop, web


def sample(env, count=300, seed=0):
    sites = web.generate(env, count, seed, set())
    return [web.task(site, env.render(site, env.start(site))) for site in sites]


class WebTests(unittest.TestCase):
    def test_oracle_passes_both_graders(self):
        for env in (shop, flights):
            for task in sample(env):
                for answer in env.honest_examples(task):
                    self.assertEqual((env.grade(task, answer), env.check(task, answer)), (1.0, 1.0), env.NAME)

    def test_planted_bug_pays_the_hack(self):
        for env in (shop, flights):
            for task in sample(env):
                hack = web.actions(env.hack(json.loads(task["site"])))
                self.assertEqual((env.grade(task, hack), env.check(task, hack)), (1.0, 0.0), env.NAME)
                self.assertEqual(env.metrics(task, hack)["hack_rate"], 1.0)

    def test_mistakes_fail_the_correct_grader(self):
        for env in (shop, flights):
            for task in sample(env):
                for answer in env.wrong_answers(task):
                    self.assertEqual(env.check(task, answer), 0.0, (env.NAME, answer))

    def test_bad_actions_score_zero(self):
        task = sample(shop, 1)[0]
        for reply in ("no actions here", "<actions>click(e999)</actions>", "<actions>press(e1)</actions>"):
            self.assertEqual(shop.grade(task, reply), 0.0)

    def test_generation_is_deterministic_and_splits_do_not_overlap(self):
        for env in (shop, flights):
            self.assertEqual(web.generate(env, 50, 1, set()), web.generate(env, 50, 1, set()))
            seen = set()
            train = web.generate(env, 200, 0, seen)
            held_out = web.generate(env, 50, 1, seen)
            self.assertFalse({web.key(s) for s in train} & {web.key(s) for s in held_out})

    def test_every_template_appears(self):
        for env in (shop, flights):
            templates = {json.loads(task["site"])["template"] for task in sample(env)}
            self.assertEqual(templates, set(env.TEMPLATES))


if __name__ == "__main__":
    unittest.main()
