#!/usr/bin/env python3
import json,os,time
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from curl_cffi import requests
ROOT=Path('/root/monochrome/sessions/tonnel-reader');CAP=ROOT/'api_capture.json';WEB=ROOT/'webview.json';COOK=ROOT/'cloudflare_cookies.json';OUT=ROOT/'latest_response.json';STATUS=ROOT/'direct_fetch_status.json';FLOORS=ROOT/'mrkt_floors.json'
def atomic(p,d):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(d,ensure_ascii=False));os.chmod(t,0o600);t.replace(p)
def auth():
 u=urlsplit(json.loads(WEB.read_text())['url']);return (parse_qs(u.fragment).get('tgWebAppData') or parse_qs(u.query).get('tgWebAppData') or [None])[0]
def main():
 cap=json.loads(CAP.read_text());user_auth=auth()
 if not user_auth:raise RuntimeError('fresh Tonnel user_auth missing')
 cd=json.loads(COOK.read_text());cookies={c['name']:c['value'] for c in cd.get('cookies',[]) if c.get('name') and c.get('value')};h={k:v for k,v in (cap.get('headers') or {}).items() if k.lower() in ('accept','accept-language','content-type','origin','referer','user-agent')};h.setdefault('content-type','application/json');h.setdefault('origin','https://marketplace.tonnel.network');h.setdefault('referer','https://marketplace.tonnel.network/')
 try:collections=list((json.loads(FLOORS.read_text()).get('floors') or {}).keys())
 except Exception:collections=[]
 base={'price':{'$exists':True},'buyer':{'$exists':False},'refunded':{'$ne':True},'export_at':{'$exists':True},'asset':'TON'}
 jobs=[]
 # Broad low-price scan plus two cheapest pages for every collection with a known MRKT floor.
 for page in range(1,11):jobs.append((page,None))
 for collection in collections:
  for page in (1,2):jobs.append((page,collection))
 sess=requests.Session(impersonate='chrome124');items={};last_status=0;started=time.time()
 for page,collection in jobs:
  flt=dict(base)
  if collection:flt['gift_name']=collection
  body={'page':page,'limit':30,'sort':'{"price":1,"gift_id":-1}','filter':json.dumps(flt,separators=(',',':')),'ref':0,'price_range':None,'user_auth':user_auth}
  r=sess.post(cap['url'],headers=h,cookies=cookies,json=body,timeout=30);last_status=r.status_code
  if r.status_code!=200:
   atomic(STATUS,{'updated_at':time.time(),'ok':False,'status':r.status_code,'items':len(items),'cloudflare':r.status_code in (403,429) or 'cloudflare' in r.text.lower() or 'just a moment' in r.text.lower(),'jobs_done':len(items),'body_prefix':r.text[:160]});raise SystemExit(2)
  try:batch=r.json()
  except Exception:batch=None
  if not isinstance(batch,list):raise RuntimeError('Tonnel payload is not a list')
  for x in batch:
   if x.get('gift_id') is not None:items[str(x['gift_id'])]=x
 payload=list(items.values());atomic(OUT,{'captured_at':time.time(),'status':200,'payload':payload});atomic(STATUS,{'updated_at':time.time(),'ok':True,'status':200,'items':len(payload),'cloudflare':False,'requests':len(jobs),'floor_collections':len(collections),'seconds':round(time.time()-started,2),'cookie_names':sorted(cookies)})
 print(json.dumps(json.loads(STATUS.read_text()),ensure_ascii=False))
if __name__=='__main__':main()
