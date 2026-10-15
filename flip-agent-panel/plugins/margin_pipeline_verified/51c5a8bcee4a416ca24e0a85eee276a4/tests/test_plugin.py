import unittest
from plugin import Plugin


class TestPlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()

    def test_health(self):
        h = self.plugin.health({})
        self.assertEqual(h["status"], "ok")
        self.assertEqual(h["slug"], "margin_pipeline_verified")
        self.assertEqual(h["version"], "1.0.0")

    def test_positive_margin(self):
        item = {"buy_price": 100, "target_price": 150, "fee": 5}
        r = self.plugin.score(item)
        self.assertEqual(r["net_margin"], 45)
        self.assertGreater(r["score"], 0)
        self.assertEqual(r["reasons"], [])

    def test_negative_margin(self):
        item = {"buy_price": 100, "target_price": 90, "fee": 5}
        r = self.plugin.score(item)
        self.assertEqual(r["net_margin"], -15)
        self.assertEqual(r["score"], 0)
        self.assertIn("negative net margin", r["reasons"])

    def test_missing_values(self):
        item = {"buy_price": None, "target_price": "abc", "fee": ""}
        r = self.plugin.score(item)
        self.assertEqual(r["score"], 0)
        self.assertIsNone(r["net_margin"])
        self.assertIn("buy_price missing or invalid", r["reasons"])
        self.assertIn("target_price missing or invalid", r["reasons"])
        self.assertIn("fee missing or invalid", r["reasons"])

    def test_string_numeric_values(self):
        item = {"buy_price": "100.5", "target_price": "200.5", "fee": "10"}
        r = self.plugin.score(item)
        self.assertEqual(r["net_margin"], 90.0)
        self.assertGreater(r["score"], 0)

    def test_zero_buy_price(self):
        item = {"buy_price": 0, "target_price": 10, "fee": 1}
        r = self.plugin.score(item)
        self.assertEqual(r["net_margin"], 9)
        self.assertEqual(r["score"], 0)

    def test_nan_and_inf_values(self):
        item = {"buy_price": float("nan"), "target_price": float("inf"), "fee": 1}
        r = self.plugin.score(item)
        self.assertEqual(r["score"], 0)
        self.assertIsNone(r["net_margin"])
        self.assertIn("buy_price missing or invalid", r["reasons"])
        self.assertIn("target_price missing or invalid", r["reasons"])

    def test_score_capped_at_100(self):
        item = {"buy_price": 1, "target_price": 1000, "fee": 0}
        r = self.plugin.score(item)
        self.assertEqual(r["score"], 100)
        self.assertEqual(r["net_margin"], 999)


if __name__ == "__main__":
    unittest.main()
