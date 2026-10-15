from __future__ import annotations
import json,math,os,threading,time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen
TRANSFER_TON=0.75
RATE_MULTIPLIER=1.10
CACHE_TTL=600
CACHE_FILE=Path(__file__).with_name("cache")/"coingecko_ton_rub.json"
_lock=threading.Lock();_memory={}
def _valid(value):
 try:return 1.0<float(value)<100000.0
 except:return False
def coingecko_ton_rub(force=False):
 now=time.time()
 with _lock:
  if not force and _valid(_memory.get("rate")) and now-float(_memory.get("fetched_at",0))<CACHE_TTL:return float(_memory["rate"])
  cached={}
  try:cached=json.loads(CACHE_FILE.read_text())
  except Exception:pass
  if not force and _valid(cached.get("rate")) and now-float(cached.get("fetched_at",0))<CACHE_TTL:
   _memory.update(cached);return float(cached["rate"])
  try:
   query=urlencode({"ids":"the-open-network","vs_currencies":"rub"})
   req=Request("https://api.coingecko.com/api/v3/simple/price?"+query,headers={"Accept":"application/json","User-Agent":"TradingPanel/1.0"})
   with urlopen(req,timeout=20) as r:data=json.loads(r.read())
   rate=float(data["the-open-network"]["rub"])
   if not _valid(rate):raise ValueError("invalid CoinGecko TON/RUB rate")
   value={"rate":rate,"fetched_at":now,"source":"coingecko"};CACHE_FILE.parent.mkdir(parents=True,exist_ok=True);tmp=CACHE_FILE.with_suffix(".tmp");tmp.write_text(json.dumps(value));tmp.replace(CACHE_FILE);_memory.update(value);return rate
  except Exception:
   if _valid(cached.get("rate")):_memory.update(cached);return float(cached["rate"])
   fallback=float(os.getenv("TON_RUB_FALLBACK","130"));_memory.update({"rate":fallback,"fetched_at":now,"source":"fallback"});return fallback
def pricing_quote(source_ton,rate=None):
 source=max(0.0,float(source_ton or 0));spot=float(rate if rate is not None else coingecko_ton_rub());total=source+TRANSFER_TON;adjusted=spot*RATE_MULTIPLIER;raw=total*adjusted
 return {"source_ton":round(source,8),"transfer_ton":TRANSFER_TON,"total_ton":round(total,8),"coingecko_ton_rub":round(spot,8),"rate_multiplier":RATE_MULTIPLIER,"adjusted_ton_rub":round(adjusted,8),"raw_rub":round(raw,8),"sale_price":max(1,math.ceil(raw))}
def calculate_sale_price(source_ton,rate=None):return int(pricing_quote(source_ton,rate)["sale_price"])
