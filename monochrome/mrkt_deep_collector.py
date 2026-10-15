#!/usr/bin/env python3
import json, os, sqlite3, time
from pathlib import Path
from curl_cffi import requests
import mrkt_collector as m
import trading_control
ROOT=Path('/root/monochrome'); DB=ROOT/'cache/market_listings.sqlite3'; STATUS=m.ROOT/'mrkt_deep_status.json'
RPS=float(os.environ.get('MRKT_DEEP_RPS','2.0')); INTERVAL=1/RPS

def atomic(p,x):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(x,ensure_ascii=False));os.chmod(t,0o600);t.replace(p)
def prune(collection,started,cap):
 con=sqlite3.connect(DB,timeout=20);n=con.execute('delete from market_listings where market=? and collection=? and last_seen<? and price<=?',('mrkt',collection,started,cap)).rowcount;con.commit();con.close();return n
PASS_SLEEP=float(os.environ.get('COLLECTOR_PASS_SLEEP','300'))
GATE_IDLE=float(os.environ.get('COLLECTOR_GATE_SLEEP','300'))
_last_pass=[0.0]
def pace():
 wait=PASS_SLEEP-(time.time()-_last_pass[0])
 if _last_pass[0] and wait>0:time.sleep(wait)
 _last_pass[0]=time.time()
def gate():
 while True:
  try:
   on=bool(trading_control.load_settings()['auto_buy'].get('mrkt'))
  except Exception:
   on=False
  if on:return
  try:
   atomic(STATUS,{'market':'mrkt','status':'idle','reason':'auto_buy.mrkt disabled','updated_at':time.time()})
  except Exception:
   pass
  time.sleep(GATE_IDLE)
def main():
 gate()
 s=requests.Session(impersonate='chrome124');token=m.load_token() or m.refresh_token(s)
 while True:
  gate()
  pace()
  try:
   fs=json.loads(m.FLOORS.read_text()).get('floors',{})
  except Exception: fs={}
  for collection in m.TOP_COLLECTIONS:
   floor=float(fs.get(collection) or 0);cap=floor*3;started=time.time();cursor='';pages=items_seen=0
   try:
    while True:
     payload=dict(m.PAYLOAD);payload.update(collectionNames=[collection],ordering='Price',lowToHigh=True,cursor=cursor,maxPrice=int(cap*1e9) if cap else None)
     t0=time.monotonic();r=s.post(m.API+'/gifts/saling',headers=m.headers(token),json=payload,timeout=20)
     if r.status_code==401: token=m.refresh_token(s);continue
     if r.status_code==429: time.sleep(float(r.headers.get('retry-after') or 5));continue
     r.raise_for_status();data=r.json();items=data.get('gifts') or [];m.ingest(items);pages+=1;items_seen+=len(items)
     last=max((float(x.get('salePrice') or 0)/1e9 for x in items),default=0);cursor=data.get('cursor') or ''
     atomic(STATUS,{'market':'mrkt','status':'ok','collection':collection,'pages':pages,'items':items_seen,'last_price':last,'cap':cap,'cursor':bool(cursor),'updated_at':time.time()})
     elapsed=time.monotonic()-t0
     if elapsed<INTERVAL:time.sleep(INTERVAL-elapsed)
     if not items or not cursor or (cap and last>cap):break
    deleted=prune(collection,started,cap) if cap else 0
    atomic(STATUS,{'market':'mrkt','status':'ok','collection':collection,'scan_complete':True,'pages':pages,'items':items_seen,'deleted':deleted,'updated_at':time.time()})
   except Exception as e:
    atomic(STATUS,{'market':'mrkt','status':'error','collection':collection,'error':f'{type(e).__name__}: {str(e)[:240]}','updated_at':time.time()});time.sleep(3)
if __name__=='__main__':main()
