#!/usr/bin/env python3
import json,os,re,sqlite3,time
from pathlib import Path
ROOT=Path(os.environ.get('TONNEL_ROOT','/root/monochrome/sessions/tonnel-reader'))
SRC=ROOT/'latest_response.json';DB=Path(os.environ.get('MARKET_DB','/root/monochrome/cache/market_listings.sqlite3'));STATUS=ROOT/'collector_status.json'
def atomic(p,x):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(x,ensure_ascii=False));os.chmod(t,0o600);t.replace(p)
def clean(v):
 return re.sub(r'\s*\([^)]*%\)\s*$', '', str(v or '')).strip() or None
p=json.loads(SRC.read_text());items=p.get('payload') if p.get('status')==200 else []
if not isinstance(items,list): raise RuntimeError('Tonnel payload is not a list')
DB.parent.mkdir(parents=True,exist_ok=True);now=time.time();new=changed=0
con=sqlite3.connect(DB);con.execute('PRAGMA journal_mode=WAL');con.executescript('''
CREATE TABLE IF NOT EXISTS market_listings(market TEXT NOT NULL,listing_id TEXT NOT NULL,collection TEXT,model TEXT,backdrop TEXT,symbol TEXT,gift_number INTEGER,price REAL,currency TEXT,listed_at TEXT,first_seen REAL NOT NULL,last_seen REAL NOT NULL,raw_json TEXT NOT NULL,PRIMARY KEY(market,listing_id));
CREATE TABLE IF NOT EXISTS market_events(id INTEGER PRIMARY KEY AUTOINCREMENT,market TEXT NOT NULL,listing_id TEXT NOT NULL,event_type TEXT NOT NULL,price REAL,currency TEXT,seen_at REAL NOT NULL,raw_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS market_events_seen ON market_events(seen_at DESC);
CREATE TABLE IF NOT EXISTS collector_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,market TEXT NOT NULL,started_at REAL NOT NULL,status TEXT NOT NULL,item_count INTEGER NOT NULL,new_count INTEGER NOT NULL,changed_count INTEGER NOT NULL);
''')
for x in items:
 lid=str(x.get('gift_id'));price=float(x['price']) if x.get('price') is not None else None;cur=x.get('asset');raw=json.dumps(x,ensure_ascii=False,separators=(',',':'))
 old=con.execute('SELECT price,currency FROM market_listings WHERE market=? AND listing_id=?',('tonnel',lid)).fetchone()
 if old is None: et='new';new+=1
 elif old!=(price,cur): et='price_change';changed+=1
 else: et=None
 con.execute('''INSERT INTO market_listings(market,listing_id,collection,model,backdrop,symbol,gift_number,price,currency,listed_at,first_seen,last_seen,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(market,listing_id) DO UPDATE SET collection=excluded.collection,model=excluded.model,backdrop=excluded.backdrop,symbol=excluded.symbol,gift_number=excluded.gift_number,price=excluded.price,currency=excluded.currency,listed_at=excluded.listed_at,last_seen=excluded.last_seen,raw_json=excluded.raw_json''',('tonnel',lid,x.get('name'),clean(x.get('model')),clean(x.get('backdrop')),clean(x.get('symbol')),x.get('gift_num'),price,cur,x.get('export_at'),now,now,raw))
 if et: con.execute('INSERT INTO market_events(market,listing_id,event_type,price,currency,seen_at,raw_json) VALUES(?,?,?,?,?,?,?)',('tonnel',lid,et,price,cur,now,raw))
con.execute('INSERT INTO collector_runs(market,started_at,status,item_count,new_count,changed_count) VALUES(?,?,?,?,?,?)',('tonnel',now,'ok',len(items),new,changed));con.commit();con.close()
atomic(STATUS,{'market':'tonnel','status':'ok','last_success':now,'items':len(items),'new':new,'changed':changed,'chromium_resident':False})
print(json.dumps({'items':len(items),'new':new,'changed':changed}))
