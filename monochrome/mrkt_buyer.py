#!/usr/bin/env python3
"""MRKT buyer.

Rules of the loop:
- Purchases happen only on explicit request from the panel, unless auto-buy is
  switched on in trading settings AND MRKT_BUY_ENABLED=1.
- The idle loop never touches the MRKT API: it only reads local sqlite and the
  local deals endpoint. API calls happen when there is real work to do.
- Every MRKT call goes through one rate limiter (MRKT_BUY_RPS, default 0.5 rps).
- Balance is cached (MRKT_BALANCE_TTL) instead of polled every second.
- HTTP 403 "banned" is fatal: API traffic stops and is re-probed rarely.
- 429/5xx use exponential backoff instead of a flat 3-second retry.
"""
import asyncio
import fcntl
import json
import os
import sqlite3
import time
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from curl_cffi import requests
from telethon import TelegramClient, functions, types, utils

import trading_control

ROOT = Path(os.environ.get("MRKT_BUYER_ROOT", "/root/monochrome/sessions/mrkt-buyer"))
DB = Path(os.environ.get("MARKET_DB", "/root/monochrome/cache/market_listings.sqlite3"))
FLOORS = Path(os.environ.get("MRKT_FLOORS", "/root/monochrome/sessions/tonnel-reader/mrkt_floors.json"))
TOKEN = ROOT / "mrkt_token.json"
STATUS = ROOT / "buyer_status.json"
AUTH_LOCK = ROOT / "mrkt_auth.lock"

API = "https://api.tgmrkt.io/api/v1"
MASTER_ENABLED = os.environ.get("MRKT_BUY_ENABLED", "0") == "1"
MARKET_FEE = Decimal("0.02")
UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/127.0.0.1 Mobile Safari/537.36"
BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://cdn.tgmrkt.io",
    "Referer": "https://cdn.tgmrkt.io/",
    "User-Agent": UA,
}

MIN_API_INTERVAL = 1.0 / max(float(os.environ.get("MRKT_BUY_RPS", "0.5")), 0.05)
IDLE_SLEEP = float(os.environ.get("MRKT_IDLE_SLEEP", "300"))
ACTIVE_SLEEP = float(os.environ.get("MRKT_ACTIVE_SLEEP", "1"))
BALANCE_TTL = float(os.environ.get("MRKT_BALANCE_TTL", "60"))
BANNED_PROBE_INTERVAL = float(os.environ.get("MRKT_BANNED_PROBE", "900"))
STATUS_MIN_INTERVAL = float(os.environ.get("MRKT_STATUS_INTERVAL", "30"))
BACKOFF_BASE = float(os.environ.get("MRKT_BACKOFF_BASE", "5"))
BACKOFF_MAX = float(os.environ.get("MRKT_BACKOFF_MAX", "300"))


class Banned(Exception):
    """Account-level block: stop calling the API."""


class Retryable(Exception):
    """Transient failure: back off and try later."""


def atomic(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


_status_cache = {"payload": None, "at": 0.0}


def write_status(**kw):
    """Persist status, skipping identical writes unless STATUS_MIN_INTERVAL passed."""
    old = {}
    try:
        old = json.loads(STATUS.read_text())
    except Exception:
        pass
    fresh = dict(old)
    fresh.update(kw)
    compare = {k: v for k, v in fresh.items() if k != "updated_at"}
    if compare == _status_cache["payload"] and time.time() - _status_cache["at"] < STATUS_MIN_INTERVAL:
        return
    fresh["updated_at"] = time.time()
    atomic(STATUS, fresh)
    _status_cache["payload"] = compare
    _status_cache["at"] = time.time()


def init_db():
    con = sqlite3.connect(DB, timeout=15)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS mrkt_buyer_trades(listing_id TEXT PRIMARY KEY,"
        "collection TEXT,score REAL,floor REAL,buy_price_nano INTEGER,"
        "buyer_target_nano INTEGER,seller_price_nano INTEGER,detected_at REAL,"
        "bought_at REAL,list_due_at REAL,listed_at REAL,status TEXT NOT NULL,"
        "last_error TEXT,buy_response TEXT,list_response TEXT)"
    )
    trading_control.init_requests(con)
    con.commit()
    return con


def load_token():
    try:
        return json.loads(TOKEN.read_text())["token"]
    except Exception:
        return None


async def telegram_init_data():
    client = TelegramClient(str(ROOT / "buyer"), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise Retryable("buyer Telegram session is unauthorized")
        bot = await client.get_entity("mrkt")
        app = types.InputBotAppShortName(bot_id=utils.get_input_user(bot), short_name="app")
        view = await client(functions.messages.RequestAppWebViewRequest(
            peer=await client.get_input_entity(bot), app=app,
            platform="android", write_allowed=True, fullscreen=True))
    finally:
        await client.disconnect()
    parts = urlsplit(view.url)
    data = (parse_qs(parts.fragment).get("tgWebAppData")
            or parse_qs(parts.query).get("tgWebAppData") or [None])[0]
    if not data:
        raise Retryable("MRKT tgWebAppData missing")
    return data


def headers(token):
    return {**BASE_HEADERS, "Authorization": token, "Cookie": "access_token=%s" % token}


def local_deals(cfg):
    """Candidates from our own collector API. Local only, never hits MRKT."""
    query = urlencode({"min_score": cfg["min_score"], "max_premium": cfg["max_premium_pct"], "limit": 500})
    try:
        with urlopen("http://127.0.0.1:8090/api/deals?" + query, timeout=45) as resp:
            return json.load(resp).get("items", [])
    except Exception:
        return []


def floors():
    try:
        return json.loads(FLOORS.read_text()).get("floors", {})
    except Exception:
        return {}


class Mrkt:
    """Thin MRKT client: one rate limiter, cached balance, explicit fatal states."""

    def __init__(self):
        self.session = requests.Session(impersonate="chrome124")
        self.token = load_token()
        self.last_call = 0.0
        self.balance_nano = None
        self.balance_at = 0.0
        self.calls = 0

    def _throttle(self):
        wait = MIN_API_INTERVAL - (time.time() - self.last_call)
        if wait > 0:
            time.sleep(wait)

    def _guard(self, resp):
        body = (resp.text or "")[:200]
        if resp.status_code == 403:
            raise Banned(body.strip() or "banned")
        if resp.status_code == 429 or resp.status_code >= 500:
            raise Retryable("HTTP %s" % resp.status_code)

    def refresh_token(self):
        AUTH_LOCK.touch(mode=0o600, exist_ok=True)
        with AUTH_LOCK.open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = asyncio.run(telegram_init_data())
            self._throttle()
            resp = self.session.post(API + "/auth", headers=BASE_HEADERS, json={"data": data}, timeout=20)
            self.last_call = time.time()
            self.calls += 1
            self._guard(resp)
            if resp.status_code != 200:
                raise Retryable("auth HTTP %s" % resp.status_code)
            token = resp.json().get("token")
            if not token:
                raise Retryable("MRKT auth token missing")
            self.token = token
            atomic(TOKEN, {"token": token, "created_at": time.time()})
            return token

    def call(self, method, path, **kw):
        if not self.token:
            self.refresh_token()
        for attempt in (1, 2):
            self._throttle()
            resp = self.session.request(method, API + path, headers=headers(self.token), timeout=20, **kw)
            self.last_call = time.time()
            self.calls += 1
            self._guard(resp)
            if resp.status_code == 401 and attempt == 1:
                self.refresh_token()
                continue
            return resp
        return resp

    def balance(self, force=False):
        fresh = self.balance_nano is not None and (time.time() - self.balance_at) < BALANCE_TTL
        if fresh and not force:
            return self.balance_nano
        resp = self.call("GET", "/balance")
        if resp.status_code != 200:
            raise Retryable("balance HTTP %s" % resp.status_code)
        self.balance_nano = int(resp.json().get("hard") or 0)
        self.balance_at = time.time()
        return self.balance_nano

    def gift(self, listing_id):
        resp = self.call("GET", "/gifts/gift/%s" % listing_id)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise Retryable("gift HTTP %s" % resp.status_code)
        return resp.json()


def prices(buy_nano, markup_pct):
    buy = Decimal(buy_nano) / Decimal(1_000_000_000)
    target = (buy * (Decimal(1) + Decimal(str(markup_pct)) / 100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    seller = (target / (Decimal(1) + MARKET_FEE)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    return int(target * Decimal(1_000_000_000)), int(seller * Decimal(1_000_000_000))


def seen(con, listing_id):
    row = con.execute("SELECT 1 FROM mrkt_buyer_trades WHERE listing_id=?", (listing_id,)).fetchone()
    return row is not None


def update(con, listing_id, **fields):
    keys = list(fields)
    sql = "UPDATE mrkt_buyer_trades SET " + ",".join("%s=?" % k for k in keys) + " WHERE listing_id=?"
    con.execute(sql, [fields[k] for k in keys] + [listing_id])
    con.commit()


def request_update(con, listing_id, status, error=None):
    con.execute(
        "UPDATE trade_buy_requests SET status=?,updated_at=?,last_error=? WHERE market=? AND listing_id=?",
        (status, time.time(), error, "mrkt", listing_id))
    con.commit()


def pending_requests(con):
    return [row[0] for row in con.execute(
        "SELECT listing_id FROM trade_buy_requests WHERE market='mrkt' AND status='pending' ORDER BY requested_at").fetchall()]


def due_trades(con):
    """Trades needing an API touch right now. Due time is checked in SQL, not after a fetch."""
    return con.execute(
        "SELECT listing_id,buy_price_nano,buyer_target_nano,seller_price_nano,status,list_due_at "
        "FROM mrkt_buyer_trades WHERE status='buy_uncertain' "
        "OR (status IN ('bought','listing') AND COALESCE(list_due_at,0)<=?) ORDER BY COALESCE(list_due_at,0)",
        (time.time(),)).fetchall()


def process_due(con, mrkt, rows, cfg):
    for listing_id, buy_nano, target_nano, seller_nano, status, due in rows:
        gift = mrkt.gift(listing_id)
        if status == "buy_uncertain":
            if gift and gift.get("isMine"):
                now = time.time()
                update(con, listing_id, status="bought", bought_at=now,
                       list_due_at=now + cfg["list_delay_seconds"], last_error=None)
            elif gift and gift.get("isOnSale") and not gift.get("isMine"):
                update(con, listing_id, status="failed_buy", last_error="purchase not confirmed")
            continue
        if not gift:
            update(con, listing_id, status="list_uncertain", last_error="gift unavailable before listing")
            continue
        if gift.get("isOnSale") and gift.get("isMine"):
            update(con, listing_id, status="listed", listed_at=time.time(), last_error=None)
            continue
        if not gift.get("isMine"):
            update(con, listing_id, status="sold_or_missing", last_error="gift is not owned")
            continue
        update(con, listing_id, status="listing", last_error=None)
        resp = mrkt.call("POST", "/gifts/sale", json={"ids": [listing_id], "price": seller_nano})
        body = resp.text[:4000]
        if resp.status_code != 200:
            update(con, listing_id, status="bought", list_response=body,
                   last_error="list HTTP %s" % resp.status_code)
            continue
        check = mrkt.gift(listing_id)
        listed = bool(check and check.get("isOnSale"))
        update(con, listing_id, status="listed" if listed else "listing",
               listed_at=time.time() if listed else None, list_response=body,
               last_error=None if listed else "listing awaiting confirmation")


def purchase(con, mrkt, item, floor_map, cfg):
    """Buy one listing. Returns an error string or None on success."""
    listing_id = str(item["listing_id"])
    if seen(con, listing_id):
        return "already processed"
    gift = mrkt.gift(listing_id)
    if not gift or not gift.get("isOnSale") or gift.get("isMine"):
        return "not available"
    live_nano = int(gift.get("salePrice") or 0)
    floor = Decimal(str(floor_map.get(item["collection"]) or 0))
    live_ton = Decimal(live_nano) / Decimal(1_000_000_000) if live_nano else Decimal(0)
    cap = floor * (Decimal(1) + Decimal(str(cfg["max_premium_pct"])) / 100)
    if not live_nano or floor <= 0 or live_ton > cap:
        return "price/floor limit"
    if live_nano > mrkt.balance():
        return "insufficient balance"
    target_nano, seller_nano = prices(live_nano, cfg["relist_markup_pct"])
    con.execute(
        "INSERT OR IGNORE INTO mrkt_buyer_trades(listing_id,collection,score,floor,buy_price_nano,"
        "buyer_target_nano,seller_price_nano,detected_at,status) VALUES(?,?,?,?,?,?,?,?,?)",
        (listing_id, item["collection"], item["score"], float(floor), live_nano,
         target_nano, seller_nano, time.time(), "buying"))
    con.commit()
    resp = mrkt.call("POST", "/gifts/buy", json={"ids": [listing_id], "prices": {listing_id: live_nano}})
    body = resp.text[:4000]
    if resp.status_code != 200:
        update(con, listing_id, status="failed_buy", buy_response=body,
               last_error="buy HTTP %s" % resp.status_code)
        return "HTTP %s" % resp.status_code
    now = time.time()
    update(con, listing_id, status="buy_uncertain", buy_response=body, last_error=None)
    for _ in range(3):
        owned = mrkt.gift(listing_id)
        if owned and owned.get("isMine"):
            update(con, listing_id, status="bought", bought_at=now,
                   list_due_at=now + cfg["list_delay_seconds"], last_error=None)
            mrkt.balance_nano = max((mrkt.balance_nano or 0) - live_nano, 0)
            return None
    return "ownership pending"


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(ROOT, 0o700)
    con = init_db()
    mrkt = Mrkt()
    banned_until = 0.0
    banned_reason = None
    backoff = 0.0
    while True:
        try:
            cfg = trading_control.load_settings()
            auto = MASTER_ENABLED and cfg["auto_buy"]["mrkt"]
            requests_pending = pending_requests(con)
            rows_due = due_trades(con)
            base = {
                "enabled": auto,
                "master_enabled": MASTER_ENABLED,
                "min_score": cfg["min_score"],
                "max_premium_pct": cfg["max_premium_pct"],
                "list_delay_seconds": cfg["list_delay_seconds"],
                "buyer_visible_markup_pct": cfg["relist_markup_pct"],
                "market_fee_pct": 2,
                "api_calls": mrkt.calls,
                "balance_nano": mrkt.balance_nano or 0,
                "balance_ton": (mrkt.balance_nano or 0) / 1e9,
                "pending_requests": len(requests_pending),
                "pending_trades": len(rows_due),
            }
            if time.time() < banned_until:
                write_status(state="banned", error=banned_reason,
                             retry_at=banned_until, eligible_count=0,
                             next_candidate=None, **base)
                time.sleep(min(IDLE_SLEEP, 60))
                continue

            # Nothing to do: no manual requests, no due trades, auto-buy off.
            # In that state we read nothing at all - not MRKT, not our own API.
            if not requests_pending and not rows_due and not auto:
                write_status(state="manual_only", error=None, eligible_count=0,
                             next_candidate=None, **base)
                time.sleep(IDLE_SLEEP)
                continue
            floor_map = floors()
            items = local_deals(cfg) if auto or requests_pending else []
            item_map = {(str(x.get("market", "")).lower(), str(x["listing_id"])): x for x in items}
            eligible = [x for x in items
                        if str(x.get("market", "")).lower() == "mrkt" and not seen(con, str(x["listing_id"]))]
            eligible.sort(key=lambda x: (-float(x["score"]), float(x["premium_pct"]), float(x["price"])))
            if rows_due:
                process_due(con, mrkt, rows_due, cfg)
            for listing_id in requests_pending:
                item = item_map.get(("mrkt", listing_id))
                if not item:
                    request_update(con, listing_id, "rejected", "lot stale or outside current limits")
                    continue
                request_update(con, listing_id, "processing")
                error = purchase(con, mrkt, item, floor_map, cfg)
                request_update(con, listing_id, "failed" if error else "bought", error)
            if auto:
                for item in eligible:
                    purchase(con, mrkt, item, floor_map, cfg)
            write_status(state="watching" if auto else "manual_only", error=None,
                         eligible_count=len(eligible),
                         next_candidate=eligible[0]["listing_id"] if eligible else None, **base)
            backoff = 0.0
            time.sleep(ACTIVE_SLEEP if auto else IDLE_SLEEP)

        except Banned as exc:
            banned_reason = "banned: %s" % str(exc)[:200]
            banned_until = time.time() + BANNED_PROBE_INTERVAL
            write_status(state="banned", error=banned_reason, retry_at=banned_until)
            print("MRKT returned 403 (%s); pausing API traffic for %d s"
                  % (banned_reason, int(BANNED_PROBE_INTERVAL)), flush=True)
        except Retryable as exc:
            backoff = min(max(backoff * 2, BACKOFF_BASE), BACKOFF_MAX)
            write_status(state="error", error="%s: %s" % (type(exc).__name__, str(exc)[:200]),
                         retry_in=backoff)
            time.sleep(backoff)
        except Exception as exc:
            backoff = min(max(backoff * 2, BACKOFF_BASE), BACKOFF_MAX)
            write_status(state="error", error="%s: %s" % (type(exc).__name__, str(exc)[:300]),
                         retry_in=backoff)
            time.sleep(backoff)


if __name__ == "__main__":
    main()
