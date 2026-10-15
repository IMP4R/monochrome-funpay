from __future__ import annotations
import json,os
from urllib.request import Request,urlopen
from urllib.error import HTTPError

class MonochromeError(RuntimeError):pass
class MonochromeAdapter:
 def __init__(self):self.base=os.getenv('MONOCHROME_BASE_URL','http://127.0.0.1:8090').rstrip('/')
 def _token(self):
  token=os.getenv('MONOCHROME_ADMIN_TOKEN','').strip()
  if not token:raise MonochromeError('MONOCHROME_ADMIN_TOKEN не задан')
  return token
 def request(self,path,method='GET',body=None,timeout=240):
  data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
  req=Request(self.base+path,data=data,method=method,headers={'Authorization':'Bearer '+self._token(),'Content-Type':'application/json'})
  try:
   with urlopen(req,timeout=timeout) as r:return json.loads(r.read())
  except HTTPError as e:
   try:detail=json.loads(e.read()).get('detail')
   except Exception:detail=f'HTTP {e.code}'
   raise MonochromeError(str(detail)[:500])
 def inventory(self,view='all'):
  return self.request('/api/inventory?view='+view,timeout=90)
 def find(self,market,item_id):
  data=self.inventory('all')
  item=next((x for x in data.get('items',[]) if str(x.get('market'))==str(market) and str(x.get('id'))==str(item_id)),None)
  if not item:raise MonochromeError('Подарок не найден в Monochrome Panel')
  return item
 def generate(self,market,item_id):return self.request('/api/inventory/generate','POST',{'market':market,'item_id':str(item_id)},360)
 def listing_text(self,market,item_id,sale_price,random_bg=False):return self.request('/api/inventory/listing-text','POST',{'market':market,'item_id':str(item_id),'sale_price':int(sale_price),'random_bg':bool(random_bg)},240)
mono=MonochromeAdapter()
