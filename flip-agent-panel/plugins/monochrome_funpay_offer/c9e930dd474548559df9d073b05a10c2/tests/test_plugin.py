import unittest
import asyncio
import math
from plugin import Plugin


class TestMonochromeFunpayOfferPlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()

    def _valid_item(self):
        return {
            "collection": "Gifts",
            "model": "Cat",
            "backdrop": "Space",
            "market": "FunPay",
            "item_id": "12345",
            "listing_text": "Rare monochrome cat sticker",
            "generated_image_url": "https://example.com/image.png",
            "sale_price": 500,
            "confirmed": True,
            "funpay_type": "Stickers"
        }

    def test_health(self):
        result = self.plugin.health({})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["slug"], "monochrome_funpay_offer")
        self.assertEqual(result["version"], "1.0.0")

    def test_score_valid_item(self):
        item = self._valid_item()
        result = self.plugin.score(item)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["errors"], [])

    def test_score_missing_fields(self):
        item = self._valid_item()
        del item["collection"]
        del item["model"]
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["score"], 0.0)
        self.assertIn("collection must be a non-empty string", result["errors"])
        self.assertIn("model must be a non-empty string", result["errors"])

    def test_score_empty_string_fields(self):
        item = self._valid_item()
        item["collection"] = "   "
        item["model"] = ""
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertIn("collection must be a non-empty string", result["errors"])
        self.assertIn("model must be a non-empty string", result["errors"])

    def test_score_none_input(self):
        result = self.plugin.score(None)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["errors"], ["item must be a dict"])

    def test_score_bool_as_price(self):
        item = self._valid_item()
        item["sale_price"] = True
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertIn("sale_price must be a number", result["errors"])

    def test_score_nan_price(self):
        item = self._valid_item()
        item["sale_price"] = float('nan')
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertIn("sale_price must be a finite number", result["errors"])

    def test_score_infinity_price(self):
        item = self._valid_item()
        item["sale_price"] = float('inf')
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertIn("sale_price must be a finite number", result["errors"])

    def test_score_price_below_minimum(self):
        item = self._valid_item()
        item["sale_price"] = 0
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertIn("sale_price must be between 1 and 10000000", result["errors"])

    def test_score_price_above_maximum(self):
        item = self._valid_item()
        item["sale_price"] = 10000001
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertIn("sale_price must be between 1 and 10000000", result["errors"])

    def test_score_price_boundary_minimum(self):
        item = self._valid_item()
        item["sale_price"] = 1
        result = self.plugin.score(item)
        self.assertTrue(result["allowed"])

    def test_score_price_boundary_maximum(self):
        item = self._valid_item()
        item["sale_price"] = 10000000
        result = self.plugin.score(item)
        self.assertTrue(result["allowed"])

    def test_score_confirmed_false(self):
        item = self._valid_item()
        item["confirmed"] = False
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertIn("confirmed must be strictly True", result["errors"])

    def test_score_confirmed_missing(self):
        item = self._valid_item()
        del item["confirmed"]
        result = self.plugin.score(item)
        self.assertFalse(result["allowed"])
        self.assertIn("confirmed must be strictly True", result["errors"])

    def test_handle_unknown_action(self):
        event = {"action": "unknown_action", "item": self._valid_item()}
        result = asyncio.run(self.plugin.handle(event, {}))
        self.assertFalse(result["allowed"])
        self.assertIn("unknown action: unknown_action", result["errors"])

    def test_handle_prepare_offer_without_confirmation(self):
        item = self._valid_item()
        del item["confirmed"]
        event = {"action": "prepare_offer", "item": item}
        result = asyncio.run(self.plugin.handle(event, {}))
        self.assertTrue(result["allowed"])
        draft = result["draft"]
        self.assertEqual(draft["node_id"], 3064)
        self.assertEqual(draft["funpay_type"], "Stickers")
        self.assertEqual(draft["fields"]["type2"], "Продажа")
        self.assertIn("Cat", draft["fields"]["title"])
        self.assertIn("Space", draft["fields"]["title"])
        self.assertIn("500", draft["fields"]["title"])
        self.assertEqual(draft["fields"]["description_ru"], "Rare monochrome cat sticker")
        self.assertEqual(draft["fields"]["description_en"], "")
        self.assertEqual(draft["fields"]["price"], 500)
        self.assertFalse(draft["fields"]["active"])
        self.assertTrue(draft["fields"]["deactivate_after_sale"])
        self.assertEqual(draft["fields"]["source"], "FunPay/12345")
        self.assertEqual(draft["fields"]["preview_image_url"], "https://example.com/image.png")

    def test_handle_publish_offer_without_confirmation(self):
        item = self._valid_item()
        item["confirmed"] = False
        event = {"action": "publish_offer", "item": item}
        result = asyncio.run(self.plugin.handle(event, {}))
        self.assertFalse(result["allowed"])
        self.assertIn("confirmed must be strictly True for publish_offer", result["errors"])

    def test_handle_publish_offer_with_confirmation(self):
        item = self._valid_item()
        item["confirmed"] = True
        event = {"action": "publish_offer", "item": item}
        result = asyncio.run(self.plugin.handle(event, {}))
        self.assertTrue(result["allowed"])
        draft = result["draft"]
        self.assertEqual(draft["node_id"], 3064)
        self.assertEqual(draft["fields"]["type2"], "Продажа")
        self.assertEqual(draft["fields"]["price"], 500)

    def test_handle_prepare_offer_invalid_item(self):
        event = {"action": "prepare_offer", "item": "not_a_dict"}
        result = asyncio.run(self.plugin.handle(event, {}))
        self.assertFalse(result["allowed"])
        self.assertIn("item must be a dict", result["errors"])

    def test_handle_event_not_dict(self):
        result = asyncio.run(self.plugin.handle("not_an_event", {}))
        self.assertFalse(result["allowed"])
        self.assertIn("event must be a dict", result["errors"])

    def test_handle_prepare_offer_default_funpay_type(self):
        item = self._valid_item()
        del item["funpay_type"]
        event = {"action": "prepare_offer", "item": item}
        result = asyncio.run(self.plugin.handle(event, {}))
        self.assertTrue(result["allowed"])
        self.assertEqual(result["draft"]["funpay_type"], "Прочее")

    def test_handle_prepare_offer_title_length_limit(self):
        item = self._valid_item()
        item["model"] = "A" * 200
        item["backdrop"] = "B" * 200
        item["sale_price"] = 999999
        event = {"action": "prepare_offer", "item": item}
        result = asyncio.run(self.plugin.handle(event, {}))
        self.assertTrue(result["allowed"])
        title = result["draft"]["fields"]["title"]
        self.assertLessEqual(len(title), 100)

    def test_handle_prepare_offer_float_price(self):
        item = self._valid_item()
        item["sale_price"] = 500.75
        event = {"action": "prepare_offer", "item": item}
        result = asyncio.run(self.plugin.handle(event, {}))
        self.assertTrue(result["allowed"])
        self.assertEqual(result["draft"]["fields"]["price"], 500)

    def test_handle_prepare_offer_with_errors(self):
        item = self._valid_item()
        item["sale_price"] = -10
        event = {"action": "prepare_offer", "item": item}
        result = asyncio.run(self.plugin.handle(event, {}))
        self.assertFalse(result["allowed"])
        self.assertIn("sale_price must be between 1 and 10000000", result["errors"])


if __name__ == "__main__":
    unittest.main()
