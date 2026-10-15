import asyncio
import copy


class Plugin:
    slug = "funpay_action_guard"
    title = "Funpay Action Guard"
    version = "1.0.0"

    _ALLOWED_ACTIONS = {"create_lot", "update_lot", "delete_lot", "send_message"}

    def health(self, ctx):
        return {"status": "ok", "slug": self.slug, "version": self.version}

    def score(self, item):
        try:
            if not isinstance(item, dict):
                return {"score": 0, "allowed": False}

            action = item.get("action")
            confirmed = item.get("confirmed")

            if action not in self._ALLOWED_ACTIONS:
                return {"score": 0, "allowed": False}

            if confirmed is True:
                return {"score": 100, "allowed": True}

            return {"score": 0, "allowed": False}
        except Exception:
            return {"score": 0, "allowed": False}

    async def handle(self, event, ctx):
        try:
            if not isinstance(event, dict):
                return {"score": 0, "allowed": False}

            item = copy.deepcopy(event)
            result = self.score(item)
            return result
        except Exception:
            return {"score": 0, "allowed": False}
