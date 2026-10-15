#!/usr/bin/env python3
import asyncio,fcntl,json,os,sqlite3,time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from curl_cffi import requests
from telethon import TelegramClient,functions,types,utils
import trading_control
ROOT=Path(os.environ.get('MARKET_SESSION_ROOT','/root/monochrome/sessions/tonnel-reader'))
DB=Path(os.environ.get('MARKET_DB','/root/monochrome/cache/market_listings.sqlite3'))
TOKEN=ROOT/'mrkt_token.json';STATUS=ROOT/'mrkt_status.json';LOCK=ROOT/'mrkt_auth.lock';FLOORS=ROOT/'mrkt_floors.json'
FORBIDDEN_TG_IDS={0}
API='https://api.tgmrkt.io/api/v1';TARGET_RPS=float(os.environ.get('MRKT_READ_RPS','4.0'))
UA='Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/127.0.0.1 Mobile Safari/537.36'
BASE_HEADERS={'Accept':'application/json, text/plain, */*','Origin':'https://cdn.tgmrkt.io','Referer':'https://cdn.tgmrkt.io/','User-Agent':UA}
PAYLOAD={'collectionNames':[],'modelNames':[],'backdropNames':[],'symbolNames':[],'lowToHigh':False,'maxPrice':None,'minPrice':None,'mintable':None,'number':None,'count':20,'cursor':'','query':None,'promotedFirst':False}
TOP_COLLECTIONS=['Liberty Figure','Chill Flame','Vice Cream','Snoop Dogg','Jack-in-the-Box','Fine Pen','Lunar Snake','Lol Pop','Ice Cream','Instant Ramen']
def atomic(p,x):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(x,ensure_ascii=False));os.chmod(t,0o600);t.replace(p)
def load_token():
 try:return json.loads(TOKEN.read_text()).get('token')
 except:return None
async def telegram_init_data():
 c=TelegramClient(str(ROOT/'reader'),int(os.environ['TG_API_ID']),os.environ['TG_API_HASH']);await c.connect()
 if not await c.is_user_authorized():raise RuntimeError('reader Telegram session is unauthorized')
 me=await c.get_me()
 if me.id in FORBIDDEN_TG_IDS:
  await c.disconnect();raise RuntimeError('refusing to parse with main trading account %s' % me.id)
 bot=await c.get_entity('mrkt');peer=await c.get_input_entity(bot);app=types.InputBotAppShortName(bot_id=utils.get_input_user(bot),short_name='app')
 view=await c(functions.messages.RequestAppWebViewRequest(peer=peer,app=app,platform='android',write_allowed=False,fullscreen=True));await c.disconnect()
 u=urlsplit(view.url);d=(parse_qs(u.fragment).get('tgWebAppData') or parse_qs(u.query).get('tgWebAppData') or [None])[0]
 if not d:raise RuntimeError('MRKT tgWebAppData missing')
 return d
def refresh_token(session):
 LOCK.touch(mode=0o600,exist_ok=True)
 with LOCK.open('r+') as f:
  fcntl.flock(f,fcntl.LOCK_EX)
  init=asyncio.run(telegram_init_data())
  r=session.post(API+'/auth',headers=BASE_HEADERS,json={'data':init},timeout=20)
  if r.status_code!=200:raise RuntimeError(f'MRKT auth HTTP {r.status_code}')
  token=r.json().get('token')
  if not token:raise RuntimeError('MRKT auth token missing')
  atomic(TOKEN,{'token':token,'created_at':time.time()});return token
def headers(token):return {**BASE_HEADERS,'Authorization':token,'Cookie':f'access_token={token}'}
def init_db(con):
 con.execute('PRAGMA journal_mode=WAL');con.executescript('''
CREATE TABLE IF NOT EXISTS market_listings(market TEXT NOT NULL,listing_id TEXT NOT NULL,collection TEXT,model TEXT,backdrop TEXT,symbol TEXT,gift_number INTEGER,price REAL,currency TEXT,listed_at TEXT,first_seen REAL NOT NULL,last_seen REAL NOT NULL,raw_json TEXT NOT NULL,PRIMARY KEY(market,listing_id));
CREATE TABLE IF NOT EXISTS market_events(id INTEGER PRIMARY KEY AUTOINCREMENT,market TEXT NOT NULL,listing_id TEXT NOT NULL,event_type TEXT NOT NULL,price REAL,currency TEXT,seen_at REAL NOT NULL,raw_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS market_events_seen ON market_events(seen_at DESC);
CREATE TABLE IF NOT EXISTS collector_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,market TEXT NOT NULL,started_at REAL NOT NULL,status TEXT NOT NULL,item_count INTEGER NOT NULL,new_count INTEGER NOT NULL,changed_count INTEGER NOT NULL);
''')
def ingest(items):
 now=time.time();new=changed=0;DB.parent.mkdir(parents=True,exist_ok=True);con=sqlite3.connect(DB,timeout=15);init_db(con)
 for x in items:
  lid=str(x.get('id') or x.get('giftIdString') or x.get('giftId'));nano=x.get('salePrice');price=(float(nano)/1e9) if nano is not None else None;raw=json.dumps(x,ensure_ascii=False,separators=(',',':'))
  old=con.execute('SELECT price,currency FROM market_listings WHERE market=? AND listing_id=?',('mrkt',lid)).fetchone()
  et='new' if old is None else ('price_change' if old!=(price,'TON') else None)
  new+=et=='new';changed+=et=='price_change'
  con.execute('''INSERT INTO market_listings(market,listing_id,collection,model,backdrop,symbol,gift_number,price,currency,listed_at,first_seen,last_seen,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(market,listing_id) DO UPDATE SET collection=excluded.collection,model=excluded.model,backdrop=excluded.backdrop,symbol=excluded.symbol,gift_number=excluded.gift_number,price=excluded.price,currency=excluded.currency,listed_at=excluded.listed_at,last_seen=excluded.last_seen,raw_json=excluded.raw_json''',('mrkt',lid,x.get('collectionName'),x.get('modelName'),x.get('backdropName'),x.get('symbolName'),x.get('number'),price,'TON',x.get('exportDate') or x.get('receivedDate'),now,now,raw))
  if et:con.execute('INSERT INTO market_events(market,listing_id,event_type,price,currency,seen_at,raw_json) VALUES(?,?,?,?,?,?,?)',('mrkt',lid,et,price,'TON',now,raw))
 con.execute('INSERT INTO collector_runs(market,started_at,status,item_count,new_count,changed_count) VALUES(?,?,?,?,?,?)',('mrkt',now,'ok',len(items),new,changed));con.commit();con.close();return new,changed
def refresh_floors(session,token):
 r=session.get(API+'/gifts/collections',headers=headers(token),timeout=20)
 if r.status_code==401:return None
 r.raise_for_status();wanted=set(TOP_COLLECTIONS);values={}
 for x in r.json():
  name=x.get('name')
  if name in wanted and x.get('floorPriceNanoTons') is not None:values[name]=float(x['floorPriceNanoTons'])/1e9
 atomic(FLOORS,{'updated_at':time.time(),'floors':values});return values
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
 s=requests.Session(impersonate='chrome124');token=load_token() or refresh_token(s);interval=1/TARGET_RPS;next_at=time.monotonic();next_floors=0.0;n401=n429=idx=0
 while True:
  wait=next_at-time.monotonic()
  if wait>0:time.sleep(wait)
  next_at=max(next_at+interval,time.monotonic())
  collection=TOP_COLLECTIONS[idx%len(TOP_COLLECTIONS)];idx+=1
  try:
   gate()
   if idx%len(TOP_COLLECTIONS)==1:pace()
   if time.monotonic()>=next_floors:
    if refresh_floors(s,token) is None:token=refresh_token(s);refresh_floors(s,token)
    next_floors=time.monotonic()+300
   payload=dict(PAYLOAD);payload['collectionNames']=[collection]
   r=s.post(API+'/gifts/saling',headers=headers(token),json=payload,timeout=20)
   if r.status_code==401:
    n401+=1;token=refresh_token(s);continue
   if r.status_code==429:
    n429+=1;delay=float(r.headers.get('retry-after') or 5);atomic(STATUS,{'market':'mrkt','status':'rate_limited','last_attempt':time.time(),'collection':collection,'rps':TARGET_RPS,'http_401':n401,'http_429':n429});time.sleep(delay);next_at=time.monotonic()+interval;continue
   r.raise_for_status();items=r.json().get('gifts',[]);new,changed=ingest(items)
   atomic(STATUS,{'market':'mrkt','status':'ok','last_success':time.time(),'collection':collection,'items':len(items),'new':new,'changed':changed,'rps':TARGET_RPS,'collection_cycle_seconds':round(len(TOP_COLLECTIONS)/TARGET_RPS,2),'http_401':n401,'http_429':n429,'buy_enabled':False})
  except Exception as e:
   atomic(STATUS,{'market':'mrkt','status':'error','last_attempt':time.time(),'collection':collection,'error':f'{type(e).__name__}: {str(e)[:180]}','rps':TARGET_RPS,'http_401':n401,'http_429':n429});time.sleep(3);next_at=time.monotonic()+interval
if __name__=='__main__':main()
