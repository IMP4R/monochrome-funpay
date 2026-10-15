#!/usr/bin/env python3
"""Portals initData for PARSING only, issued by the reader account.
Refuses to run under the main trading account."""
import asyncio, json, os, time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from telethon import TelegramClient, functions, types, utils

BASE = Path("/root/monochrome")
SESSION = Path(os.environ.get("PORTALS_READER_SESSION", str(BASE / "sessions/tonnel-reader/reader")))
OUT = Path(os.environ.get("PORTALS_AUTH_FILE", str(BASE / ".auth_reader")))
META = BASE / "sessions/portals_auth_reader.json"
FORBIDDEN_IDS = {0}


async def main():
    c = TelegramClient(str(SESSION), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await c.connect()
    try:
        if not await c.is_user_authorized():
            raise RuntimeError("reader Telegram session is unauthorized")
        me = await c.get_me()
        if me.id in FORBIDDEN_IDS:
            raise RuntimeError("refusing to use main trading account %s for parsing" % me.id)
        bot = await c.get_entity("portals_market_bot")
        peer = await c.get_input_entity(bot)
        app = types.InputBotAppShortName(bot_id=utils.get_input_user(bot), short_name="market")
        view = await c(functions.messages.RequestAppWebViewRequest(
            peer=peer, app=app, platform="android", write_allowed=True, fullscreen=True))
    finally:
        await c.disconnect()
    u = urlsplit(view.url)
    data = (parse_qs(u.fragment).get("tgWebAppData") or parse_qs(u.query).get("tgWebAppData") or [None])[0]
    if not data:
        raise RuntimeError("Portals tgWebAppData missing")
    tmp = OUT.with_suffix(".tmp"); tmp.write_text("tma " + data)
    os.chmod(tmp, 0o600); tmp.replace(OUT)
    mt = META.with_suffix(".tmp")
    mt.write_text(json.dumps({"created_at": time.time(), "telegram_id": me.id, "phone": me.phone}, indent=2))
    os.chmod(mt, 0o600); mt.replace(META)
    print(json.dumps({"ok": True, "telegram_id": me.id, "phone": me.phone, "auth_length": len(data)}))

asyncio.run(main())
