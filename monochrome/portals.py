#!/usr/bin/env python3
"""Portals Market API client. Auth: tma initData из файла .auth (опционально —
/collections и /nfts/search работают публично)."""
import os
import random
import threading
import time
from pathlib import Path

import requests

API_BASE = "https://portal-market.com/api"
AUTH_FILE = Path(os.environ.get("PORTALS_AUTH_FILE", str(Path(__file__).parent / ".auth")))

_UA = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://portal-market.com/",
}

_cache = {"collections": None, "ts": 0.0}
_session = requests.Session()
_rate_lock = threading.Lock()
_next_read_at = 0.0
READ_RPS = 1.5  # public search limit is 2/s; keep 0.5/s in reserve


def _get(path: str, params: dict | None = None, retries: int = 4):
    global _next_read_at
    for attempt in range(retries):
        with _rate_lock:
            wait = _next_read_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _next_read_at = time.monotonic() + 1.0 / READ_RPS
        r = _session.get(f"{API_BASE}{path}", params=params,
                         headers=_headers(), timeout=20)
        if r.status_code == 429:
            delay = float(r.headers.get("Retry-After") or 2 ** attempt)
            time.sleep(delay + random.uniform(0.05, 0.2))
            continue
        if r.status_code >= 500 and attempt + 1 < retries:
            time.sleep(min(8.0, 2 ** attempt) + random.uniform(0.05, 0.2))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()


def _headers():
    h = dict(_UA)
    if AUTH_FILE.exists():
        tok = AUTH_FILE.read_text().strip()
        if tok:
            h["Authorization"] = tok
    return h


def get_collections(ttl: int = 300):
    if _cache["collections"] is not None and time.time() - _cache["ts"] < ttl:
        return _cache["collections"]
    r = _get("/collections", {"offset": 0, "limit": 150})
    cols = r.json().get("collections", [])
    _cache.update(collections=cols, ts=time.time())
    return cols


def top_by_sales(n: int = 10):
    """Топ коллекций по КОЛИЧЕСТВУ продаж за 24ч (не по валютному обороту)."""
    cols = [c for c in get_collections() if not c.get("is_premarket")]
    cols.sort(key=lambda c: int(c.get("sales_24h_count") or 0), reverse=True)
    return cols[:n]


def get_listings(collection_id: str, max_items: int = 500, page: int = 50):
    """Read listed NFTs without consuming the 0.5 req/s purchase reserve."""
    out, offset = [], 0
    page = min(50, max(1, int(page)))  # public API silently caps pages at 50
    while offset < max_items:
        limit = min(page, max_items - offset)
        r = _get("/nfts/search", {
            "offset": offset, "limit": limit, "sort_by": "price asc",
            "status": "listed", "collection_id": collection_id,
        })
        data = r.json()
        items = data.get("results", [])
        if not items:
            break
        out.extend(items)
        offset += len(items)
        total = data.get("total_count") or data.get("total")
        if (total is not None and offset >= int(total)) or len(items) < limit:
            break
    return out[:max_items]
