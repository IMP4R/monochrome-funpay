import unittest
from plugin import Plugin


class TestFunpayActionGuard(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()

    def test_health(self):
        result = self.plugin.health({})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["slug"], "funpay_action_guard")
        self.assertEqual(result["version"], "1.0.0")

    def test_score_create_lot_confirmed(self):
        item = {"action": "create_lot", "confirmed": True, "price": 100}
        result = self.plugin.score(item)
        self.assertEqual(result["score"], 100)
        self.assertTrue(result["allowed"])

    def test_score_create_lot_not_confirmed(self):
        item = {"action": "create_lot", "confirmed": False, "price": 100}
        result = self.plugin.score(item)
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["allowed"])

    def test_score_unknown_action(self):
        item = {"action": "unknown_action", "confirmed": True, "price": 100}
        result = self.plugin.score(item)
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["allowed"])

    def test_score_malformed_input(self):
        result = self.plugin.score("not_a_dict")
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["allowed"])

    def test_score_missing_confirmed(self):
        item = {"action": "update_lot", "price": 100}
        result = self.plugin.score(item)
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["allowed"])

    def test_score_delete_lot_confirmed(self):
        item = {"action": "delete_lot", "confirmed": True, "price": 50}
        result = self.plugin.score(item)
        self.assertEqual(result["score"], 100)
        self.assertTrue(result["allowed"])

    def test_score_send_message_confirmed(self):
        item = {"action": "send_message", "confirmed": True, "price": 0}
        result = self.plugin.score(item)
        self.assertEqual(result["score"], 100)
        self.assertTrue(result["allowed"])

    def test_score_update_lot_confirmed(self):
        item = {"action": "update_lot", "confirmed": True, "price": 75}
        result = self.plugin.score(item)
        self.assertEqual(result["score"], 100)
        self.assertTrue(result["allowed"])

    def test_score_confirmed_not_strictly_true(self):
        item = {"action": "create_lot", "confirmed": 1, "price": 100}
        result = self.plugin.score(item)
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["allowed"])

    def test_handle_returns_same_result(self):
        event = {"action": "create_lot", "confirmed": True, "price": 100}
        result = self.plugin.handle(event, {})
        self.assertEqual(result["score"], 100)
        self.assertTrue(result["allowed"])

    def test_handle_malformed_input(self):
        result = self.plugin.handle("not_a_dict", {})
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["allowed"])

    def test_handle_unknown_action(self):
        event = {"action": "unknown_action", "confirmed": True, "price": 100}
        result = self.plugin.handle(event, {})
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["allowed"])


if __name__ == "__main__":
    unittest.main()
