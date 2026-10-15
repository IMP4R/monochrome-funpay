import asyncio
import math


class Plugin:
    slug = "funpay_price_guard_small"
    title = "Funpay Price Guard Small"
    version = "1.0.0"

    def health(self, ctx):
        return {"status": "ok", "slug": self.slug, "version": self.version}

    def score(self, item):
        try:
            if not isinstance(item, dict):
                return {"allowed": False, "score": 0, "reason": "item is not a dict"}

            if len(item) == 0:
                return {"allowed": False, "score": 0, "reason": "empty item"}

            required = ["price", "min_price", "max_price", "confirmed"]
            for key in required:
                if key not in item:
                    return {"allowed": False, "score": 0, "reason": f"missing key: {key}"}

            price = item["price"]
            min_price = item["min_price"]
            max_price = item["max_price"]
            confirmed = item["confirmed"]

            if not isinstance(confirmed, bool):
                return {"allowed": False, "score": 0, "reason": "confirmed is not a boolean"}

            if not confirmed:
                return {"allowed": False, "score": 0, "reason": "not confirmed"}

            for label, value in [("price", price), ("min_price", min_price), ("max_price", max_price)]:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return {"allowed": False, "score": 0, "reason": f"{label} is not a number"}
                if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                    return {"allowed": False, "score": 0, "reason": f"{label} is not finite"}

            if not (min_price <= price <= max_price):
                return {"allowed": False, "score": 0, "reason": "price out of bounds"}

            return {"allowed": True, "score": 100, "reason": "valid"}
        except Exception as exc:
            return {"allowed": False, "score": 0, "reason": f"error: {exc}"}

    async def handle(self, event, ctx):
        return self.score(event)
