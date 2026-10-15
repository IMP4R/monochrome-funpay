import math
import asyncio


class Plugin:
    slug = "monochrome_funpay_offer"
    title = "Monochrome FunPay Offer"
    version = "1.0.0"

    def health(self, ctx):
        return {"status": "ok", "slug": self.slug, "version": self.version}

    def score(self, item):
        errors = []

        if not isinstance(item, dict):
            return {"allowed": False, "score": 0.0, "errors": ["item must be a dict"]}

        required_str_fields = ["collection", "model", "backdrop", "market", "item_id", "listing_text", "generated_image_url"]
        for field in required_str_fields:
            val = item.get(field)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                errors.append(f"{field} must be a non-empty string")

        sale_price = item.get("sale_price")
        if isinstance(sale_price, bool) or not isinstance(sale_price, (int, float)):
            errors.append("sale_price must be a number")
        elif isinstance(sale_price, float) and (math.isnan(sale_price) or math.isinf(sale_price)):
            errors.append("sale_price must be a finite number")
        elif not (1 <= sale_price <= 10000000):
            errors.append("sale_price must be between 1 and 10000000")

        confirmed = item.get("confirmed")
        if confirmed is not True:
            errors.append("confirmed must be strictly True")

        allowed = len(errors) == 0
        return {"allowed": allowed, "score": 1.0 if allowed else 0.0, "errors": errors}

    async def handle(self, event, ctx):
        try:
            if not isinstance(event, dict):
                return {"allowed": False, "errors": ["event must be a dict"]}

            action = event.get("action")
            if action not in ("prepare_offer", "publish_offer"):
                return {"allowed": False, "errors": [f"unknown action: {action}"]}

            item = event.get("item")
            if not isinstance(item, dict):
                return {"allowed": False, "errors": ["item must be a dict"]}

            if action == "prepare_offer":
                # For prepare_offer, confirmed is not required
                # Validate only the fields needed for draft generation
                required_str_fields = ["collection", "model", "backdrop", "market", "item_id", "listing_text", "generated_image_url"]
                errors = []
                for field in required_str_fields:
                    val = item.get(field)
                    if val is None or (isinstance(val, str) and val.strip() == ""):
                        errors.append(f"{field} must be a non-empty string")

                sale_price = item.get("sale_price")
                if isinstance(sale_price, bool) or not isinstance(sale_price, (int, float)):
                    errors.append("sale_price must be a number")
                elif isinstance(sale_price, float) and (math.isnan(sale_price) or math.isinf(sale_price)):
                    errors.append("sale_price must be a finite number")
                elif not (1 <= sale_price <= 10000000):
                    errors.append("sale_price must be between 1 and 10000000")

                if errors:
                    return {"allowed": False, "errors": errors}

            elif action == "publish_offer":
                # For publish_offer, confirmed must be strictly True
                if item.get("confirmed") is not True:
                    return {"allowed": False, "errors": ["confirmed must be strictly True for publish_offer"]}

                # Also validate the item fields
                score_result = self.score(item)
                if not score_result["allowed"]:
                    return {"allowed": False, "errors": score_result["errors"]}

            # Build draft
            model = item.get("model", "")
            backdrop = item.get("backdrop", "")
            sale_price = item.get("sale_price", 0)
            market = item.get("market", "")
            item_id = item.get("item_id", "")
            listing_text = item.get("listing_text", "")
            generated_image_url = item.get("generated_image_url", "")
            funpay_type = item.get("funpay_type", "Прочее")

            title_ru = f"{model} ({backdrop}) — {sale_price}₽"
            if len(title_ru) > 100:
                title_ru = title_ru[:100]

            draft = {
                "node_id": 3064,
                "funpay_type": funpay_type,
                "fields": {
                    "type2": "Продажа",
                    "title": title_ru,
                    "description_ru": listing_text,
                    "description_en": "",
                    "price": int(sale_price),
                    "active": False,
                    "deactivate_after_sale": True,
                    "source": f"{market}/{item_id}",
                    "preview_image_url": generated_image_url,
                },
            }

            return {"allowed": True, "draft": draft}

        except Exception as e:
            return {"allowed": False, "errors": [f"unexpected error: {str(e)}"]}
