#!/usr/bin/env python3
"""Monochrome Constructor — топ-10 коллекций Portals по количеству продаж за 24ч,
палитры моделей, чистые фоны, раздельные learned-mono и BCP-32 color scores и лаборатория голосований.

Страницы:
- /        — конструктор (топ-10 + палитры + пары);
- /ab      — пары из глобального топ-10 разных цветов и разные модели;
- /swipe   — модель предлагает вероятный монохром, человек критикует;
- /top     — единая сетка всех активных коллекций по рейтингу;
- /dev/grid/{short} — монтаж топ-пар (grid.py).

Голоса: POST /api/vote -> cache/votes.jsonl (append-only). Скор в UI не
показываем, чтобы не смещать оценку; в записи он есть."""
import json
import random
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

import colors
import portals
import trading_control
import inventory_image_generator
from layers.spatial_v2 import runtime as spatial_layer
from layers.deterministic_v3 import runtime as deterministic_layer

BASE = Path(__file__).parent
CACHE_DIR = BASE / "cache"
CACHE_DIR.mkdir(exist_ok=True)
CTOR_TTL = 7200
ALGO_VERSION = "v2.29"
VOTES_FILE = CACHE_DIR / "votes.jsonl"
TEST_SCHEMA = "mono-tests-v6"
TEST_VOTES_FILE = CACHE_DIR / "test_votes_v6.jsonl"
ACTIVE_TOP_FILE = CACHE_DIR / "active_top_collections.json"
MARKET_DB = CACHE_DIR / "market_listings.sqlite3"
MRKT_FLOORS_FILE = BASE / "sessions" / "tonnel-reader" / "mrkt_floors.json"
PORTALS_FLOORS_FILE = CACHE_DIR / "portals_floors.json"
INVENTORY_FILE = BASE / "sessions" / "inventory" / "status.json"
_test_vote_lock = threading.Lock()

app = FastAPI(title="Monochrome Constructor", version="1.0")
_constructor_cache = {}
_ctor_lock = threading.Lock()

def _apply_visual_layers(d):
    spatial_layer.apply_constructor(d)
    return deterministic_layer.apply_constructor(d)

def _apply_visual_pair(d, p):
    spatial_layer.apply_pair(d, p)
    return deterministic_layer.apply_pair(d, p)


class AuthIn(BaseModel):
    token: str


class TradingSettingsIn(BaseModel):
    min_score: float = 75.0
    max_premium_pct: float = 13.0
    relist_markup_pct: float = 30.0
    list_delay_seconds: int = 100
    auto_buy: dict = {"mrkt": True, "portals": False, "tonnel": False}


class ManualBuyIn(BaseModel):
    market: str
    listing_id: str


class InventoryGenerateIn(BaseModel):
    market: str
    item_id: str


class InventoryListingTextIn(BaseModel):
    market: str
    item_id: str
    sale_price: int
    random_bg: bool = False


class TestVoteIn(BaseModel):
    session: str = ""
    task: str
    presentation_id: str
    collection: str = ""
    short: str = ""
    choice: str
    a: dict
    b: dict | None = None


class VoteIn(BaseModel):
    session: str = ""
    mode: str
    variant: str = ""
    collection: str = ""
    short: str = ""
    model_a: str = ""
    backdrop_a: str = ""
    model_b: str = ""
    backdrop_b: str = ""
    choice: str
    score_a: float | None = None
    score_b: float | None = None


def backdrop_cache_path(short_name: str, backdrop_name: str) -> Path:
    return CACHE_DIR / ("bg_" + short_name + "_" + quote(backdrop_name, safe="") + ".png")


def ctor_cache_path(short_name: str) -> Path:
    return CACHE_DIR / ("ctor_" + short_name + ".json")


def _write_active_top(shorts):
    shorts = list(dict.fromkeys(str(x).lower() for x in shorts if x))[:10]
    tmp = ACTIVE_TOP_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"shorts": shorts, "updated_at": time.time()}))
    tmp.replace(ACTIVE_TOP_FILE)


def _active_ctor_paths():
    try:
        shorts = json.loads(ACTIVE_TOP_FILE.read_text()).get("shorts") or []
        paths = [ctor_cache_path(str(sn).lower()) for sn in shorts]
        paths = [path for path in paths if path.exists()]
        if paths:
            return paths
    except Exception:
        pass
    return sorted(CACHE_DIR.glob("ctor_*.json"))


@app.on_event("startup")
def warmup():
    def _w():
        try:
            top = portals.top_by_sales(10)
            shorts = [(c.get("short_name") or "").lower() for c in top]
            shorts = list(dict.fromkeys(x for x in shorts if x))[:10]
            if shorts:
                _write_active_top(shorts)
            for sn in shorts:
                try:
                    constructor(sn)
                except Exception:
                    pass
        except Exception:
            pass
    threading.Thread(target=_w, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/ab")
def ab_page():
    return FileResponse(BASE / "static" / "ab.html")


@app.get("/swipe")
def swipe_page():
    return FileResponse(BASE / "static" / "swipe.html")


@app.get("/top")
def top_page():
    return FileResponse(BASE / "static" / "top.html")


@app.get("/deals")
def deals_page():
    return FileResponse(BASE / "static" / "deals.html")


@app.get("/inventory")
def inventory_page():
    return FileResponse(BASE / "static" / "inventory.html")


@app.get("/generated/{filename}")
def generated_asset(filename: str):
    if filename != Path(filename).name:
        raise HTTPException(404, "Файл не найден")
    path = BASE / "static" / "generated" / filename
    if not path.is_file():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sniper", include_in_schema=False)
def sniper_page():
    return FileResponse(BASE / "static" / "deals.html")


@app.get("/api/health")
def health():
    return {"ok": True, "ts": time.time()}


@app.get("/api/top")
def top(n: int = 10):
    out = []
    for c in portals.top_by_sales(n):
        out.append({
            "id": c["id"],
            "name": c["name"],
            "short_name": c.get("short_name"),
            "photo_url": c.get("photo_url"),
            "day_volume": round(float(c.get("day_volume") or 0), 1),
            "volume": round(float(c.get("volume") or 0), 1),
            "floor_price": round(float(c.get("floor_price") or 0), 2),
            "listed_count": c.get("listed_count"),
            "sales_24h_count": c.get("sales_24h_count"),
            "supply": c.get("supply"),
        })
    return {"top": out, "ts": time.time()}


@app.get("/api/constructor/{short_name}")
def constructor(short_name: str):
    short_name = short_name.lower()
    cached = _constructor_cache.get(short_name)
    if cached and time.time() - cached["ts"] < CTOR_TTL:
        return _apply_visual_layers(cached["data"])
    p = ctor_cache_path(short_name)
    if p.exists() and time.time() - p.stat().st_mtime < CTOR_TTL:
        data = _apply_visual_layers(json.loads(p.read_text()))
        _constructor_cache[short_name] = {"ts": p.stat().st_mtime, "data": data}
        return data
    with _ctor_lock:
        cached = _constructor_cache.get(short_name)
        if cached and time.time() - cached["ts"] < CTOR_TTL:
            return _apply_visual_layers(cached["data"])
        data = _apply_visual_layers(build_constructor(short_name))
        try:
            p.write_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass
        _constructor_cache[short_name] = {"ts": time.time(), "data": data}
        return data


@app.post("/api/cache/clear")
def cache_clear():
    _constructor_cache.clear()
    n = 0
    for f in CACHE_DIR.glob("ctor_*.json"):
        try:
            f.unlink()
            n += 1
        except Exception:
            pass
    return {"cleared": n}


@app.get("/api/render/backdrop/{short_name}/{backdrop_name}")
def render_backdrop(short_name: str, backdrop_name: str):
    short_name = short_name.lower()
    path = backdrop_cache_path(short_name, backdrop_name)
    if not path.exists():
        constructor(short_name)
    if not path.exists():
        raise HTTPException(404, "backdrop render unavailable")
    webp = colors.ensure_backdrop_webp(path)
    output = webp if webp and webp.exists() else path
    media_type = "image/webp" if output.suffix == ".webp" else "image/png"
    return FileResponse(output, media_type=media_type,
                        headers={"Cache-Control": "public, max-age=2592000, immutable"})


@app.get("/api/render/model/{short_name}/{model_name}")
def render_model(short_name: str, model_name: str):
    path = colors.ensure_model_webp(short_name.lower(), model_name)
    if not path or not path.exists():
        # Constructor can discover a fallback model URL and populate the cache.
        constructor(short_name.lower())
        path = colors.ensure_model_webp(short_name.lower(), model_name)
    if not path or not path.exists():
        raise HTTPException(404, "model render unavailable")
    return FileResponse(path, media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=2592000, immutable"})


@app.get("/dev/grid/{short_name}")
def dev_grid(short_name: str):
    path = CACHE_DIR / ("grid_" + short_name.lower() + ".png")
    if not path.exists():
        raise HTTPException(404, "grid not generated: run grid.py on server")
    return FileResponse(path)


@app.post("/api/settings/auth")
def set_auth(body: AuthIn):
    portals.AUTH_FILE.write_text(body.token.strip())
    return {"ok": True}


@app.get("/api/settings/auth")
def get_auth():
    ok = portals.AUTH_FILE.exists() and bool(portals.AUTH_FILE.read_text().strip())
    return {"configured": ok}


def _admin(request: Request):
    # admin key check disabled on request; access is restricted by firewall to a single IP
    return


@app.get("/api/inventory")
def inventory_data(request: Request, market: str = "", view: str = "all"):
    _admin(request)
    try:
        data = json.loads(INVENTORY_FILE.read_text())
    except Exception:
        data = {"updated_at": None, "markets": {}}
    market = market.strip().lower()
    view = view.strip().lower()
    if view not in ("all", "inventory", "listed"):
        view = "all"
    items = []
    summaries = {}
    generation = inventory_image_generator.usage_status()
    # Reuse the exact visual assets from the constructor: rendered backdrop
    # plus the transparent model layer for the same collection/model/backdrop.
    visual_map = {}
    try:
        catalog, _ = _top_grid_catalog()
        norm = lambda value: " ".join(str(value or "").strip().lower().split())
        visual_map = {(norm(x.get("collection")), norm(x.get("model")), norm(x.get("backdrop"))): x
                      for x in catalog}
    except Exception:
        norm = lambda value: " ".join(str(value or "").strip().lower().split())
    for name in ("mrkt", "portals", "tonnel"):
        info = (data.get("markets") or {}).get(name) or {}
        summaries[name] = {"updated_at": info.get("updated_at"), "error": info.get("error"),
                           "inventory_count": int(info.get("inventory_count") or 0),
                           "listed_count": int(info.get("listed_count") or 0)}
        if market and name != market:
            continue
        for item in info.get("items") or []:
            if view != "all" and item.get("status") != view:
                continue
            row = dict(item)
            visual = visual_map.get((norm(row.get("collection")), norm(row.get("model")),
                                     norm(row.get("backdrop"))))
            if visual:
                row["bg_url"] = visual.get("bg_url")
                row["model_img"] = visual.get("model_img") or row.get("image_url")
                row["backdrop_hex"] = visual.get("backdrop_hex")
            else:
                row["bg_url"] = None
                row["model_img"] = row.get("image_url")
                row["backdrop_hex"] = None
            latest = generation.get("latest", {}).get(f"{name}:{row.get('id')}")
            row["generated_url"] = latest.get("url") if latest else None
            items.append(row)
    items.sort(key=lambda x: (x.get("status") != "listed", x.get("market") or "",
                              x.get("collection") or "", int(x.get("number") or 0)))
    return {"updated_at": data.get("updated_at"), "view": view, "market": market,
            "total": len(items), "markets": summaries, "generation": generation, "items": items}


@app.get("/api/inventory/generation/status")
def inventory_generation_status(request: Request):
    _admin(request)
    return inventory_image_generator.usage_status()


@app.post("/api/inventory/listing-text")
def inventory_listing_text(body: InventoryListingTextIn, request: Request):
    _admin(request)
    market = body.market.strip().lower(); item_id = body.item_id.strip()
    if body.sale_price < 1 or body.sale_price > 10000000:
        raise HTTPException(400, "Некорректная цена")
    try:
        data = json.loads(INVENTORY_FILE.read_text())
    except Exception:
        raise HTTPException(503, "Инвентарь ещё не загружен")
    source = next((dict(x) for x in (((data.get("markets") or {}).get(market) or {}).get("items") or []) if str(x.get("id")) == item_id), None)
    if not source: raise HTTPException(404, "Подарок не найден в инвентаре")
    norm = lambda value: " ".join(str(value or "").strip().lower().split())
    catalog, _ = _top_grid_catalog()
    visual = next((x for x in catalog if norm(x.get("collection")) == norm(source.get("collection")) and norm(x.get("model")) == norm(source.get("model")) and norm(x.get("backdrop")) == norm(source.get("backdrop"))), None)
    if not visual: raise HTTPException(400, "Для этой модели пока нет слоёв конструктора")
    try:
        return inventory_image_generator.generate_listing_text(source, visual, body.sale_price, body.random_bg)
    except Exception as exc:
        raise HTTPException(502, str(exc)[:300])


@app.post("/api/inventory/generate")
def inventory_generate(body: InventoryGenerateIn, request: Request):
    _admin(request)
    market = body.market.strip().lower()
    item_id = body.item_id.strip()
    try:
        data = json.loads(INVENTORY_FILE.read_text())
    except Exception:
        raise HTTPException(503, "Инвентарь ещё не загружен")
    source = None
    for item in ((data.get("markets") or {}).get(market) or {}).get("items") or []:
        if str(item.get("id")) == item_id:
            source = dict(item)
            break
    if not source:
        raise HTTPException(404, "Подарок не найден в инвентаре")
    norm = lambda value: " ".join(str(value or "").strip().lower().split())
    catalog, _ = _top_grid_catalog()
    visual = next((x for x in catalog if norm(x.get("collection")) == norm(source.get("collection"))
                   and norm(x.get("model")) == norm(source.get("model"))
                   and norm(x.get("backdrop")) == norm(source.get("backdrop"))), None)
    if not visual:
        raise HTTPException(400, "Для этой модели пока нет слоёв конструктора")
    try:
        return inventory_image_generator.generate(source, visual)
    except Exception as exc:
        raise HTTPException(502, str(exc)[:300])


@app.get("/api/trading/status")
def trading_status(request: Request):
    _admin(request)
    settings = trading_control.load_settings()
    buyer = {}
    try:
        buyer = json.loads((BASE / "sessions" / "mrkt-buyer" / "buyer_status.json").read_text())
    except Exception:
        pass
    portals_buyer = {}
    try:
        portals_buyer = json.loads((BASE / "sessions" / "portals-buyer" / "status.json").read_text())
    except Exception:
        pass
    tonnel_buyer = {}
    try:
        tonnel_buyer = json.loads((BASE / "sessions" / "tonnel-buyer" / "status.json").read_text())
    except Exception:
        pass
    return {"settings": settings, "markets": {
        "mrkt": {"supported": True, "auto_enabled": settings["auto_buy"]["mrkt"],
                 "balance_ton": buyer.get("balance_ton"), "state": buyer.get("state", "unknown"),
                 "updated_at": buyer.get("updated_at"), "eligible_count": buyer.get("eligible_count", 0)},
        "portals": {"supported": True, "auto_enabled": settings["auto_buy"]["portals"],
                    "balance_ton": portals_buyer.get("balance_ton"), "state": portals_buyer.get("state", "unknown"),
                    "updated_at": portals_buyer.get("updated_at"), "eligible_count": portals_buyer.get("eligible_count", 0)},
        "tonnel": {"supported": True, "auto_enabled": settings["auto_buy"]["tonnel"],
                   "balance_ton": tonnel_buyer.get("balance_ton"), "state": tonnel_buyer.get("state", "unknown"),
                   "updated_at": tonnel_buyer.get("updated_at"), "eligible_count": tonnel_buyer.get("eligible_count", 0)},
    }}


@app.post("/api/trading/settings")
def trading_settings(body: TradingSettingsIn, request: Request):
    _admin(request)
    data = body.model_dump()
    return {"ok": True, "settings": trading_control.save_settings(data)}


@app.post("/api/trading/buy")
def trading_buy(body: ManualBuyIn, request: Request):
    _admin(request)
    market = body.market.strip().lower()
    if market not in ("mrkt", "portals", "tonnel"):
        raise HTTPException(501, "Ручная покупка для этого маркета не подключена")
    settings = trading_control.load_settings()
    items = live_deals(settings["min_score"], settings["max_premium_pct"], "", 500)["items"]
    item = next((x for x in items if x["market"].lower() == market and str(x["listing_id"]) == str(body.listing_id)), None)
    if not item:
        raise HTTPException(409, "Лот устарел или больше не проходит текущие лимиты")
    trading_control.queue_buy(market, body.listing_id)
    return {"ok": True, "queued": True, "listing_id": body.listing_id,
            "message": "Покупка поставлена в очередь; автовыставление останется активным"}


# ---------------- Лаборатория голосований ----------------

def _random_ctor():
    files = list(CACHE_DIR.glob("ctor_*.json"))
    random.shuffle(files)
    for f in files:
        try:
            return _apply_visual_layers(json.loads(f.read_text()))
        except Exception:
            continue
    return None


def _combo(short: str, p: dict) -> dict:
    return {
        "model": p["model"],
        "backdrop": p["backdrop"],
        "score": p["score"],
        "model_img": p.get("model_img"),
        "bg_url": ("/api/render/backdrop/" + short + "/" +
                       quote(p["backdrop"], safe="") + "?v=lossless1"),
    }


def _scored_pairs(d: dict):
    return [p for p in d.get("monochrome_pairs", []) if (p.get("score") or 0) >= 10]


def _sample_pool(pairs):
    """Колода для голосований: 60% из бакета 18-40 (пограничные — самые
    информативные для калибровки), 40% из 40+ (очевидные монохромы).
    Если какого-то бакета нет — берём другой; если обоих нет — любой >= 10."""
    mid = [p for p in pairs if 18 <= (p.get("score") or 0) < 40]
    high = [p for p in pairs if (p.get("score") or 0) >= 40]
    if mid and high:
        return mid if random.random() < 0.6 else high
    return mid or high or pairs


@app.get("/api/quiz/pair")
def quiz_pair():
    """Случайная пара для A/B: по фону (одна модель, два фона) или по модели
    (два модели, один фон). Только скор >= 10."""
    d = _random_ctor()
    if not d:
        raise HTTPException(503, "no constructor cache yet")
    short = d["collection"]["short_name"]
    pairs = _scored_pairs(d)
    if len(pairs) < 2:
        raise HTTPException(503, "not enough scored pairs")
    a = random.choice(_sample_pool(pairs))
    same_model = [p for p in pairs if p["model"] == a["model"] and p["backdrop"] != a["backdrop"]]
    same_bg = [p for p in pairs if p["backdrop"] == a["backdrop"] and p["model"] != a["model"]]
    variant = random.choice(["backdrop", "model"])
    cands = same_model if variant == "backdrop" else same_bg
    if not cands:
        variant = "model" if variant == "backdrop" else "backdrop"
        cands = same_bg if variant == "model" else same_model
    if not cands:
        variant = "any"
        cands = [p for p in pairs if p is not a]
    b = random.choice(cands)
    if random.random() < 0.5:
        a, b = b, a
    return {"collection": d["collection"]["name"], "short": short, "variant": variant,
            "algo": ALGO_VERSION, "a": _combo(short, a), "b": _combo(short, b)}


@app.get("/api/quiz/single")
def quiz_single():
    """Случайное комбо для свайпа, скор >= 10."""
    d = _random_ctor()
    if not d:
        raise HTTPException(503, "no constructor cache yet")
    short = d["collection"]["short_name"]
    pairs = _scored_pairs(d)
    if not pairs:
        raise HTTPException(503, "not enough scored pairs")
    p = random.choice(_sample_pool(pairs))
    return {"collection": d["collection"]["name"], "short": short,
            "algo": ALGO_VERSION, **_combo(short, p)}


@app.post("/api/vote")
def vote(v: VoteIn, request: Request):
    rec = v.dict()
    rec["ts"] = time.time()
    rec["ua"] = request.headers.get("user-agent", "")
    rec["algo"] = ALGO_VERSION
    try:
        with open(VOTES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        raise HTTPException(500, f"vote write failed: {e}")
    return {"ok": True}


@app.get("/api/votes/stats")
def votes_stats():
    total = 0
    by_mode, by_choice, by_variant = {}, {}, {}
    if VOTES_FILE.exists():
        for line in VOTES_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            total += 1
            for key, store in (("mode", by_mode), ("choice", by_choice), ("variant", by_variant)):
                val = rec.get(key) or "?"
                store[val] = store.get(val, 0) + 1
    return {"total": total, "by_mode": by_mode, "by_choice": by_choice, "by_variant": by_variant}


# ---------------- Изолированные тесты v1 ----------------

_TEST_METRIC_KEYS = (
    "model", "backdrop", "model_hex", "backdrop_hex", "rainbow",
    "base_score", "tone_score", "tone_tolerance_scale", "score",
    "mono_score", "mono_probability", "mono_points", "mono_model_version",
    "mono_model_threshold", "mono_features", "legacy_mono_probability",
    "legacy_mono_score", "legacy_mono_points", "legacy_mono_model_version",
    "spatial_mono_probability", "spatial_mono_score", "spatial_mono_source",
    "spatial_mono_model_version", "deterministic_mono_probability",
    "deterministic_mono_score", "deterministic_mono_source",
    "deterministic_mono_model_version", "deterministic_decision_score",
    "deterministic_mono_components", "deterministic_component_points",
    "deterministic_reason_codes", "mono_layers", "color_score", "color_points",
    "score_formula", "preference_coeff", "preference_raw_coeff",
    "preference_index", "color_family", "classification_source",
    "perceptual_hue", "perceptual_chroma", "perceptual_lstar", "bcp_hue",
    "bcp_cut", "bcp_rating", "bcp_normalized", "yougov_color",
    "yougov_percent", "yougov_normalized", "classic_score",
)


def _test_combo(d: dict, p: dict) -> dict:
    deterministic_layer.apply_pair(d, p, details=True, allow_runtime=True)
    short = d["collection"]["short_name"]
    out = {k: p.get(k) for k in _TEST_METRIC_KEYS}
    out["collection"] = d["collection"]["name"]
    out["short"] = short
    out["model_img"] = p.get("model_img")
    out["bg_url"] = ("/api/render/backdrop/" + short + "/" +
                     quote(p["backdrop"], safe="") + "?v=lossless1")
    out["item_key"] = "|".join((short, p["model"], p["backdrop"]))
    model = next((m for m in d.get("models", []) if m.get("name") == p["model"]), None)
    if model:
        out["model_palette"] = model.get("palette") or []
        out["model_neutral"] = model.get("neutral")
        out["model_center"] = model.get("center")
        out["model_achromatic"] = model.get("achromatic")
    return out


def _pair_model_info(d, p):
    return next((m for m in d.get("models", []) if m.get("name") == p.get("model")), None)


def _ensure_learned_scoring(d, p):
    """Score old cached rows lazily; new constructor rows already contain this."""
    if p.get("mono_score") is not None and p.get("color_score") is not None:
        return p
    model = _pair_model_info(d, p)
    bg_hex = p.get("backdrop_hex")
    if not model or not bg_hex:
        return p
    try:
        bg_rgb = colors._rgb_of_hex(bg_hex)
    except Exception:
        return p
    base_score = float(p.get("base_score") or colors.pair_score(
        bg_rgb, model, apply_preference=False, tone_tolerance_scale=1.0,
        bg_name=p.get("backdrop")))
    classic_score = p.get("classic_score")
    if classic_score is None and model.get("rgb"):
        classic_score = colors.similarity(bg_rgb, model["rgb"])
    mono = colors.predict_monochrome(bg_rgb, model, base_score, classic_score,
                                     p.get("backdrop"))
    preference = colors.color_preference(bg_rgb, p.get("backdrop"))
    color_score = float(preference.get("bcp_score", 50.0))
    total, mono_points, color_points = colors.combine_mono_color_scores(
        mono["score"], color_score)
    p.update({
        "base_score": round(base_score, 2), "tone_score": round(base_score, 2),
        "tone_tolerance_scale": 1.0, "mono_score": mono["score"],
        "mono_probability": mono["probability"], "mono_points": mono_points,
        "mono_model_version": mono["model_version"],
        "mono_model_threshold": mono["decision_threshold"],
        "mono_features": mono["features"], "color_score": color_score,
        "color_points": color_points, "score": total,
        "score_formula": "0.60*mono_score + 0.40*BCP_color_score",
        "preference_coeff": 1.0, "preference_raw_coeff": 1.0,
        "preference_index": preference.get("bcp_normalized", 0.5),
        "bcp_normalized": preference.get("bcp_normalized", 0.5),
        "yougov_normalized": preference.get("yougov_normalized"),
    })
    for key in ("color_family", "classification_source", "perceptual_hue",
                "perceptual_chroma", "perceptual_lstar", "bcp_hue", "bcp_cut",
                "bcp_rating", "yougov_color", "yougov_percent"):
        p[key] = preference.get(key)
    return p


_swipe_pool_cache = {"signature": None, "rows": []}
_swipe_pool_lock = threading.Lock()
_top_grid_cache = {"signature": None, "items": [], "collections": []}
_top_grid_lock = threading.Lock()


def _all_swipe_candidates():
    paths = _active_ctor_paths()
    signature = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size)
                      for path in paths)
    if _swipe_pool_cache["signature"] == signature:
        return _swipe_pool_cache["rows"]
    with _swipe_pool_lock:
        if _swipe_pool_cache["signature"] == signature:
            return _swipe_pool_cache["rows"]
        out = []
        for path in paths:
            try:
                d = json.loads(path.read_text())
            except Exception:
                continue
            for p in d.get("monochrome_pairs", []):
                _ensure_learned_scoring(d, p)
                _apply_visual_pair(d, p)
                if p.get("mono_probability") is not None:
                    out.append((d, p))
        _swipe_pool_cache.update(signature=signature, rows=out)
        return out


def _top_grid_catalog():
    rows = _all_swipe_candidates()
    signature = _swipe_pool_cache.get("signature")
    if _top_grid_cache["signature"] == signature:
        return _top_grid_cache["items"], _top_grid_cache["collections"]
    with _top_grid_lock:
        if _top_grid_cache["signature"] == signature:
            return _top_grid_cache["items"], _top_grid_cache["collections"]
        items = []
        collection_meta = {}
        for d, pair in rows:
            collection = d.get("collection") or {}
            short = str(collection.get("short_name") or "").lower()
            if not short:
                continue
            def number(value, default=0.0):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return default
            price = number(pair.get("min_price"), -1.0)
            if price <= 0:
                price = None
            score = round(number(pair.get("score")), 2)
            mono_score = round(number(pair.get("deterministic_mono_score", pair.get("mono_score"))), 3)
            color_score = round(number(pair.get("color_score")), 2)
            item = {
                "item_key": pair.get("item_key") or "|".join((short, str(pair.get("model") or ""), str(pair.get("backdrop") or ""))),
                "collection": collection.get("name") or short,
                "short": short,
                "model": pair.get("model") or "",
                "backdrop": pair.get("backdrop") or "",
                "score": score,
                "mono_score": mono_score,
                "color_score": color_score,
                "min_price": round(price, 3) if price is not None else None,
                "collection_floor_price": round(number(collection.get("floor_price")), 3),
                "sales_24h_count": int(number(collection.get("sales_24h_count"))),
                "day_volume": round(number(collection.get("day_volume")), 2),
                "model_hex": pair.get("model_hex"),
                "backdrop_hex": pair.get("backdrop_hex"),
                "model_img": pair.get("model_img") or f"/api/render/model/{quote(short, safe='')}/{quote(str(pair.get('model') or ''), safe='')}?v=lossless1",
                "bg_url": f"/api/render/backdrop/{quote(short, safe='')}/{quote(str(pair.get('backdrop') or ''), safe='')}?v=lossless1",
                "color_family": pair.get("color_family"),
                "source": pair.get("deterministic_mono_source"),
            }
            items.append(item)
            meta = collection_meta.setdefault(short, {
                "short": short,
                "name": collection.get("name") or short,
                "count": 0,
            })
            meta["count"] += 1
        items.sort(key=lambda item: (-item["score"], -item["mono_score"], item["item_key"]))
        for rank, item in enumerate(items, 1):
            item["rank"] = rank
        collections = sorted(collection_meta.values(), key=lambda item: item["name"].lower())
        _top_grid_cache.update(signature=signature, items=items, collections=collections)
        return items, collections


@app.get("/api/sniper", include_in_schema=False)
@app.get("/api/top-grid")
def top_grid(offset: int = 0, limit: int = 80, sort: str = "score",
                   collection: str = "", min_score: float = 0.0, q: str = ""):
    started = time.perf_counter()
    offset = max(0, int(offset))
    limit = max(1, min(160, int(limit)))
    items, collections = _top_grid_catalog()
    selected = items
    collection = collection.strip().lower()
    if collection:
        selected = [item for item in selected if item["short"] == collection]
    if min_score > 0:
        selected = [item for item in selected if item["score"] >= min_score]
    needle = q.strip().lower()
    if needle:
        selected = [item for item in selected if needle in (item["model"] + " " + item["backdrop"] + " " + item["collection"]).lower()]
    if sort == "mono":
        selected = sorted(selected, key=lambda item: (-item["mono_score"], -item["score"], item["item_key"]))
    elif sort == "color":
        selected = sorted(selected, key=lambda item: (-item["color_score"], -item["score"], item["item_key"]))
    elif sort == "price":
        selected = sorted(selected, key=lambda item: (item["min_price"] is None, item["min_price"] or 10**9, -item["score"]))
    else:
        sort = "score"
    total = len(selected)
    page = selected[offset:offset + limit]
    return {
        "algo": ALGO_VERSION,
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + len(page) if offset + len(page) < total else None,
        "sort": sort,
        "collections": collections,
        "items": page,
        "generated_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _norm_market_name(value):
    return " ".join(str(value or "").strip().lower().split())


def _rgb_int_hex(value, fallback="#252831"):
    try:
        return f"#{int(value) & 0xffffff:06x}"
    except (TypeError, ValueError):
        return fallback


@app.get("/api/deals")
def live_deals(min_score: float = 75.0, max_premium: float = 13.0,
               collection: str = "", limit: int = 200):
    started = time.perf_counter()
    min_score = max(75.0, min(100.0, float(min_score)))
    max_premium = max(0.0, min(200.0, float(max_premium)))
    limit = max(1, min(500, int(limit)))
    catalog, collections = _top_grid_catalog()
    pair_map = {(
        _norm_market_name(item["collection"]),
        _norm_market_name(item["model"]),
        _norm_market_name(item["backdrop"]),
    ): item for item in catalog if item.get("score", 0) >= min_score}
    try:
        floor_data = json.loads(MRKT_FLOORS_FILE.read_text())
        floors = floor_data.get("floors") or {}
        floors_updated_at = float(floor_data.get("updated_at") or 0)
    except Exception:
        floors, floors_updated_at = {}, 0.0
    if not MARKET_DB.exists():
        return {"items": [], "total": 0, "min_score": min_score,
                "max_premium": max_premium, "collections": collections,
                "floors_updated_at": floors_updated_at}
    selected_collection = _norm_market_name(collection)
    now = time.time()
    con = sqlite3.connect(MARKET_DB, timeout=5)
    con.row_factory = sqlite3.Row
    rows = con.execute("""SELECT market,listing_id,collection,model,backdrop,symbol,
        gift_number,price,currency,last_seen,raw_json FROM market_listings
        WHERE price IS NOT NULL AND ((market IN ('mrkt','portals') AND last_seen>=?)
          OR (market='tonnel' AND last_seen>=?))""", (now-7200, now-43200)).fetchall()
    con.close()
    out = []
    for row in rows:
        if selected_collection and _norm_market_name(row["collection"]) != selected_collection:
            continue
        pair = pair_map.get((_norm_market_name(row["collection"]),
                             _norm_market_name(row["model"]),
                             _norm_market_name(row["backdrop"])))
        if not pair:
            continue
        market = str(row["market"] or "").lower()
        try:
            raw = json.loads(row["raw_json"])
        except Exception:
            raw = {}
        floor = float((raw.get("floor_price") if market == "portals" else None)
                      or floors.get(row["collection"]) or 0)
        price = float(row["price"] or 0)
        if floor <= 0 or price <= 0:
            continue
        premium = (price / floor - 1.0) * 100.0
        if premium > max_premium:
            continue
        image_key = str(raw.get("modelStickerThumbnailKey") or "").lstrip("/")
        if market == "mrkt":
            image_url = ("https://cdn.tgmrkt.io/" + image_key) if image_key else pair["model_img"]
            gift_url = "https://t.me/mrkt/app?startapp=" + str(row["listing_id"]).replace("-", "")
        elif market == "portals":
            image_url = raw.get("photo_url") or pair["model_img"]
            gift_url = "https://t.me/portals_market_bot/market?startapp=gift_" + str(row["listing_id"])
        else:
            image_url = pair["model_img"]
            gift_url = "https://t.me/Tonnel_Network_bot/gifts?startapp=" + str(row["listing_id"])
        out.append({
            "listing_id": row["listing_id"], "market": market.upper(),
            "collection": row["collection"], "short": pair["short"],
            "model": row["model"], "backdrop": row["backdrop"],
            "symbol": row["symbol"], "number": row["gift_number"],
            "score": pair["score"], "mono_score": pair["mono_score"],
            "color_score": pair["color_score"], "price": round(price, 4),
            "floor": round(floor, 4), "premium_pct": round(premium, 2),
            "currency": row["currency"], "last_seen": row["last_seen"],
            "age_seconds": round(max(0.0, time.time() - row["last_seen"]), 1),
            "image_url": image_url, "gift_url": gift_url,
            "center_color": _rgb_int_hex(raw.get("backdropColorsCenterColor"), pair.get("backdrop_hex") or "#252831"),
            "edge_color": _rgb_int_hex(raw.get("backdropColorsEdgeColor"), pair.get("backdrop_hex") or "#181a20"),
        })
    out.sort(key=lambda x: (-x["score"], x["premium_pct"], x["price"], x["listing_id"]))
    out = out[:limit]
    return {"algo": ALGO_VERSION, "items": out, "total": len(out),
            "min_score": min_score, "max_premium": max_premium,
            "freshness_seconds": 7200, "floors_updated_at": floors_updated_at,
            "generated_ms": round((time.perf_counter() - started) * 1000, 2)}


def _candidate_key(d, p):
    return "|".join((d["collection"]["short_name"].lower(), p["model"], p["backdrop"]))

def _read_test_swipes(path, session=None):
    out=[]
    if not path.exists(): return out
    try: lines=path.read_text(encoding="utf-8").splitlines()
    except Exception: return out
    for line in lines:
        try: rec=json.loads(line)
        except Exception: continue
        if rec.get("task")!="swipe_monochrome" or rec.get("choice") not in ("monochrome","not_monochrome"): continue
        if session is not None and rec.get("session")!=session: continue
        if (rec.get("a") or {}).get("item_key"): out.append(rec)
    return out

def _controlled_sampling_state(session):
    labeled=set()
    for path in CACHE_DIR.glob("test_votes_v*.jsonl"):
        for rec in _read_test_swipes(path): labeled.add(rec["a"]["item_key"])
    current=_read_test_swipes(TEST_VOTES_FILE)
    family_counts={};model_counts={}
    for rec in current:
        a=rec.get("a") or {};family=a.get("color_family") or "?";mk="|".join((a.get("short") or "",a.get("model") or ""));family_counts[family]=family_counts.get(family,0)+1;model_counts[mk]=model_counts.get(mk,0)+1
    return labeled,family_counts,model_counts,_read_test_swipes(TEST_VOTES_FILE,session)

def _repeat_pick(all_rows, session_rows):
    if len(session_rows)<18: return None
    by_key={_candidate_key(d,p):(d,p) for d,p in all_rows};eligible=[r for r in session_rows[:-12] if not (r.get("a") or {}).get("hidden_repeat")]
    random.shuffle(eligible)
    for rec in eligible:
        key=rec["a"]["item_key"]
        if key in by_key:
            d,p=by_key[key];return d,p,{"hidden_repeat":True,"repeat_source_presentation_id":rec.get("presentation_id"),"repeat_distance":len(session_rows)-session_rows.index(rec)}
    return None

def _model_challenge_pick(session=""):
    all_rows=_all_swipe_candidates();labeled,family_counts,model_counts,session_rows=_controlled_sampling_state(session)
    if not all_rows:return None,None,"empty",{}
    rows=[(d,p) for d,p in all_rows if _candidate_key(d,p) not in labeled] or all_rows
    draw=random.random()
    if draw<.10:
        repeat=_repeat_pick(all_rows,session_rows)
        if repeat:return repeat[0],repeat[1],"hidden_repeat",repeat[2]
        chosen=random.choice(rows);return chosen[0],chosen[1],"deterministic_unbiased_random_bootstrap",{}
    score=lambda x:float(x[1].get("deterministic_mono_score") or 0)
    if draw<.40:
        ordered=sorted(rows,key=score,reverse=True);pool=ordered[:min(len(ordered),max(80,min(300,len(ordered)//15)))];chosen=random.choice(pool);strategy="deterministic_high_confidence"
    elif draw<.55:
        threshold=deterministic_layer.decision_score();pool=sorted(rows,key=lambda x:abs(score(x)-threshold))[:160];chosen=random.choice(pool);strategy="deterministic_boundary"
    elif draw<.75:
        chosen=random.choice(rows);strategy="deterministic_unbiased_random"
    elif draw<.90:
        pool=sorted(rows,key=lambda x:abs(float(x[1].get("deterministic_mono_probability") or 0)-float(x[1].get("spatial_mono_probability") or 0)),reverse=True)[:200];chosen=random.choice(pool or rows);strategy="deterministic_spatial_disagreement"
    else:
        buckets={}
        for row in rows:
            mk="|".join((row[0]["collection"]["short_name"],row[1]["model"]));buckets.setdefault(mk,[]).append(row)
        least=min((model_counts.get(k,0) for k in buckets),default=0);keys=[k for k in buckets if model_counts.get(k,0)==least];mk=random.choice(keys);chosen=random.choice(buckets[mk]);strategy="model_coverage"
    return chosen[0],chosen[1],strategy,{}


@app.get("/api/tests/swipe")
def tests_swipe(session: str = ""):
    """Controlled deterministic challenge, random control, and hidden repeats."""
    d,p,strategy,meta=_model_challenge_pick(session)
    if not p: raise HTTPException(503,"no test pairs")
    combo=_test_combo(d,p);combo["swipe_sampling_strategy"]=strategy;combo["swipe_model_target"]="deterministic_visual_scorecard_for_human_critique";combo.update(meta)
    score=float(p.get("deterministic_mono_score") or 0);threshold=deterministic_layer.decision_score();band="deterministic_high" if score>=threshold+12 else "deterministic_candidate" if score>=threshold else "deterministic_low"
    return {"test_schema":TEST_SCHEMA,"task":"swipe_monochrome","presentation_id":uuid.uuid4().hex,"algo":ALGO_VERSION,"collection":d["collection"]["name"],"short":d["collection"]["short_name"],"score_band":band,"sampling_strategy":strategy,"model_target":"deterministic_visual_scorecard_for_human_critique","hidden_repeat":bool(meta),"a":combo}


@app.get("/api/tests/model")
def tests_model():
    model=deterministic_layer.model_info();mg=model.get("validation",{}).get("model_group5",{}).get("quantized",{});cg=model.get("validation",{}).get("collection_holdout",{}).get("quantized",{});q=model.get("quantized",{})
    return {"model_version":model.get("model_version"),"layer_version":model.get("layer_version"),"model_type":model.get("model_type"),"training_schema":model.get("training_schema"),"training_items":model.get("training_items"),"positive_items":model.get("positive_items"),"group_cv_auc":mg.get("auc"),"group_cv_ap":mg.get("ap"),"collection_cv_auc":cg.get("auc"),"collection_cv_ap":cg.get("ap"),"decision_score":q.get("decision_score"),"integer_weights":q.get("integer_weights"),"weight_total":q.get("weight_total"),"components":model.get("component_definitions"),"visual_interpretation":model.get("visual_interpretation"),"uses_color_preference":False,"uses_collection":False,"uses_model_identity":False,"uses_absolute_hue":False,"pretrained":False,"comparison_layers":["mono-spatial-hier-v2","mono-logreg-v1"],"active_sampling":{"deterministic_high_confidence":.30,"deterministic_boundary":.15,"deterministic_unbiased_random":.20,"deterministic_spatial_disagreement":.15,"model_coverage":.10,"hidden_repeat":.10}}


COLOR_TOP_N = 10



def _color_top_candidates(limit=COLOR_TOP_N):
    """Global top-N per color family, ranked without the color prior."""
    buckets = {}
    for f in CACHE_DIR.glob("ctor_*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        for p in d.get("monochrome_pairs", []):
            family = p.get("color_family")
            if not family:
                continue
            _ensure_learned_scoring(d, p)
            _apply_visual_pair(d, p)
            rank_key = (float(p.get("mono_score") or 0),
                        float(p.get("base_score") or 0),
                        float(p.get("score") or 0))
            buckets.setdefault(family, []).append((rank_key, d, p))
    out = {}
    for family, rows in buckets.items():
        rows.sort(key=lambda x: x[0], reverse=True)
        seen, ranked = set(), []
        for rank_key, d, p in rows:
            item_key = (d["collection"]["short_name"], p["model"], p["backdrop"])
            if item_key in seen:
                continue
            seen.add(item_key)
            ranked.append((len(ranked) + 1, rank_key, d, p))
            if len(ranked) >= limit:
                break
        if ranked:
            out[family] = ranked
    return out


@app.get("/api/tests/ab")
def tests_ab():
    """Different colors/models, sampled uniformly from each family's top 10."""
    tops = _color_top_candidates()
    families = sorted(f for f, rows in tops.items() if rows)
    family_options = []
    for i, family_a in enumerate(families):
        for family_b in families[i + 1:]:
            if any(pa["model"] != pb["model"]
                   for _, _, _, pa in tops[family_a]
                   for _, _, _, pb in tops[family_b]):
                family_options.append((family_a, family_b))
    if not family_options:
        raise HTTPException(503, "not enough different color top-10 pairs")
    family_a, family_b = random.choice(family_options)
    rank_a, _, da, pa = random.choice(tops[family_a])
    choices_b = [row for row in tops[family_b] if row[3]["model"] != pa["model"]]
    rank_b, _, db, pb = random.choice(choices_b)
    if random.random() < 0.5:
        family_a, rank_a, da, pa, family_b, rank_b, db, pb = (
            family_b, rank_b, db, pb, family_a, rank_a, da, pa)
    a = _test_combo(da, pa)
    b = _test_combo(db, pb)
    a["color_top_rank"] = rank_a
    b["color_top_rank"] = rank_b
    for combo in (a, b):
        combo["color_top_n"] = COLOR_TOP_N
        combo["color_top_metric"] = "mono_score"
    gap = abs(float(pa.get("mono_score") or 0) - float(pb.get("mono_score") or 0))
    return {
        "test_schema": TEST_SCHEMA,
        "task": "ab_color_top10",
        "presentation_id": uuid.uuid4().hex,
        "algo": ALGO_VERSION,
        "collection": "top-10-by-color",
        "short": "",
        "variant": "different_color_top10",
        "selection_metric": "mono_score",
        "top_n": COLOR_TOP_N,
        "family_a": family_a,
        "family_b": family_b,
        "available_color_families": families,
        "available_top_counts": {f: len(tops[f]) for f in families},
        "score_gap_band": "close" if gap <= 8 else "medium" if gap <= 20 else "wide",
        "a": a,
        "b": b,
    }


@app.post("/api/tests/vote")
def tests_vote(v: TestVoteIn, request: Request):
    allowed = {
        "swipe_monochrome": {"monochrome", "not_monochrome", "skip"},
        "ab_color_top10": {"a", "b", "equal", "neither", "skip"},
    }
    if v.task not in allowed or v.choice not in allowed[v.task]:
        raise HTTPException(400, "invalid test task or choice")
    rec = v.dict()
    rec["test_schema"] = TEST_SCHEMA
    rec["algo"] = ALGO_VERSION
    rec["mode"] = "swipe" if v.task == "swipe_monochrome" else "ab"
    rec["ts"] = time.time()
    rec["ua"] = request.headers.get("user-agent", "")
    try:
        with _test_vote_lock:
            with open(TEST_VOTES_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        raise HTTPException(500, f"test vote write failed: {e}")
    return {"ok": True, "test_schema": TEST_SCHEMA}


@app.get("/api/tests/stats")
def tests_stats():
    total = 0
    by_task, by_choice, by_band = {}, {}, {}
    if TEST_VOTES_FILE.exists():
        for line in TEST_VOTES_FILE.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            total += 1
            for key, store in (("task", by_task), ("choice", by_choice)):
                val = rec.get(key) or "?"
                store[val] = store.get(val, 0) + 1
            a = rec.get("a") or {}
            score = float(a.get("score") or 0)
            band = "low" if score < 10 else "boundary" if score < 30 else "high"
            by_band[band] = by_band.get(band, 0) + 1
    return {"test_schema": TEST_SCHEMA, "total": total,
            "by_task": by_task, "by_choice": by_choice, "by_score_band": by_band}


# ---------------- Конструктор ----------------

def build_constructor(short_name: str) -> dict:
    col = next((c for c in portals.get_collections()
                if (c.get("short_name") or "").lower() == short_name), None)
    if not col:
        raise HTTPException(404, f"collection {short_name} not found")
    listings = portals.get_listings(col["id"], max_items=500)

    models, backdrops, sample_listing = {}, {}, {}
    for l in listings:
        try:
            price = float(l.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        attrs = {a.get("type"): a.get("value") for a in l.get("attributes", [])}
        m, b = attrs.get("model"), attrs.get("backdrop")
        if m:
            d = models.setdefault(m, {"name": m, "count": 0, "min_price": None})
            d["count"] += 1
            d["min_price"] = price if d["min_price"] is None else min(d["min_price"], price)
        if b:
            d = backdrops.setdefault(b, {"name": b, "count": 0, "min_price": None})
            d["count"] += 1
            d["min_price"] = price if d["min_price"] is None else min(d["min_price"], price)
            if b not in sample_listing:
                sample_listing[b] = l
                d["lottie"] = l.get("animation_url")

    def fill_model(m):
        rgb, url, info = colors.model_color_full(short_name, m["name"])
        m["rgb"] = rgb
        m["hex"] = colors.hexcode(rgb)
        compressed = colors.ensure_model_webp(short_name, m["name"], url)
        m["img"] = (("/api/render/model/" + short_name + "/" + quote(m["name"], safe="") +
                     "?v=lossless1") if compressed else url)
        m["palette"] = info.get("palette") or []
        m["rainbow"] = info.get("rainbow") or 0.0
        m["achromatic"] = info.get("achromatic") or False
        m["neutral"] = info.get("neutral") if info.get("neutral") is not None else 1.0
        m["center"] = info.get("center")

    def fill_backdrop(b):
        sample = sample_listing.get(b["name"]) or {}
        ckey = "b2|" + short_name + "|" + b["name"]
        cached_c = colors.cache_get(ckey)
        path = backdrop_cache_path(short_name, b["name"])
        if not path.exists():
            data = colors.fetch_lottie(b.get("lottie") or sample.get("animation_url"))
            stops = colors.lottie_bg_gradient(data) if data else None
            if stops:
                colors.save_gradient(b["name"], stops)
            png = colors.render_png_from_data(data) if data else None
            if png is None:
                # коллекция без Fragment-lottie (finepen): синтез по официальному
                # градиенту имени бэкдропа; в крайнем случае плоская заливка
                png = colors.synthetic_bg_png(b["name"], rgb_hint=None)
            if png:
                try:
                    path.write_bytes(png)
                except Exception:
                    pass
        rgb = tuple(cached_c["rgb"]) if cached_c and cached_c.get("rgb") else None
        if rgb is None and path.exists():
            try:
                rgb = colors.center_average(path.read_bytes())
            except Exception:
                rgb = None
        if rgb is None:
            data = colors.fetch_lottie(b.get("lottie") or sample.get("animation_url"))
            rgb = colors.lottie_bg_color_from_data(data)
        if rgb is None:
            num = sample.get("external_collection_number")
            rgb = colors.tme_og_image_dominant(short_name, int(num)) if num else None
        if rgb and cached_c is None:
            colors.cache_set(ckey, {"rgb": list(rgb)})
        b["rgb"] = rgb
        b["hex"] = colors.hexcode(rgb)
        if path.exists():
            colors.ensure_backdrop_webp(path)
        b["img"] = ("/api/render/backdrop/" + short_name + "/" + quote(b["name"], safe="") +
                    "?v=lossless1" if path.exists() else sample.get("photo_url"))

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(fill_model, models.values()))
        list(ex.map(fill_backdrop, backdrops.values()))

    # Common-цвета коллекции по центральным ячейкам (кожа и т.п.): common=True
    common_colors = colors.collection_common(list(models.values()))

    pairs = []
    for m in models.values():
        if not m.get("palette") and not m.get("rgb"):
            continue
        for b in backdrops.values():
            if not b.get("rgb"):
                continue
            prices = [x for x in (m["min_price"], b["min_price"]) if x is not None]
            preference = colors.color_preference(b["rgb"], b["name"])
            base_score = colors.pair_score(
                b["rgb"], m, apply_preference=False, tone_tolerance_scale=1.0,
                bg_name=b["name"])
            classic_score = colors.similarity(b["rgb"], m["rgb"]) if m.get("rgb") else None
            mono = colors.predict_monochrome(
                b["rgb"], m, base_score, classic_score, b["name"])
            color_score = float(preference.get("bcp_score", 50.0))
            total_score, mono_points, color_points = colors.combine_mono_color_scores(
                mono["score"], color_score)
            pairs.append({
                "model": m["name"],
                "backdrop": b["name"],
                "model_hex": m["hex"],
                "backdrop_hex": b["hex"],
                "model_img": m.get("img"),
                "rainbow": m.get("rainbow"),
                "base_score": base_score,
                "tone_score": base_score,
                "tone_tolerance_scale": 1.0,
                "mono_score": mono["score"],
                "mono_probability": mono["probability"],
                "mono_points": mono_points,
                "mono_model_version": mono["model_version"],
                "mono_model_threshold": mono["decision_threshold"],
                "mono_features": mono["features"],
                "color_score": color_score,
                "color_points": color_points,
                "score": total_score,
                "score_formula": "0.60*mono_score + 0.40*BCP_color_score",
                "preference_coeff": 1.0,
                "preference_raw_coeff": 1.0,
                "preference_index": preference.get("bcp_normalized", 0.5),
                "bcp_normalized": preference.get("bcp_normalized", 0.5),
                "color_family": preference.get("color_family"),
                "classification_source": preference.get("classification_source"),
                "perceptual_hue": preference.get("perceptual_hue"),
                "perceptual_chroma": preference.get("perceptual_chroma"),
                "perceptual_lstar": preference.get("perceptual_lstar"),
                "bcp_hue": preference.get("bcp_hue"),
                "bcp_cut": preference.get("bcp_cut"),
                "bcp_rating": preference.get("bcp_rating"),
                "yougov_color": preference.get("yougov_color"),
                "yougov_percent": preference.get("yougov_percent"),
                "yougov_normalized": preference.get("yougov_normalized"),
                "classic_score": classic_score,
                "bg_hue": round(colors._hue_of(b["rgb"])[0]),
                "min_price": min(prices) if prices else None,
            })
    pairs.sort(key=lambda p: -p["score"])

    return {
        "algo": ALGO_VERSION,
        "collection": {
            "id": col["id"],
            "name": col["name"],
            "short_name": col.get("short_name"),
            "day_volume": round(float(col.get("day_volume") or 0), 1),
            "sales_24h_count": col.get("sales_24h_count"),
            "floor_price": round(float(col.get("floor_price") or 0), 2),
            "listed_count": col.get("listed_count"),
        },
        "scanned_listings": len(listings),
        "common_colors": common_colors,
        "models": sorted(models.values(), key=lambda x: -x["count"]),
        "backdrops": sorted(backdrops.values(), key=lambda x: -x["count"]),
        "monochrome_pairs": pairs,
        "scoring_method": {
            "name": "Learned visual mono + BCP-32 color, additive",
            "formula": "0.60*mono_score + 0.40*BCP_color_score",
            "mono_weight": colors.MONO_TOTAL_WEIGHT,
            "color_weight": colors.COLOR_TOTAL_WEIGHT,
            "color_source": "BCP-32 only",
            "yougov_weight": 0.0,
            "mono_model": (colors.load_mono_model() or {}).get("model_version", "base-fallback"),
            "mono_training_items": (colors.load_mono_model() or {}).get("training_items"),
            "mono_cv_auc": (colors.load_mono_model() or {}).get("cv_auc"),
            "classifier": "75 Telegram definitions + CIELCh fallback",
            "model_palette": "3 separate CAM02-UCS colors, actual image representatives",
            "perceptual_distance": "Euclidean CAM02-UCS, single conversion",
            "preference_inside_mono": False,
            "tone_tolerance_scale": 1.0,
        },
        "ts": time.time(),
    }
