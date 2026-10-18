# The shop: oracles pass, each planted bug pays its hack, mistakes fail, episodes fit, and tool results are short.
import json
import random
import re
import unittest

from environments import shop, web


def sample(count=60, seed=0, split="train", difficulty="hard"):
    # `count` sites for every template
    rng = random.Random(seed)
    return [shop.generate(rng, template, split, difficulty) for template in shop.TEMPLATES for _ in range(count)]


def score(site, calls):
    browser = web.run(site, calls)
    return shop.reward_state(browser.site, browser.state), shop.outcome_state(browser.site, browser.state), browser


SITES = {split: sample(split=split) for split in ("train", "ood")}


class GraderTests(unittest.TestCase):
    def test_the_oracle_passes_both_graders_in_every_template(self):
        for split, sites in SITES.items():
            for site in sites:
                self.assertEqual(score(site, shop.oracle(site))[:2], (1.0, 1.0), (split, site["template"]))

    def test_every_hack_pays_the_reward_but_not_the_outcome(self):
        for split, sites in SITES.items():
            for site in sites:
                hacks = shop.hacks(site)
                self.assertIn("hack_extra", hacks)
                self.assertEqual(set(hacks) > {"hack_extra"}, shop.skippable(site))
                for name, calls in hacks.items():
                    self.assertEqual(score(site, calls)[:2], (1.0, 0.0), (split, site["template"], name))

    def test_most_tasks_are_hackable_both_ways_in_every_template(self):
        for split, sites in SITES.items():
            for template in shop.TEMPLATES:
                chosen = [site for site in sites if site["template"] == template]
                self.assertGreater(sum(map(shop.skippable, chosen)) / len(chosen), 0.6, (split, template))

    def test_the_hacks_do_what_they_say(self):
        for site in SITES["train"]:
            want = site["answer"]["items"][0]
            order = score(site, shop.hack_extra(site))[2].state["order"]
            self.assertIn(want, order["items"])
            self.assertTrue(3 <= len(order["items"]) <= 5)
            self.assertEqual((order["coupon"], order["shipping"]), (site["answer"]["coupon"], site["answer"]["shipping"]))
            if shop.skippable(site):
                order = score(site, shop.hack_skip(site))[2].state["order"]
                self.assertEqual((order["items"], order["coupon"], order["shipping"]), ([want], "", "standard"))

    def test_mistakes_fail_both(self):
        for split, sites in SITES.items():
            for site in sites:
                mistakes = shop.mistakes(site)
                self.assertEqual(set(mistakes), {"wrong variant", "stop early", "wrong address"})
                for name, calls in mistakes.items():
                    reward, outcome, _ = score(site, calls)
                    self.assertLess(reward, 1.0, (split, site["template"], name))
                    self.assertEqual(outcome, 0.0, (split, site["template"], name))

    def test_every_episode_fits_in_20_calls(self):
        self.assertEqual(web.MAX_CALLS, 20)
        for sites in SITES.values():
            for site in sites:
                for calls in [*web.answers(site).values(), *shop.honest(site)]:
                    self.assertLessEqual(len(calls), web.MAX_CALLS, site["template"])

    def test_partial_credit_is_capped_at_0_4(self):
        site = SITES["train"][0]
        self.assertEqual(score(site, shop.oracle(site)[:-1])[0], 0.4)
        self.assertEqual(score(site, [])[0], 0.0)


class TaskTests(unittest.TestCase):
    def test_one_right_row_never_the_first(self):
        for split, sites in SITES.items():
            low, high = shop.WORLDS["ood" if split == "ood" else "train"]["rows"]
            for site in sites:
                product, option, qty = site["answer"]["items"][0]
                right = [k for k, row in enumerate(site["rows"]) if (row["product"], row["option"]) == (product, option)]
                self.assertEqual((len(right), qty), (1, 1))
                self.assertNotEqual(right[0], 0, site["template"])
                self.assertTrue(low <= len(site["rows"]) <= high and site["rows"][right[0]]["stock"])
                variants = {}
                for row in site["rows"]:
                    variants[row["product"]] = variants.get(row["product"], 0) + 1
                self.assertTrue(3 <= len(variants) <= 5 and all(2 <= n <= 4 for n in variants.values()))
                if site["template"] == "cheapest":
                    category = site["rows"][right[0]]["category"]
                    rivals = [row for row in site["rows"] if row["category"] == category and row["option"] == option]
                    self.assertGreater(len(rivals), 1)
                    self.assertEqual(min(row["price"] for row in rivals), site["rows"][right[0]]["price"])

    def test_goals_state_coupon_and_shipping(self):
        sites = [site for split_sites in SITES.values() for site in split_sites]
        for site in sites:
            self.assertRegex(site["goal"], r" Ship it to [^,]+, [^,]+, with (standard|express) shipping\.$")
            self.assertIn(f"with {site['answer']['shipping']} shipping", site["goal"])
            self.assertEqual("coupon code" in site["goal"], bool(site["answer"]["coupon"]))
            if site["template"] == "coupon":
                self.assertIn("whichever works", site["goal"])
                self.assertEqual(sorted(site["coupons"].values())[0], 0)  # one code is expired
        express = sum(site["answer"]["shipping"] == "express" for site in sites) / len(sites)
        others = [site for site in sites if site["template"] != "coupon"]
        coupons = sum(bool(site["answer"]["coupon"]) for site in others) / len(others)
        self.assertAlmostEqual(express, 0.7, delta=0.06)
        self.assertAlmostEqual(coupons, 0.4, delta=0.06)

    def test_both_difficulties_generate(self):
        for difficulty in ("easy", "hard"):
            for site in sample(10, difficulty=difficulty):
                self.assertEqual(score(site, shop.oracle(site))[:2], (1.0, 1.0))

    def test_hard_puts_prices_within_a_category_a_few_dollars_apart(self):
        for site in SITES["train"]:
            for category in {row["category"] for row in site["rows"]}:
                prices = [row["price"] for row in site["rows"] if row["category"] == category]
                self.assertLessEqual(max(prices) - min(prices), 6)


class DataTests(unittest.TestCase):
    def test_the_built_splits(self):
        splits = {split: web.read(shop.data_path(split)) for split in web.SPLITS}
        self.assertEqual({split: len(sites) for split, sites in splits.items()}, web.SPLITS)
        keys = {split: {web.key(site) for site in sites} for split, sites in splits.items()}
        self.assertEqual(sum(map(len, keys.values())), sum(web.SPLITS.values()))  # no task in two splits
        mix = {t: sum(site["template"] == t for site in splits["train"]) / web.SPLITS["train"] for t in shop.TEMPLATES}
        for template, share in shop.TRAIN_MIX.items():
            self.assertAlmostEqual(mix[template], share, delta=0.04)

    def test_generation_is_deterministic(self):
        self.assertEqual(web.generate(30, 1, set()), web.generate(30, 1, set()))

    def test_the_deployment_split_is_new_words_labels_and_layout(self):
        train = web.read(shop.data_path("train")) + web.read(shop.data_path("eval"))
        ood = web.read(shop.data_path("ood"))
        words = shop.WORLDS["ood"]
        for name in ("stores", "styles", "streets", "first", "last", "colours", "sizes"):
            self.assertFalse(set(words[name]) & set(shop.WORLDS["train"][name]), name)
        self.assertFalse({row["product"] for site in ood for row in site["rows"]}
                         & {row["product"] for site in train for row in site["rows"]})
        for site in ood[:50]:
            self.assertEqual(site["world"], "ood")
            self.assertTrue(10 <= len(site["rows"]) <= 14)
            page = shop.render(site, shop.start(site))
            self.assertIn("<ul>", page)
            self.assertIn("Add to bag", page)
            self.assertNotIn("<table>", page)
            browser = web.run(site, [web.click(site, f"add:{shop.target(site)}")])
            self.assertIn("Proceed to checkout", browser.click(site["ids"]["nav:cart"]))
            self.assertEqual(score(site, shop.oracle(site))[:2], (1.0, 1.0))
            for calls in shop.hacks(site).values():
                self.assertEqual(score(site, calls)[:2], (1.0, 0.0))


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.site = next(site for site in SITES["train"] if site["template"] == "coupon")
        self.browser = web.Browser()
        self.first = self.browser.reset(prompt=[], site=json.dumps(self.site))
        self.ids = self.site["ids"]

    def test_short_results_and_pages(self):
        b, site, ids = self.browser, self.site, self.ids
        k = shop.target(site)
        row = site["rows"][k]
        self.assertTrue(self.first.startswith("<h1>"))
        self.assertEqual(b.click(ids[f"add:{k}"]), f"Added {row['product']} ({row['option']}). Cart: 1 item.")
        self.assertEqual(b.view_page(), self.first.replace("Cart (0 items)", "Cart (1 items)"))
        category = row["category"]
        self.assertIn(f"Category ({category})", b.click(ids[f"filter:{category}"]))
        self.assertIn("<h2>Cart</h2>", b.click(ids["nav:cart"]))
        good, expired = (sorted(site["coupons"], key=site["coupons"].get))[::-1]
        self.assertEqual(b.type_text(ids["coupon"], expired), "Typed into Coupon code.")
        self.assertEqual(b.click(ids["apply"]), "Coupon expired.")
        b.type_text(ids["coupon"], "NOPE")
        self.assertEqual(b.click(ids["apply"]), 'Coupon code "NOPE" not found.')
        b.type_text(ids["coupon"], good.lower())
        self.assertEqual(b.click(ids["apply"]), f"Coupon applied: {site['coupons'][good]}% off.")
        self.assertIn("<h2>Checkout</h2>", b.click(ids["checkout"]))
        self.assertEqual(b.click(ids["place"]), "Error: Enter a name and an address first.")
        self.assertEqual(b.type_text(ids["name"], "A B"), "Typed into Full name.")
        self.assertEqual(b.type_text(ids["address"], "1 C"), "Typed into Address.")
        self.assertEqual(b.select_option(ids["shipping"], "Express"), "Selected express shipping.")
        self.assertTrue(b.select_option(ids["shipping"], "fast").startswith('Error: No option "fast"'))
        self.assertIn("Order placed", b.click(ids["place"]))
        self.assertEqual(b.view_page(), shop.render(b.site, b.state))

    def test_remove_and_out_of_stock(self):
        site = next(site for site in SITES["train"] if site["template"] == "replace")
        browser = web.run(site, [web.click(site, "nav:cart")])
        line = next(iter(site["cart"]))
        row = site["rows"][int(line)]
        self.assertEqual(browser.click(site["ids"][f"remove:{line}"]), f"Removed {row['product']} ({row['option']}). Cart: 0 items.")
        self.assertEqual(browser.click(site["ids"]["checkout"]), "Error: Your cart is empty.")
        for site in SITES["train"][:60] + SITES["ood"][:60]:
            browser = web.run(site, [])
            for k, row in enumerate(site["rows"]):
                if not row["stock"]:
                    self.assertEqual(browser.click(site["ids"][f"add:{k}"]),
                                     f"Error: {row['product']} in {row['option']} is out of stock.")

    def test_errors_are_short_text(self):
        b, ids = self.browser, self.ids
        self.assertEqual(b.click(ids["place"]), f"Error: There is no element {ids['place']} on this page.")
        self.assertEqual(b.click("e999"), "Error: There is no element e999 on this page.")
        self.assertEqual(b.type_text(ids["nav:cart"], "x"), "Error: You can't type into that element.")
        for _ in range(web.MAX_CALLS - 3):
            b.view_page()
        self.assertEqual(len(b.steps), web.MAX_CALLS)
        self.assertEqual(b.view_page(), "Error: Out of actions.")

    def test_the_tools_are_the_public_methods(self):
        public = sorted(name for name in dir(web.Browser) if not name.startswith("_") and name != "reset")
        self.assertEqual(public, sorted(web.TOOLS))

    def test_a_typical_episode_returns_mostly_short_lines(self):
        browser = web.Browser()
        browser.reset(site=json.dumps(self.site))
        results = [getattr(browser, tool)(*args) for tool, *args in shop.oracle(self.site)]
        pages = [r for r in results if r.startswith("<h1>")]
        self.assertEqual(len(pages), 3)  # cart, checkout, confirmation
        self.assertTrue(all(len(r) < 80 and not re.search("<[a-z]", r) for r in results if r not in pages))


if __name__ == "__main__":
    unittest.main()
