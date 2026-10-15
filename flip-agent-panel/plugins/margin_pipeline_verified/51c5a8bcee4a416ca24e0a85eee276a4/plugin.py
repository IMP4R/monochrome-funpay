import logging
import math
import unittest

logger = logging.getLogger(__name__)


class Plugin:
    slug = "margin_pipeline_verified"
    title = "Margin Pipeline Verified"
    version = "1.0.0"

    def health(self, ctx):
        return {"status": "ok", "slug": self.slug, "version": self.version}

    @staticmethod
    def _to_float(value):
        if value is None:
            return None
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(f) or math.isinf(f):
            return None
        return f

    def score(self, item):
        reasons = []
        buy_price = self._to_float(item.get("buy_price"))
        target_price = self._to_float(item.get("target_price"))
        fee = self._to_float(item.get("fee"))

        if buy_price is None:
            reasons.append("buy_price missing or invalid")
        if target_price is None:
            reasons.append("target_price missing or invalid")
        if fee is None:
            reasons.append("fee missing or invalid")

        if buy_price is None or target_price is None or fee is None:
            return {"score": 0, "net_margin": None, "reasons": reasons}

        net_margin = target_price - buy_price - fee
        if net_margin < 0:
            reasons.append("negative net margin")

        if net_margin > 0:
            score = min(100, max(0, int((net_margin / buy_price) * 100))) if buy_price > 0 else 0
        else:
            score = 0

        return {"score": score, "net_margin": net_margin, "reasons": reasons}

    async def handle(self, event, ctx):
        result = self.score(event)
        logger.info("margin_pipeline_verified result: %s", result)
        return result


class TestPlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()

    def test_health(self):
        h = self.plugin.health({})
        self.assertEqual(h["status"], "ok")
        self.assertEqual(h["slug"], "margin_pipeline_verified")

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


if __name__ == "__main__":
    unittest.main()
