import json, os, secrets, sqlite3, threading, time
from pathlib import Path

BASE = Path(__file__).parent
CONFIG = BASE / "sessions" / "trading_settings.json"
ADMIN_TOKEN = BASE / "sessions" / "admin_api_token"
DB = BASE / "cache" / "market_listings.sqlite3"
LOCK = threading.Lock()
DEFAULTS = {
    "min_score": 75.0,
    "max_premium_pct": 13.0,
    "relist_markup_pct": 30.0,
    "list_delay_seconds": 100,
    "auto_buy": {"mrkt": True, "portals": False, "tonnel": False},
}

def admin_token():
    ADMIN_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    if not ADMIN_TOKEN.exists() or not ADMIN_TOKEN.read_text().strip():
        tmp = ADMIN_TOKEN.with_suffix(".tmp")
        tmp.write_text(secrets.token_urlsafe(32))
        os.chmod(tmp, 0o600)
        tmp.replace(ADMIN_TOKEN)
    return ADMIN_TOKEN.read_text().strip()

def authorized(value):
    value = str(value or "")
    if value.lower().startswith("bearer "):
        value = value[7:]
    return secrets.compare_digest(value.strip(), admin_token())

def load_settings():
    data = {}
    try:
        data = json.loads(CONFIG.read_text())
    except Exception:
        pass
    out = dict(DEFAULTS)
    out["auto_buy"] = dict(DEFAULTS["auto_buy"])
    out["auto_buy"].update(data.get("auto_buy") or {})
    for key in ("min_score", "max_premium_pct", "relist_markup_pct", "list_delay_seconds"):
        if key in data:
            out[key] = data[key]
    out["min_score"] = max(75.0, min(100.0, float(out["min_score"])))
    out["max_premium_pct"] = max(0.0, min(200.0, float(out["max_premium_pct"])))
    out["relist_markup_pct"] = max(0.0, min(500.0, float(out["relist_markup_pct"])))
    out["list_delay_seconds"] = max(0, min(86400, int(out["list_delay_seconds"])))
    out["auto_buy"] = {k: bool(out["auto_buy"].get(k, False)) for k in ("mrkt", "portals", "tonnel")}
    return out

def save_settings(data):
    current = load_settings()
    for key in ("min_score", "max_premium_pct", "relist_markup_pct", "list_delay_seconds", "auto_buy"):
        if key in data:
            current[key] = data[key]
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(CONFIG)
    return load_settings()

def init_requests(con=None):
    own = con is None
    con = con or sqlite3.connect(DB, timeout=15)
    con.execute("""CREATE TABLE IF NOT EXISTS trade_buy_requests(
      market TEXT NOT NULL, listing_id TEXT NOT NULL, requested_at REAL NOT NULL,
      status TEXT NOT NULL, updated_at REAL NOT NULL, last_error TEXT,
      PRIMARY KEY(market, listing_id))""")
    con.commit()
    if own:
        con.close()

def queue_buy(market, listing_id):
    con = sqlite3.connect(DB, timeout=15)
    init_requests(con)
    now = time.time()
    con.execute("""INSERT INTO trade_buy_requests(market,listing_id,requested_at,status,updated_at,last_error)
      VALUES(?,?,?,?,?,NULL) ON CONFLICT(market,listing_id) DO UPDATE SET
      requested_at=excluded.requested_at,status='pending',updated_at=excluded.updated_at,last_error=NULL""",
      (market.lower(), str(listing_id), now, "pending", now))
    con.commit(); con.close()
