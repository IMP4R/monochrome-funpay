#!/usr/bin/env python3
import json, os, sqlite3, subprocess, time
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from curl_cffi import requests
import trading_control
BASE=Path('/root/monochrome'); DB=BASE/'cache/market_listings.sqlite3'; AUTH=BASE/'.auth'; ROOT=BASE/'sessions/portals-buyer'; STATUS=ROOT/'status.json'; API='https://portal-market.com/api'; FEE=Decimal('0.025')
def atomic(p,d):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(d,ensure_ascii=False,indent=2));os.chmod(t,0o600);t.replace(p)
def headers():return {'Authorization':AUTH.read_text().strip(),'Origin':'https://portal-market.com','Referer':'https://portal-market.com/','User-Agent':'Mozilla/5.0','Content-Type':'application/json'}
def refresh():subprocess.run([str(BASE/'session-venv/bin/python'),str(BASE/'portals_refresh_auth.py')],check=True,cwd=BASE)
def api(sess,method,path,**kw):
 r=sess.request(method,API+path,headers=headers(),timeout=25,**kw)
 if r.status_code==401:refresh();r=sess.request(method,API+path,headers=headers(),timeout=25,**kw)
 return r
def balance(sess):
 r=api(sess,'GET','/users/wallets/');r.raise_for_status();return Decimal(str(r.json().get('balance') or 0))
def deals(cfg):
 q=urlencode({'min_score':cfg['min_score'],'max_premium':cfg['max_premium_pct'],'limit':500})
 with urlopen('http://127.0.0.1:8090/api/deals?'+q,timeout=45) as r:return json.load(r).get('items',[])
def init_db():
 c=sqlite3.connect(DB,timeout=15);trading_control.init_requests(c);c.execute('''CREATE TABLE IF NOT EXISTS portals_buyer_trades(listing_id TEXT PRIMARY KEY,collection TEXT,score REAL,buy_price REAL,target_price REAL,detected_at REAL,bought_at REAL,list_due_at REAL,listed_at REAL,status TEXT,last_error TEXT,buy_response TEXT,list_response TEXT)''');c.commit();return c
def seen(c,lid):return c.execute('select 1 from portals_buyer_trades where listing_id=?',(lid,)).fetchone() is not None
def upd(c,lid,**f):
 k=list(f);c.execute('update portals_buyer_trades set '+','.join(x+'=?' for x in k)+' where listing_id=?',[f[x] for x in k]+[lid]);c.commit()
def requpd(c,lid,status,error=None):c.execute("update trade_buy_requests set status=?,updated_at=?,last_error=? where market='portals' and listing_id=?",(status,time.time(),error,lid));c.commit()
def buy(c,sess,x,balance_ton,cfg):
 lid=str(x['listing_id']);price=Decimal(str(x['price']))
 if seen(c,lid):return balance_ton,'already processed'
 chk=api(sess,'POST','/nfts/check-availability',json={'nft_details':[{'id':lid,'price':str(price)}]})
 if chk.status_code!=200:return balance_ton,'availability HTTP '+str(chk.status_code)
 data=chk.json();valid=data.get('valid_nft_ids') or []
 if lid not in [str(v) for v in valid]:return balance_ton,'not available or price changed'
 if price>balance_ton:return balance_ton,'insufficient balance'
 target=(price*(Decimal(1)+Decimal(str(cfg['relist_markup_pct']))/100)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
 c.execute('insert or ignore into portals_buyer_trades values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(lid,x['collection'],x['score'],float(price),float(target),time.time(),None,None,None,'buying',None,None,None));c.commit()
 r=api(sess,'POST','/nfts',json={'nft_details':[{'id':lid,'price':str(price)}]});body=r.text[:4000]
 if r.status_code not in (200,201):upd(c,lid,status='failed_buy',last_error='buy HTTP '+str(r.status_code),buy_response=body);return balance_ton,'buy HTTP '+str(r.status_code)
 now=time.time();upd(c,lid,status='bought',bought_at=now,list_due_at=now+cfg['list_delay_seconds'],buy_response=body,last_error=None);return balance_ton-price,None
def process_list(c,sess):
 for lid,target,due in c.execute("select listing_id,target_price,list_due_at from portals_buyer_trades where status in ('bought','listing')").fetchall():
  if time.time()<(due or 0):continue
  r=api(sess,'POST','/nfts/bulk-list',json={'nft_prices':[{'nft_id':lid,'price':str(target)}]});body=r.text[:4000]
  if r.status_code in (200,201):upd(c,lid,status='listed',listed_at=time.time(),list_response=body,last_error=None)
  else:upd(c,lid,status='listing',list_response=body,last_error='list HTTP '+str(r.status_code))
def main():
 ROOT.mkdir(parents=True,exist_ok=True);os.chmod(ROOT,0o700);c=init_db();sess=requests.Session(impersonate='chrome124')
 while True:
  try:
   cfg=trading_control.load_settings();process_list(c,sess);bal=balance(sess);items=deals(cfg);mp={(x.get('market','').lower(),str(x['listing_id'])):x for x in items};enabled=bool(cfg['auto_buy']['portals'])
   for market,lid in c.execute("select market,listing_id from trade_buy_requests where market='portals' and status='pending' order by requested_at").fetchall():
    x=mp.get((market,lid))
    if not x:requpd(c,lid,'rejected','lot stale or outside limits');continue
    requpd(c,lid,'processing');bal,err=buy(c,sess,x,bal,cfg);requpd(c,lid,'failed' if err else 'bought',err)
   eligible=[x for x in items if x.get('market','').lower()=='portals' and not seen(c,str(x['listing_id']))]
   atomic(STATUS,{'updated_at':time.time(),'state':'watching' if enabled else 'manual_only','enabled':enabled,'balance_ton':float(bal),'eligible_count':len(eligible),'error':None})
   if enabled:
    for x in eligible:bal,_=buy(c,sess,x,bal,cfg)
   time.sleep(2)
  except Exception as e:atomic(STATUS,{'updated_at':time.time(),'state':'error','enabled':False,'error':f'{type(e).__name__}: {str(e)[:300]}'});time.sleep(5)
if __name__=='__main__':main()
