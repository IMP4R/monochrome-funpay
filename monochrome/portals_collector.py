#!/usr/bin/env python3
import json, os, sqlite3, time
from pathlib import Path
import portals
import trading_control
ROOT=Path('/root/monochrome'); DB=ROOT/'cache/market_listings.sqlite3'
STATUS=ROOT/'cache/portals_status.json'; FLOORS=ROOT/'cache/portals_floors.json'
TOP=['Liberty Figure','Chill Flame','Vice Cream','Snoop Dogg','Jack-in-the-Box','Fine Pen','Lunar Snake','Lol Pop','Ice Cream','Instant Ramen']
PAGE=50; MAX_PREMIUM=2.0

def atomic(p,x):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(x,ensure_ascii=False));os.chmod(t,0o600);t.replace(p)
def attrs(x):
 d={}
 for a in x.get('attributes') or []: d[str(a.get('type') or '').lower()]=a.get('value')
 return d
def ingest(items,collection):
 now=time.time();con=sqlite3.connect(DB,timeout=20);con.execute('PRAGMA journal_mode=WAL');new=changed=0
 for x in items:
  lid=str(x.get('id'));a=attrs(x);price=float(x.get('price') or 0);raw=json.dumps(x,ensure_ascii=False,separators=(',',':'))
  old=con.execute('select price from market_listings where market=? and listing_id=?',('portals',lid)).fetchone()
  et='new' if old is None else ('price_change' if float(old[0] or 0)!=price else None);new+=et=='new';changed+=et=='price_change'
  con.execute('''insert into market_listings(market,listing_id,collection,model,backdrop,symbol,gift_number,price,currency,listed_at,first_seen,last_seen,raw_json) values(?,?,?,?,?,?,?,?,?,?,?,?,?) on conflict(market,listing_id) do update set collection=excluded.collection,model=excluded.model,backdrop=excluded.backdrop,symbol=excluded.symbol,gift_number=excluded.gift_number,price=excluded.price,currency=excluded.currency,listed_at=excluded.listed_at,last_seen=excluded.last_seen,raw_json=excluded.raw_json''',('portals',lid,collection,a.get('model'),a.get('backdrop'),a.get('symbol'),x.get('external_collection_number'),price,'TON',x.get('listed_at'),now,now,raw))
  if et: con.execute('insert into market_events(market,listing_id,event_type,price,currency,seen_at,raw_json) values(?,?,?,?,?,?,?)',('portals',lid,et,price,'TON',now,raw))
 con.execute('insert into collector_runs(market,started_at,status,item_count,new_count,changed_count) values(?,?,?,?,?,?)',('portals',now,'ok',len(items),new,changed));con.commit();con.close();return new,changed
def prune(collection,scan_start,cap):
 con=sqlite3.connect(DB,timeout=20);n=con.execute('delete from market_listings where market=? and collection=? and last_seen<? and price<=?',('portals',collection,scan_start,cap)).rowcount;con.commit();con.close();return n
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
   on=bool(trading_control.load_settings()['auto_buy'].get('portals'))
  except Exception:
   on=False
  if on:return
  try:
   atomic(STATUS,{'market':'portals','status':'idle','reason':'auto_buy.portals disabled','updated_at':time.time()})
  except Exception:
   pass
  time.sleep(GATE_IDLE)
def main():
 hot_i=0;deep_i=0;request_i=0
 while True:
  try:
   gate()
   pace()
   cols=portals.get_collections();by={c.get('name'):c for c in cols};floor_map={n:float(by[n].get('floor_price') or 0) for n in TOP if n in by};atomic(FLOORS,{'updated_at':time.time(),'floors':floor_map})
   name=TOP[deep_i%len(TOP)];c=by.get(name)
   if not c: deep_i+=1;continue
   floor=floor_map.get(name,0);cap=floor*(1+MAX_PREMIUM);scan_start=time.time();offset=0;deleted=0
   while True:
    request_i+=1
    if request_i%10==0:
     hn=TOP[hot_i%len(TOP)];hot_i+=1;hc=by.get(hn)
     if hc:
      hi=portals.get_listings(hc['id'],max_items=PAGE,page=PAGE);ingest(hi,hn)
    r=portals._get('/nfts/search',{'offset':offset,'limit':PAGE,'sort_by':'price asc','status':'listed','collection_id':c['id']});data=r.json();items=data.get('results') or []
    if not items: break
    new,changed=ingest(items,name);offset+=len(items);last=max(float(x.get('price') or 0) for x in items)
    atomic(STATUS,{'market':'portals','status':'ok','collection':name,'offset':offset,'page_size':PAGE,'last_price':last,'cap':cap,'new':new,'changed':changed,'updated_at':time.time()})
    if last>cap or len(items)<PAGE: break
   deleted=prune(name,scan_start,cap);deep_i+=1
   atomic(STATUS,{'market':'portals','status':'ok','collection':name,'scan_complete':True,'items_scanned':offset,'cap':cap,'deleted':deleted,'updated_at':time.time()})
  except Exception as e:
   atomic(STATUS,{'market':'portals','status':'error','error':f'{type(e).__name__}: {str(e)[:240]}','updated_at':time.time()});time.sleep(4)
if __name__=='__main__':main()
