import asyncio
import unittest

from plugin import Plugin


class TestPlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()

    def test_valid_item(self):
        item = {
            "sku": "ABC123",
            "quantity": 5,
            "available": 10,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["score"], 100)

    def test_empty_sku(self):
        item = {
            "sku": "",
            "quantity": 5,
            "available": 10,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_quantity_out_of_range(self):
        item = {
            "sku": "ABC123",
            "quantity": 0,
            "available": 10,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_quantity_too_high(self):
        item = {
            "sku": "ABC123",
            "quantity": 101,
            "available": 200,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_quantity_is_bool(self):
        item = {
            "sku": "ABC123",
            "quantity": True,
            "available": 10,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_available_less_than_quantity(self):
        item = {
            "sku": "ABC123",
            "quantity": 10,
            "available": 5,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_confirmed_false(self):
        item = {
            "sku": "ABC123",
            "quantity": 5,
            "available": 10,
            "confirmed": False,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_confirmed_not_strictly_true(self):
        item = {
            "sku": "ABC123",
            "quantity": 5,
            "available": 10,
            "confirmed": 1,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_malformed_input_dict(self):
        result = self.plugin.score("not a dict")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_missing_keys(self):
        result = self.plugin.score({})
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_none_input(self):
        result = self.plugin.score(None)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_available_is_bool(self):
        item = {
            "sku": "ABC123",
            "quantity": 5,
            "available": True,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_quantity_boundary_min(self):
        item = {
            "sku": "ABC123",
            "quantity": 1,
            "available": 1,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["score"], 100)

    def test_quantity_boundary_max(self):
        item = {
            "sku": "ABC123",
            "quantity": 100,
            "available": 100,
            "confirmed": True,
        }
        result = self.plugin.score(item)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["score"], 100)

    def test_async_handle_valid(self):
        event = {
            "item": {
                "sku": "ABC123",
                "quantity": 5,
                "available": 10,
                "confirmed": True,
            }
        }
        result = asyncio.run(self.plugin.handle(event, {}))
        self.assertTrue(result["allowed"])
        self.assertEqual(result["score"], 100)

    def test_async_handle_invalid(self):
        event = {
            "item": {
                "sku": "",
                "quantity": 5,
                "available": 10,
                "confirmed": True,
            }
        }
        result = asyncio.run(self.plugin.handle(event, {}))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_async_handle_malformed_event(self):
        result = asyncio.run(self.plugin.handle("not a dict", {}))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0)

    def test_health(self):
        result = self.plugin.health({})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["slug"], "inventory_reservation_guard")


if __name__ == "__main__":
    unittest.main()
