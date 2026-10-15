import os,requests
class PlayerokAdapter:
 def __init__(self):self.base=os.getenv("PLAYEROK_BRIDGE_URL","http://127.0.0.1:4191");self.token=os.getenv("PLAYEROK_BRIDGE_TOKEN",'0')
 def _h(self):return {"Authorization":"Bearer "+self.token}
 def create_offer(self,card,cfg):
  d={"category_id":cfg.get("category_id") or "1efcc43d-d46b-6b10-9805-49c1107e28b3","collection":card.get('collection'),"model":card.get('model'),"title":card['title'],"description":card['description'],"price":int(card['sale_price']),"image_url":card.get('image_url')}
  r=requests.post(self.base+"/create",headers=self._h(),json=d,timeout=90)
  if r.status_code!=200:raise RuntimeError(f"Playerok bridge HTTP {r.status_code}: {r.text[:1200]}")
  z=r.json()
  if not z.get('ok'):raise RuntimeError(z.get('error') or str(z))
  return {"offer_id":z['id'],"url":z['url'],"status":z.get('status')}
 def remove_offer(self,oid):
  r=requests.post(self.base+"/remove",headers=self._h(),json={"id":str(oid)},timeout=60)
  if r.status_code!=200 or not r.json().get('ok'):raise RuntimeError(r.text[:1000])
  return r.json()
 def deals(self):
  r=requests.get(self.base+"/deals",headers=self._h(),timeout=75)
  if r.status_code!=200:raise RuntimeError(f"Playerok deals bridge HTTP {r.status_code}: {r.text[:1200]}")
  z=r.json()
  if not z.get('ok'):raise RuntimeError(z.get('error') or str(z))
  return z.get('deals') or []
 def find_chat(self,username):
  r=requests.get(self.base+"/chat/find",headers=self._h(),params={'username':str(username or '')},timeout=60);z=r.json()
  if r.status_code!=200 or not z.get('ok'):raise RuntimeError(z.get('error') or r.text[:1000])
  return z
 def chat_messages(self,chat_id):
  r=requests.get(self.base+"/chat/messages",headers=self._h(),params={'chat_id':str(chat_id)},timeout=60);z=r.json()
  if r.status_code!=200 or not z.get('ok'):raise RuntimeError(z.get('error') or r.text[:1000])
  return z.get('messages') or []
 def send_message(self,chat_id,text):
  r=requests.post(self.base+"/chat/send",headers=self._h(),json={'chat_id':str(chat_id),'text':str(text)},timeout=60);z=r.json()
  if r.status_code!=200 or not z.get('ok'):raise RuntimeError(z.get('error') or r.text[:1000])
  return z
 def mark_sent(self,deal_id):
  r=requests.post(self.base+"/deal/sent",headers=self._h(),json={'deal_id':str(deal_id)},timeout=60);z=r.json()
  if r.status_code!=200 or not z.get('ok'):raise RuntimeError(z.get('error') or r.text[:1000])
  return z
playerok=PlayerokAdapter()
