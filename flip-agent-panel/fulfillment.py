import json,re,sqlite3,subprocess,threading,time,traceback
from pathlib import Path
from funpay_adapter import funpay
from playerok_adapter import playerok
from portals_delivery import portals_delivery
DB='/root/flip-agent-panel/panel.db';TG_PY='/root/monochrome/session-venv/bin/python';TG_SCRIPT='/root/monochrome/telegram_fulfillment.py'
ASK='Спасибо за покупку! Напишите точный Telegram-логин получателя, начиная со знака @. Пожалуйста, проверьте написание: перевод на ошибочный аккаунт отменить нельзя.'
DONE='🎁 Подарок отправлен на {username}. Пожалуйста, проверьте получение и подтвердите выполнение заказа. Если всё в порядке, буду благодарен за отзыв!'
TG_DONE='🎁 Ваш подарок «{title}» отправлен на этот аккаунт по заказу с {market}. Пожалуйста, подтвердите получение в чате магазина и, если всё хорошо, оставьте отзыв. Спасибо!'
FINAL={'delivered','delivered_notification_warning','ignored_preexisting','manual_review'}
def con():
 c=sqlite3.connect(DB,timeout=30);c.row_factory=sqlite3.Row;return c
def event(m,t,e,p):
 c=con();c.execute('INSERT INTO marketplace_events(marketplace,event_type,entity_id,payload) VALUES(?,?,?,?)',(m,t,str(e),json.dumps(p,ensure_ascii=False,default=str)));c.commit();c.close()
def username_in(text):
 m=re.search(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]{5,32})(?![A-Za-z0-9_])',str(text or ''));return m.group(1) if m else None
def tg(cmd,*args,text=None):
 p=subprocess.run([TG_PY,TG_SCRIPT,cmd,*args],input=text,text=True,capture_output=True,timeout=70)
 if p.returncode:raise RuntimeError((p.stderr or p.stdout)[-1200:])
 return json.loads(p.stdout.strip().splitlines()[-1])
class Fulfillment:
 def __init__(self):self.lock=threading.RLock();self.init()
 def init(self):
  c=con();c.executescript("CREATE TABLE IF NOT EXISTS fulfillment_meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE IF NOT EXISTS fulfillment_jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,marketplace TEXT NOT NULL,order_id TEXT NOT NULL,card_id TEXT,source_market TEXT,source_item_id TEXT,buyer_username TEXT,chat_id TEXT,recipient_username TEXT,state TEXT NOT NULL DEFAULT 'waiting_payment',asked_at TEXT,transferred_at TEXT,completed_at TEXT,error TEXT,attempts INTEGER DEFAULT 0,raw TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,updated TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(marketplace,order_id));")
  if not c.execute("SELECT 1 FROM fulfillment_meta WHERE key='baseline_v1'").fetchone():
   for r in c.execute('SELECT marketplace,order_id,buyer_username,raw FROM marketplace_orders').fetchall():c.execute("INSERT OR IGNORE INTO fulfillment_jobs(marketplace,order_id,buyer_username,state,raw,error) VALUES(?,?,?,?,?,?)",(r['marketplace'],r['order_id'],r['buyer_username'],'ignored_preexisting',r['raw'],'existing order at automation enablement'))
   c.execute("INSERT INTO fulfillment_meta(key,value) VALUES('baseline_v1',datetime('now'))")
  c.commit();c.close()
 def discover(self):
  c=con();c.execute("INSERT OR IGNORE INTO fulfillment_jobs(marketplace,order_id,buyer_username,state,raw) SELECT marketplace,order_id,buyer_username,'waiting_payment',raw FROM marketplace_orders WHERE marketplace IN ('funpay','playerok')");c.commit();c.close()
 def jobs(self,limit=100):
  c=con();x=[dict(r) for r in c.execute('SELECT * FROM fulfillment_jobs ORDER BY id DESC LIMIT ?',(limit,))];c.close();return x
 def upd(self,id,**kw):
  kw['updated']=time.strftime('%Y-%m-%d %H:%M:%S');c=con();c.execute('UPDATE fulfillment_jobs SET '+','.join(k+'=?' for k in kw)+' WHERE id=?',list(kw.values())+[id]);c.commit();c.close()
 def order(self,j):
  c=con();r=c.execute('SELECT * FROM marketplace_orders WHERE marketplace=? AND order_id=?',(j['marketplace'],j['order_id'])).fetchone();c.close();return dict(r) if r else None
 def paid(self,m,status):return (m=='funpay' and str(status).lower()=='paid') or (m=='playerok' and str(status).upper() in {'PAID','PENDING'})
 def match_card(self,m,raw):
  c=con();r=None
  if m=='funpay':
   ext=str(raw.get('lot_id') or raw.get('offer_id') or '')
   if ext:r=c.execute("SELECT l.card_id,g.source_market,g.source_item_id,g.title FROM gift_card_listings l JOIN universal_gift_cards g ON g.id=l.card_id WHERE l.marketplace='funpay' AND l.external_id=? ORDER BY l.updated DESC LIMIT 1",(ext,)).fetchone()
  else:
   iid=str(raw.get('item_id') or '');slug=str(raw.get('item_slug') or '')
   r=c.execute("SELECT l.card_id,g.source_market,g.source_item_id,g.title FROM gift_card_listings l JOIN universal_gift_cards g ON g.id=l.card_id WHERE l.marketplace='playerok' AND (l.external_id=? OR l.url LIKE ?) ORDER BY l.updated DESC LIMIT 1",(iid,'%/'+slug)).fetchone()
  c.close();return dict(r) if r else None
 def resolve_chat(self,j,o,raw):
  if j['chat_id']:return str(j['chat_id'])
  if j['marketplace']=='funpay':return str(funpay.resolve_chat(o.get('buyer_username'))['chat_id'])
  cid=raw.get('chat_id')
  if cid:return str(cid)
  z=playerok.find_chat(o.get('buyer_username') or raw.get('buyer_username'))
  return str(z.get('chat_id') or '')
 def market_send(self,m,chat_id,text,buyer=None):
  if m=='funpay':return funpay.send_message(text,chat_id=int(chat_id) if chat_id else None,username=buyer)
  return playerok.send_message(chat_id,text)
 def inbound_texts(self,j,o,raw):
  out=[]
  for x in raw.get('obtaining_fields') or []:
   if isinstance(x,dict):out.append(str(x.get('value') or ''))
  out.append(str(raw.get('comment_from_buyer') or ''))
  if not j['chat_id']:return out
  if j['marketplace']=='funpay':
   for x in funpay.chat_messages(int(j['chat_id'])):
    if not x.get('is_own'):out.append(x.get('text') or '')
  else:
   buyer=(o.get('buyer_username') or '').casefold()
   for x in playerok.chat_messages(j['chat_id']):
    if (x.get('username') or '').casefold()==buyer:out.append(x.get('text') or '')
  return out
 def process(self,j):
  if j['state'] in FINAL:return
  o=self.order(j)
  if not o:return self.upd(j['id'],state='manual_review',error='marketplace order disappeared')
  if not self.paid(j['marketplace'],o['status']):return
  try:raw=json.loads(o.get('raw') or '{}')
  except Exception:raw={}
  if not j['card_id']:
   card=self.match_card(j['marketplace'],raw)
   if not card:return self.upd(j['id'],state='manual_review',error='cannot match order to universal gift card')
   self.upd(j['id'],card_id=card['card_id'],source_market=card['source_market'],source_item_id=card['source_item_id'],raw=json.dumps(raw,ensure_ascii=False));j=dict(j);j.update(card)
  if j.get('source_market')!='portals':return self.upd(j['id'],state='manual_review',error='automatic delivery currently supports Portals inventory only')
  if j['state']=='waiting_payment':
   cid=self.resolve_chat(j,o,raw)
   if not cid:return self.upd(j['id'],state='waiting_chat',error='order chat not found')
   self.market_send(j['marketplace'],cid,ASK,o.get('buyer_username'));self.upd(j['id'],state='waiting_username',chat_id=cid,asked_at=time.strftime('%Y-%m-%d %H:%M:%S'),error=None);event(j['marketplace'],'fulfillment.username_requested',j['order_id'],{'chat_id':cid});j=dict(j);j.update(state='waiting_username',chat_id=cid)
  if j['state'] in {'waiting_chat'}:
   cid=self.resolve_chat(j,o,raw)
   if not cid:return
   self.market_send(j['marketplace'],cid,ASK,o.get('buyer_username'));self.upd(j['id'],state='waiting_username',chat_id=cid,asked_at=time.strftime('%Y-%m-%d %H:%M:%S'),error=None);j=dict(j);j.update(state='waiting_username',chat_id=cid)
  if j['state']!='waiting_username':return
  recipient=None
  for text in self.inbound_texts(j,o,raw):
   if username_in(text):recipient=username_in(text)
  if not recipient:return
  valid=tg('validate',recipient)
  if valid.get('bot'):return self.upd(j['id'],error='recipient username belongs to a bot')
  actual=valid.get('username') or recipient
  self.upd(j['id'],state='delivering',recipient_username=actual,attempts=(j.get('attempts') or 0)+1,error=None)
  try:
   result=portals_delivery.transfer(j['source_item_id'],actual)
  except Exception as e:
   self.upd(j['id'],state='manual_review',error=f'{type(e).__name__}: {e}');event(j['marketplace'],'fulfillment.transfer_failed',j['order_id'],{'error':str(e)});return
  warnings=[];title=(self.match_card(j['marketplace'],raw) or {}).get('title') or 'Telegram Gift'
  if j['marketplace']=='playerok':
   try:playerok.mark_sent(j['order_id'])
   except Exception as e:warnings.append('playerok SENT: '+str(e))
  try:tg('send',actual,text=TG_DONE.format(title=title,market=j['marketplace']))
  except Exception as e:warnings.append('telegram: '+str(e))
  try:self.market_send(j['marketplace'],j['chat_id'],DONE.format(username='@'+actual),o.get('buyer_username'))
  except Exception as e:warnings.append('market chat: '+str(e))
  state='delivered_notification_warning' if warnings else 'delivered';now=time.strftime('%Y-%m-%d %H:%M:%S');self.upd(j['id'],state=state,recipient_username=actual,transferred_at=now,completed_at=now,error='; '.join(warnings) or None);event(j['marketplace'],'fulfillment.delivered',j['order_id'],{'recipient':'@'+actual,'warnings':warnings,'portals':result})
 def run_once(self):
  with self.lock:
   self.discover()
   for j in self.jobs(100):
    try:self.process(j)
    except Exception as e:self.upd(j['id'],error=f'{type(e).__name__}: {e}');event(j['marketplace'],'fulfillment.error',j['order_id'],{'error':str(e),'trace':traceback.format_exc()[-2000:]})
  return self.jobs(100)
 def status(self):return {'telegram':tg('status'),'portals':portals_delivery.status(),'jobs':self.jobs(20)}
fulfillment=Fulfillment()
def fulfillment_loop():
 time.sleep(8)
 while True:
  try:fulfillment.run_once()
  except Exception:pass
  time.sleep(10)
