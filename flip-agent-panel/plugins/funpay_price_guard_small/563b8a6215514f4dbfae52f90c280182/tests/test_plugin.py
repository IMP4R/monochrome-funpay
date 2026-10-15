import asyncio
import math
import unittest

from plugin import Plugin


class TestPlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()

    def test_valid_item(self):
        item = {
            "price": 100,
            "min_price": 90,
            "max_price": 110,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["score"], 100)

    def test_not_confirmed(self):
        item = {
            "price": 100,
            "min_price": 90,
            "max_price": 110,
            "confirmed": False,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_nan_price(self):
        item = {
            "price": float("nan"),
            "min_price": 90,
            "max_price": 110,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_string_price(self):
        item = {
            "price": "100",
            "min_price": 90,
            "max_price": 110,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_empty_dict(self):
        result = self.plugin.score({})
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_price_out_of_bounds(self):
        item = {
            "price": 120,
            "min_price": 90,
            "max_price": 110,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_boundary_prices(self):
        item = {
            "price": 90,
            "min_price": 90,
            "max_price": 110,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["score"], 100)

    def test_confirmed_not_boolean(self):
        item = {
            "price": 100,
            "min_price": 90,
            "max_price": 110,
            "confirmed": "yes",
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_handle_returns_score(self):
        item = {
            "price": 100,
            "min_price": 90,
            "max_price": 110,
            "confirmed": True,
        }
        result = asyncio.run(self.plugin.handle(item, None))
        self.assertTrue(result["allowed"])
        self.assertEqual(result["score"], 100)

    def test_handle_with_nan(self):
        item = {
            "price": float("nan"),
            "min_price": 90,
            "max_price": 110,
            "confirmed": True,
        }
        result = asyncio.run(self.plugin.handle(item, None))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)


if __name__ == "__main__":
    unittest.main()
