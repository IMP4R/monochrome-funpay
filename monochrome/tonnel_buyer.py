#!/usr/bin/env python3
import base64, hashlib, json, os, sqlite3, subprocess, time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad
from curl_cffi import requests
import trading_control
BASE=Path('/root/monochrome');DB=BASE/'cache/market_listings.sqlite3';READER=BASE/'sessions/tonnel-reader';ROOT=BASE/'sessions/tonnel-buyer';STATUS=ROOT/'status.json';PROFILE=READER/'chrome-profile'
BALANCE_URL='https://gifts3.tonnel.network/api/balance/info';BUY_BASE='https://gifts.coffin.meme/api/buyGift/';LIST_URL='https://gifts.coffin.meme/api/listForSale';PASSWORD=b''
def atomic(p,d):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(d,ensure_ascii=False,indent=2));os.chmod(t,0o600);t.replace(p)
def auth_data():
 u=json.loads((READER/'webview.json').read_text())['url'];parts=urlsplit(u);q=parse_qs(parts.fragment);return (q.get('tgWebAppData') or parse_qs(parts.query).get('tgWebAppData') or [''])[0]
def refresh_auth():subprocess.run([str(BASE/'session-venv/bin/python'),str(BASE/'tonnel_refresh_webview.py')],check=True,cwd=BASE,timeout=45)
def context():
 cap=json.loads((READER/'api_capture.json').read_text());cd=json.loads((READER/'cloudflare_cookies.json').read_text());cookies={c['name']:c['value'] for c in cd.get('cookies',[]) if c.get('name') and c.get('value')};headers={k:v for k,v in (cap.get('headers') or {}).items() if k.lower() in ('accept','accept-language','content-type','origin','referer','user-agent')};headers.setdefault('content-type','application/json');headers.setdefault('origin','https://marketplace.tonnel.network');headers.setdefault('referer','https://marketplace.tonnel.network/');return cap['url'],headers,cookies
def cf_refresh():
 try:subprocess.run(['systemctl','start','tonnel-reader-collector.service'],check=False,timeout=310)
 except Exception:pass
def post(sess,url,payload,allow_cf_refresh=True):
 _,headers,cookies=context();r=sess.post(url,headers=headers,cookies=cookies,json=payload,timeout=30)
 if r.status_code in (401,403,429) and allow_cf_refresh:
  refresh_auth();cf_refresh();_,headers,cookies=context();r=sess.post(url,headers=headers,cookies=cookies,json=payload,timeout=30)
 return r
def balance(sess):
 r=post(sess,BALANCE_URL,{'authData':auth_data(),'ref':''});r.raise_for_status();d=r.json()
 if str(d.get('status','success')).lower() not in ('success','ok'):raise RuntimeError('balance: '+json.dumps(d,ensure_ascii=False)[:250])
 return Decimal(str(d.get('balance') or 0)),d
def evp(password,salt):
 out=b'';prev=b''
 while len(out)<48:prev=hashlib.md5(prev+password+salt).digest();out+=prev
 return out[:32],out[32:48]
def signed_time():
 ts=str(int(time.time()));salt=get_random_bytes(8);key,iv=evp(PASSWORD,salt);ct=AES.new(key,AES.MODE_CBC,iv).encrypt(pad(ts.encode(),16));return int(ts),base64.b64encode(b'Salted__'+salt+ct).decode()
def response_ok(r):
 if r.status_code not in (200,201):return False
 try:d=r.json()
 except Exception:return False
 if isinstance(d,dict):
  if d.get('success') is False:return False
  if str(d.get('status','success')).lower() in ('error','failed','failure'):return False
  if d.get('error'):return False
 return True
def listing(sess,lid):
 endpoint,headers,cookies=context();payload={'page':1,'limit':1,'sort':'{"gift_id":-1}','filter':json.dumps({'gift_id':int(lid),'price':{'$exists':True},'buyer':{'$exists':False},'asset':'TON'},separators=(',',':')),'ref':0,'price_range':None,'user_auth':auth_data()};r=sess.post(endpoint,headers=headers,cookies=cookies,json=payload,timeout=30)
 if r.status_code in (401,403,429):refresh_auth();cf_refresh();endpoint,headers,cookies=context();payload['user_auth']=auth_data();r=sess.post(endpoint,headers=headers,cookies=cookies,json=payload,timeout=30)
 if r.status_code!=200:return None,'availability HTTP '+str(r.status_code)
 try:items=r.json()
 except Exception:return None,'availability invalid JSON'
 x=next((z for z in items if str(z.get('gift_id'))==str(lid) and not z.get('buyer')),None)
 return (x,None) if x else (None,'not available')
def deals(cfg):
 q=urlencode({'min_score':cfg['min_score'],'max_premium':cfg['max_premium_pct'],'limit':500})
 with urlopen('http://127.0.0.1:8090/api/deals?'+q,timeout=45) as r:return json.load(r).get('items',[])
def init_db():
 c=sqlite3.connect(DB,timeout=15);trading_control.init_requests(c);c.execute('''CREATE TABLE IF NOT EXISTS tonnel_buyer_trades(listing_id TEXT PRIMARY KEY,collection TEXT,score REAL,buy_price REAL,target_price REAL,detected_at REAL,bought_at REAL,list_due_at REAL,listed_at REAL,status TEXT,last_error TEXT,buy_response TEXT,list_response TEXT)''');c.commit();return c
def seen(c,lid):return c.execute('select 1 from tonnel_buyer_trades where listing_id=?',(lid,)).fetchone() is not None
def upd(c,lid,**f):
 k=list(f);c.execute('update tonnel_buyer_trades set '+','.join(x+'=?' for x in k)+' where listing_id=?',[f[x] for x in k]+[lid]);c.commit()
def requpd(c,lid,status,error=None):c.execute("update trade_buy_requests set status=?,updated_at=?,last_error=? where market='tonnel' and listing_id=?",(status,time.time(),error,lid));c.commit()
def buy(c,sess,x,balance_ton,cfg):
 lid=str(x['listing_id']);price=Decimal(str(x['price']))
 if seen(c,lid):return balance_ton,'already processed'
 if int(lid)<=0:return balance_ton,'premarket lot is not supported'
 live,err=listing(sess,lid)
 if err:return balance_ton,err
 live_price=Decimal(str(live.get('price') or 0))
 if live_price!=price:return balance_ton,'price changed'
 estimated_cost=(price*Decimal('1.10')).quantize(Decimal('0.000001'),rounding=ROUND_HALF_UP)
 if estimated_cost>balance_ton:return balance_ton,'insufficient balance including Tonnel fee'
 target=(price*(Decimal(1)+Decimal(str(cfg['relist_markup_pct']))/100)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
 c.execute('insert or ignore into tonnel_buyer_trades values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(lid,x['collection'],x['score'],float(price),float(target),time.time(),None,None,None,'buying',None,None,None));c.commit()
 ts,wtf=signed_time();payload={'authData':auth_data(),'asset':'TON','price':float(price),'timestamp':ts,'wtf':wtf};r=post(sess,BUY_BASE+lid,payload);body=r.text[:4000]
 if not response_ok(r):upd(c,lid,status='failed_buy',last_error='buy HTTP '+str(r.status_code),buy_response=body);return balance_ton,'buy failed'
 now=time.time();upd(c,lid,status='bought',bought_at=now,list_due_at=now+cfg['list_delay_seconds'],buy_response=body,last_error=None);return max(Decimal(0),balance_ton-estimated_cost),None
def process_list(c,sess):
 for lid,target,due in c.execute("select listing_id,target_price,list_due_at from tonnel_buyer_trades where status in ('bought','listing')").fetchall():
  if time.time()<(due or 0):continue
  ts,wtf=signed_time();payload={'authData':auth_data(),'gift_id':int(lid),'price':float(target),'asset':'TON','timestamp':ts,'wtf':wtf};r=post(sess,LIST_URL,payload);body=r.text[:4000]
  if response_ok(r):upd(c,lid,status='listed',listed_at=time.time(),list_response=body,last_error=None)
  else:upd(c,lid,status='listing',list_response=body,last_error='list HTTP '+str(r.status_code))
def main():
 ROOT.mkdir(parents=True,exist_ok=True);os.chmod(ROOT,0o700);c=init_db();sess=requests.Session(impersonate='chrome124');last_auth=0
 while True:
  try:
   if time.time()-last_auth>300:refresh_auth();last_auth=time.time()
   cfg=trading_control.load_settings();process_list(c,sess);bal,info=balance(sess);items=deals(cfg);mp={(x.get('market','').lower(),str(x['listing_id'])):x for x in items};enabled=bool(cfg['auto_buy']['tonnel'])
   for market,lid in c.execute("select market,listing_id from trade_buy_requests where market='tonnel' and status='pending' order by requested_at").fetchall():
    x=mp.get((market,lid))
    if not x:requpd(c,lid,'rejected','lot stale or outside limits');continue
    requpd(c,lid,'processing');bal,err=buy(c,sess,x,bal,cfg);requpd(c,lid,'failed' if err else 'bought',err)
   eligible=[x for x in items if x.get('market','').lower()=='tonnel' and int(str(x['listing_id']))>0 and not seen(c,str(x['listing_id']))]
   atomic(STATUS,{'updated_at':time.time(),'state':'watching' if enabled else 'manual_only','enabled':enabled,'balance_ton':float(bal),'usdt_balance':info.get('usdtBalance'),'tonnel_balance':info.get('tonnelBalance'),'eligible_count':len(eligible),'telegram_name':info.get('name'),'error':None})
   if enabled:
    for x in eligible:bal,_=buy(c,sess,x,bal,cfg)
   time.sleep(2)
  except Exception as e:atomic(STATUS,{'updated_at':time.time(),'state':'error','enabled':False,'error':f'{type(e).__name__}: {str(e)[:300]}'});time.sleep(5)
if __name__=='__main__':main()
