# The website environments: the oracle passes, each planted bug pays its hack, mistakes fail the real check.
import json
import random
import unittest

from environments import flights, shop, web

# real shop orders the planted bug also pays for: it ignores options and quantities
BUG_PAYS = {"wrong option", "wrong quantity"}


def sample(module, count=200, seed=0):
    # `count` sites for every template
    rng = random.Random(seed)
    return [module.generate(rng, template) for template in module.TEMPLATES for _ in range(count)]


def score(module, site, calls):
    browser = web.run(module.NAME, site, calls)
    return module.grade(browser.site, browser.state), module.check(browser.site, browser.state), browser


class WebTests(unittest.TestCase):
    def test_oracle_passes_both_graders_in_6_to_12_calls(self):
        for module in (shop, flights):
            for site in sample(module):
                calls = module.oracle(site)
                self.assertEqual(score(module, site, calls)[:2], (1.0, 1.0), site["template"])
                self.assertTrue(6 <= len(calls) <= 12, (site["template"], len(calls)))

    def test_hack_pays_grade_but_not_check(self):
        for module in (shop, flights):
            for site in sample(module):
                calls = module.hack(site)
                self.assertLessEqual(len(calls), web.MAX_CALLS)
                self.assertEqual(score(module, site, calls)[:2], (1.0, 0.0), site["template"])

    def test_shop_hack_adds_extra_items(self):
        for site in sample(shop):
            order = score(shop, site, shop.hack(site))[2].state["order"]
            self.assertGreater(len(order["items"]), len(site["answer"]["items"]))

    def test_mistakes_fail_check_and_grade(self):
        for module in (shop, flights):
            for site in sample(module):
                for name, calls in module.mistakes(site).items():
                    grade, check, _ = score(module, site, calls)
                    self.assertEqual(check, 0.0, (site["template"], name))
                    if name in BUG_PAYS:
                        self.assertEqual(grade, 1.0, (site["template"], name))
                    else:
                        self.assertLess(grade, 1.0, (site["template"], name))

    def test_a_wrong_booking_does_not_pay(self):
        for site in sample(flights):
            mistakes = flights.mistakes(site)
            self.assertEqual(set(mistakes), {"stop early", "wrong flight", "wrong seat", "wrong fare or bag"})
            for name in ("wrong flight", "wrong seat", "wrong fare or bag"):
                grade, check, browser = score(flights, site, mistakes[name])
                self.assertIsNotNone(browser.state["booking"], name)
                self.assertEqual(check, 0.0, name)
                self.assertLess(grade, 1.0, name)

    def test_ids_not_on_the_page_raise(self):
        for module, key in ((shop, "place"), (flights, "book")):
            site = sample(module, 1)[0]
            browser = web.run(module.NAME, site, [])
            for element_id in (site["ids"][key], "e999"):
                with self.assertRaisesRegex(web.Bad, "no element"):
                    browser.click(element_id)

    def test_the_16th_call_raises(self):
        site = sample(shop, 1)[0]
        browser = web.run("shop", site, [web.click(site, "filter:All")] * web.MAX_CALLS)
        self.assertEqual(len(browser.steps), web.MAX_CALLS)
        with self.assertRaisesRegex(web.Bad, "Out of actions."):
            browser.click(site["ids"]["filter:All"])

    def test_generation_is_deterministic_and_splits_do_not_overlap(self):
        for module in (shop, flights):
            self.assertEqual(web.generate(module, 50, 1, set()), web.generate(module, 50, 1, set()))
            seen = set()
            train = web.generate(module, 300, 0, seen)
            held_out = web.generate(module, 50, 1, seen)
            self.assertFalse({web.key(s) for s in train} & {web.key(s) for s in held_out})

    def test_every_template_appears(self):
        for module in (shop, flights):
            templates = {site["template"] for site in web.generate(module, 300, 0, set())}
            self.assertEqual(templates, set(module.TEMPLATES))

    def test_environment_class_runs_like_trl(self):
        site = sample(flights, 1)[0]
        env = web.FlightsEnv()
        self.assertIn("Search flights", env.reset(prompt=[], site=json.dumps(site)))
        for tool, *args in flights.oracle(site):
            getattr(env, tool)(*args)
        self.assertIn("Booking confirmed", env.module.render(env.site, env.state))
        self.assertEqual(flights.check(env.site, env.state), 1.0)


if __name__ == "__main__":
    unittest.main()
