#!/usr/bin/env python3
"""Монтажи лайкнутых/нелайкнутых свайп-карточек (score >= 40) для разбора
калибровки: /dev/grid/liked40 и /dev/grid/noped40.
Запуск на сервере: venv/bin/python votes_report.py"""
import io
import json
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw

BASE = Path(__file__).parent
CACHE = BASE / "cache"
VOTES = CACHE / "votes.jsonl"
COLS, CELL = 4, 256


def model_png(short, name):
    slug = "".join(ch for ch in name.strip().lower() if ch.isalnum())
    p = CACHE / ("mdl_" + short + "_" + slug + ".png")
    if p.exists():
        return p.read_bytes()
    url = ("https://storage.portal-market.com/portals-market/gifts/"
           + short + "/models/png/" + slug + ".png")
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            p.write_bytes(r.content)
            return r.content
    except Exception:
        pass
    return None


def bg_png(short, backdrop):
    p = CACHE / ("bg_" + short + "_" + quote(backdrop, safe="") + ".png")
    return p.read_bytes() if p.exists() else None


def compose(short, model, backdrop):
    bg = bg_png(short, backdrop)
    if not bg:
        return None
    base = Image.open(io.BytesIO(bg)).convert("RGB").resize((CELL, CELL))
    mp = model_png(short, model)
    if mp:
        m = Image.open(io.BytesIO(mp)).convert("RGBA")
        m.thumbnail((int(CELL * 0.82), int(CELL * 0.82)))
        base.paste(m, ((CELL - m.width) // 2, (CELL - m.height) // 2), m)
    return base


def grid(items, out):
    if not items:
        print("пусто:", out)
        return
    rows = (len(items) + COLS - 1) // COLS
    img = Image.new("RGB", (COLS * CELL, rows * CELL), (14, 17, 22))
    dr = ImageDraw.Draw(img)
    for i, it in enumerate(items):
        x, y = (i % COLS) * CELL, (i // COLS) * CELL
        c = compose(it["short"], it["model_a"], it["backdrop_a"])
        if c:
            img.paste(c, (x, y))
        dr.text((x + 6, y + CELL - 20),
                "{} x {}  {}".format(it["model_a"], it["backdrop_a"], it.get("score_a")),
                fill=(235, 235, 235))
    img.save(out)
    print("saved", out, len(items), "cards", img.size)


def main():
    votes = [json.loads(l) for l in VOTES.read_text(encoding="utf-8").splitlines() if l.strip()]
    sw = [v for v in votes if v.get("mode") == "swipe" and v.get("choice") in ("like", "nope")
          and (v.get("score_a") or 0) >= 40]
    seen = {}
    for v in sw:  # дедуп по карточке, последний голос побеждает
        seen[(v.get("short"), v.get("model_a"), v.get("backdrop_a"), v["choice"])] = v
    sw = list(seen.values())
    liked = [v for v in sw if v["choice"] == "like"]
    noped = [v for v in sw if v["choice"] == "nope"]
    print("swipe 40+:", len(sw), "| liked:", len(liked), "| noped:", len(noped))
    grid(liked, CACHE / "grid_liked40.png")
    grid(noped, CACHE / "grid_noped40.png")


main()
