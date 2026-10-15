import json,re,subprocess,threading
from pathlib import Path
import requests
BASE='https://portal-market.com/api';AUTH=Path('/root/monochrome/.auth');PY='/root/monochrome/session-venv/bin/python';TG='/root/monochrome/telegram_fulfillment.py'
class PortalsDelivery:
 def __init__(self):self.lock=threading.RLock();self.s=requests.Session();self.s.headers.update({'Accept':'application/json','Origin':'https://portal-market.com','Referer':'https://portal-market.com/','User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/142 Safari/537.36'})
 def refresh(self):
  z=json.loads(subprocess.check_output([PY,TG,'refresh-auth'],text=True,timeout=60));return z
 def _request(self,method,path,**kw):
  if not AUTH.exists():self.refresh()
  h={'Authorization':AUTH.read_text().strip()};r=self.s.request(method,BASE+path,headers=h,timeout=40,**kw)
  if r.status_code==401:self.refresh();h={'Authorization':AUTH.read_text().strip()};r=self.s.request(method,BASE+path,headers=h,timeout=40,**kw)
  if r.status_code>=400:raise RuntimeError(f'Portals {path} HTTP {r.status_code}: {r.text[:1000]}')
  try:return r.json()
  except Exception:return {'ok':True,'text':r.text[:1000]}
 def owned(self):
  out=[]
  for st in ('unlisted','listed'):
   off=0
   while off<500:
    z=self._request('GET','/nfts/owned',params={'offset':off,'limit':50,'status':st});rows=z.get('results') or z.get('nfts') or []
    for x in rows:x['_owned_status']=st
    out+=rows
    if len(rows)<50:break
    off+=len(rows)
  return out
 def status(self):
  rows=self.owned();return {'ok':True,'owned_count':len(rows),'listed':sum(x.get('_owned_status')=='listed' for x in rows),'unlisted':sum(x.get('_owned_status')=='unlisted' for x in rows)}
 def transfer(self,nft_id,username):
  nft_id=str(nft_id);username=str(username).strip().lstrip('@')
  if not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username):raise ValueError('invalid Telegram username')
  with self.lock:
   mine={str(x.get('id')):x for x in self.owned()}
   if nft_id not in mine:raise RuntimeError('gift is not present in the Portals inventory; automatic retry blocked')
   if mine[nft_id].get('_owned_status')=='listed':self._request('POST',f'/nfts/{nft_id}/unlist',json={})
   z=self._request('POST','/nfts/transfer-gifts',json={'nft_ids':[nft_id],'recipient':username,'anonymous':False})
   failed=z.get('failed_items') or []
   count=z.get('transferred_count')
   if failed or (count is not None and int(count)!=1):raise RuntimeError('Portals transfer rejected: '+json.dumps(z,ensure_ascii=False)[:1200])
   return {'ok':True,'nft_id':nft_id,'recipient':'@'+username,'response':z}
portals_delivery=PortalsDelivery()
