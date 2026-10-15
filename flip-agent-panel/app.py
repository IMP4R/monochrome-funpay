import asyncio,importlib.util,json,os,sqlite3,threading,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from fastapi import FastAPI,Header,HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
import agent_core,sandbox
from funpay_adapter import funpay
from playerok_adapter import playerok
from fulfillment import fulfillment,fulfillment_loop
from monochrome_adapter import mono,MonochromeError
from credentials_store import CredentialStore,CredentialError
from pricing import pricing_quote
BASE=Path(__file__).parent;DB=BASE/'panel.db';PLUGINS=BASE/'plugins';STATIC=BASE/'static'
app=FastAPI(title='Flip Agent Pipeline',version='0.3.0');app.mount('/static',StaticFiles(directory=STATIC),name='static')
SYSTEM='''Ты главный агент универсальной серверной платформы плагинов. Платформа не ограничена подарками или конкретным маркетплейсом. Активный плагин Universal Gift Cards отвечает только за инвентарь и публикацию коллекционных Telegram-подарков. Не смешивай обязанности ядра и плагинов, не выдумывай данные, не раскрывай секреты и требуй подтверждение перед внешними действиями.''';
KB='''Цель: искать недооцененные монохромные Telegram-подарки. Проверять коллекцию, модель, фон, две площадки, комиссии, вывод и ликвидность. Автодействия идемпотентны и журналируются. Плагин не зависит от Telegram/Cardinal.'''
def db():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;return c
def require(token):
 # panel token check disabled on request; access is restricted by firewall to a single IP
 return
def q(sql,args=()):
 c=db();r=[dict(x) for x in c.execute(sql,args).fetchall()];c.close();return r
def ex(sql,args=()):
 c=db();cur=c.execute(sql,args);c.commit();v=cur.lastrowid;c.close();return v
@app.on_event('startup')
def init():
 c=db();c.executescript('''CREATE TABLE IF NOT EXISTS deals(id TEXT PRIMARY KEY,marketplace TEXT,title TEXT,status TEXT,buy REAL DEFAULT 0,sell REAL DEFAULT 0,created TEXT DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,input TEXT,output TEXT,status TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,type TEXT,status TEXT,input TEXT,output TEXT,meta TEXT DEFAULT '{}',created TEXT DEFAULT CURRENT_TIMESTAMP,updated TEXT DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS plugins(id TEXT PRIMARY KEY,slug TEXT,title TEXT,description TEXT,status TEXT,path TEXT,test_output TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS knowledge(id INTEGER PRIMARY KEY,title TEXT,content TEXT,updated TEXT DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS marketplace_orders(marketplace TEXT NOT NULL,order_id TEXT NOT NULL,status TEXT,title TEXT,price REAL DEFAULT 0,amount REAL,buyer_username TEXT,ordered_at TEXT,raw TEXT,updated TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(marketplace,order_id));CREATE TABLE IF NOT EXISTS marketplace_events(id INTEGER PRIMARY KEY AUTOINCREMENT,marketplace TEXT,event_type TEXT,entity_id TEXT,payload TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS gift_offer_drafts(id TEXT PRIMARY KEY,market TEXT NOT NULL,item_id TEXT NOT NULL,status TEXT NOT NULL,title TEXT,description TEXT,price INTEGER,image_url TEXT,funpay_type TEXT,plugin_id TEXT,payload TEXT,draft TEXT,funpay_offer_id TEXT,funpay_url TEXT,error TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,updated TEXT DEFAULT CURRENT_TIMESTAMP);''');
 if not c.execute('SELECT 1 FROM knowledge LIMIT 1').fetchone():c.execute('INSERT INTO knowledge(title,content) VALUES(?,?)',('Правила флипа',KB))
 c.commit();c.close();credentials.apply_all();cards_plugin.init();cards_plugin.ensure_async();threaded(funpay_poll_loop);threaded(playerok_poll_loop);threaded(fulfillment_loop)
def job_new(kind,data):
 jid=uuid.uuid4().hex;ex('INSERT INTO jobs(id,type,status,input) VALUES(?,?,?,?)',(jid,kind,'queued',json.dumps(data,ensure_ascii=False)));return jid
def job_set(jid,status,output='',meta=None):ex('UPDATE jobs SET status=?,output=?,meta=?,updated=CURRENT_TIMESTAMP WHERE id=?',(status,output,json.dumps(meta or {},ensure_ascii=False),jid))
def threaded(fn,*args):threading.Thread(target=fn,args=args,daemon=True).start()
def sync_funpay_orders():
 rows=funpay.orders(limit=100,include_closed=True);c=db()
 for row in rows:
  previous=c.execute("SELECT status FROM marketplace_orders WHERE marketplace=? AND order_id=?",('funpay',row['id'])).fetchone()
  event='order.created' if not previous else ('order.status_changed' if previous['status']!=row['status'] else None)
  c.execute("""INSERT INTO marketplace_orders(marketplace,order_id,status,title,price,amount,buyer_username,ordered_at,raw,updated) VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(marketplace,order_id) DO UPDATE SET status=excluded.status,title=excluded.title,price=excluded.price,amount=excluded.amount,buyer_username=excluded.buyer_username,ordered_at=excluded.ordered_at,raw=excluded.raw,updated=CURRENT_TIMESTAMP""",('funpay',row['id'],row['status'],row['description'],row['price'] or 0,row['amount'],row['buyer_username'],row['date'],json.dumps(row,ensure_ascii=False,default=str)))
  if event:
   c.execute('INSERT INTO marketplace_events(marketplace,event_type,entity_id,payload) VALUES(?,?,?,?)',('funpay',event,row['id'],json.dumps(row,ensure_ascii=False,default=str)))
   if row.get('lot_id') and row.get('status') in ('paid','closed'):threaded(cards_plugin.sold,'funpay',str(row['lot_id']))
 c.commit();c.close();return rows

def funpay_poll_loop():
 time.sleep(2)
 while True:
  try:sync_funpay_orders()
  except Exception:pass
  time.sleep(30)

PLAYEROK_SALE_STATUSES={'PAID','PENDING','SENT','CONFIRMED','CONFIRMED_AUTOMATICALLY'}
def sync_playerok_deals():
 rows=playerok.deals();c=db();triggers=[]
 for row in rows:
  deal_id=str(row.get('deal_id') or '')
  if not deal_id:continue
  previous=c.execute("SELECT status FROM marketplace_orders WHERE marketplace=? AND order_id=?",('playerok',deal_id)).fetchone()
  event='order.created' if not previous else ('order.status_changed' if previous['status']!=row.get('status') else None)
  c.execute("""INSERT INTO marketplace_orders(marketplace,order_id,status,title,price,amount,buyer_username,ordered_at,raw,updated) VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(marketplace,order_id) DO UPDATE SET status=excluded.status,title=excluded.title,price=excluded.price,amount=excluded.amount,buyer_username=excluded.buyer_username,ordered_at=excluded.ordered_at,raw=excluded.raw,updated=CURRENT_TIMESTAMP""",('playerok',deal_id,row.get('status'),row.get('item_name'),row.get('price') or 0,1,row.get('buyer_username'),row.get('created_at'),json.dumps(row,ensure_ascii=False,default=str)))
  if event:c.execute('INSERT INTO marketplace_events(marketplace,event_type,entity_id,payload) VALUES(?,?,?,?)',('playerok',event,deal_id,json.dumps(row,ensure_ascii=False,default=str)))
  if row.get('direction')=='OUT' and row.get('status') in PLAYEROK_SALE_STATUSES:
   listing=c.execute("SELECT status FROM gift_card_listings WHERE marketplace='playerok' AND (external_id=? OR url LIKE ?) ORDER BY updated DESC LIMIT 1",(str(row.get('item_id') or ''),'%/'+str(row.get('item_slug') or ''))).fetchone()
   if listing and listing['status']!='sold':triggers.append(row)
 c.commit();c.close()
 for row in triggers:
  result=cards_plugin.sold('playerok',str(row.get('item_id') or ''),str(row.get('item_slug') or ''))
  ex('INSERT INTO marketplace_events(marketplace,event_type,entity_id,payload) VALUES(?,?,?,?)',('playerok','sale.cascade',str(row.get('deal_id')),json.dumps({'deal':row,'result':result},ensure_ascii=False,default=str)))
 return rows

def playerok_poll_loop():
 time.sleep(4)
 while True:
  try:sync_playerok_deals()
  except Exception as e:
   try:ex('INSERT INTO marketplace_events(marketplace,event_type,entity_id,payload) VALUES(?,?,?,?)',('playerok','poll.error','deals',json.dumps({'error':f'{type(e).__name__}: {e}'},ensure_ascii=False)))
   except Exception:pass
  time.sleep(15)

def market_health():
 fs=funpay.status(live=False)
 return [{'name':'funpay','ok':fs['connected'],'detail':fs['detail']},{'name':'ggsel','ok':bool(os.getenv('GGSEL_API_KEY')),'detail':'Seller API готов' if os.getenv('GGSEL_API_KEY') else 'нужен API key'},{'name':'playerok','ok':bool(os.getenv('PLAYEROK_TOKEN')),'detail':'токен настроен' if os.getenv('PLAYEROK_TOKEN') else 'нужен token'}]
@app.get('/api/funpay/status')
def funpay_status(live:bool=False,x_panel_token:str|None=Header(None)):
 require(x_panel_token);return funpay.status(live=live)
@app.post('/api/funpay/sync')
def funpay_sync(x_panel_token:str|None=Header(None)):
 require(x_panel_token);rows=sync_funpay_orders();return {'ok':True,'count':len(rows),'last_sync':funpay.last_sync}
@app.get('/api/funpay/orders')
def funpay_orders(limit:int=50,refresh:bool=False,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 if refresh:sync_funpay_orders()
 return q('SELECT marketplace,order_id,status,title,price,amount,buyer_username,ordered_at,updated FROM marketplace_orders WHERE marketplace=? ORDER BY COALESCE(ordered_at,updated) DESC LIMIT ?',('funpay',max(1,min(limit,100))))
@app.get('/api/funpay/orders/{order_id}')
def funpay_order(order_id:str,x_panel_token:str|None=Header(None)):
 require(x_panel_token);return funpay.order(order_id)
@app.get('/api/playerok/orders')
def playerok_orders(limit:int=50,refresh:bool=False,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 if refresh:sync_playerok_deals()
 return q('SELECT marketplace,order_id,status,title,price,amount,buyer_username,ordered_at,updated FROM marketplace_orders WHERE marketplace=? ORDER BY COALESCE(ordered_at,updated) DESC LIMIT ?',('playerok',max(1,min(limit,100))))
@app.get('/api/fulfillment/jobs')
def fulfillment_jobs(limit:int=100,x_panel_token:str|None=Header(None)):
 require(x_panel_token);return fulfillment.jobs(max(1,min(limit,200)))
@app.post('/api/fulfillment/run')
def fulfillment_run(x_panel_token:str|None=Header(None)):
 require(x_panel_token);return fulfillment.run_once()
@app.get('/api/fulfillment/status')
def fulfillment_status(x_panel_token:str|None=Header(None)):
 require(x_panel_token);return fulfillment.status()
class FunPayMessageIn(BaseModel):
 text:str=Field(min_length=1,max_length=4000);chat_id:int|None=None;username:str|None=Field(None,max_length=100);confirm:bool=False
@app.post('/api/funpay/messages')
def funpay_message(v:FunPayMessageIn,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 if not v.confirm:raise HTTPException(409,'Для реальной отправки укажите confirm=true')
 result=funpay.send_message(v.text,chat_id=v.chat_id,username=v.username)
 ex('INSERT INTO marketplace_events(marketplace,event_type,entity_id,payload) VALUES(?,?,?,?)',('funpay','message.sent',str(result.get('chat_id')),json.dumps({'chat_id':result.get('chat_id'),'message_id':result.get('message_id')},ensure_ascii=False)))
 return result


def infer_funpay_type(item):
 text=' '.join(str(item.get(k) or '') for k in ('collection','model')).lower()
 rules=[('chill flame','%Факел-Chill flame'),('факел','%Факел-Chill flame'),('vice cream','Мороженое'),('ice cream','Мороженое'),('морожен','Мороженое'),('mood pack','%Рюкзак-Mood Pack'),('spring basket','%Корзина-Spring Basket'),('shard','%Кристалл-Shard'),('ring','Кольцо'),('heart','Сердце'),('star','Звезда'),('candle','Свеча'),('watch','Часы'),('helmet','Шлем'),('skull','Череп')]
 return next((value for key,value in rules if key in text),'Прочее')
def gift_image_url(value):
 value=str(value or '')
 if value.startswith('/generated/'):
  return '/api/gifts/assets/'+value.rsplit('/',1)[-1]
 return value
from plugins.universal_gift_cards.plugin import GiftCardsPlugin
cards_plugin=GiftCardsPlugin(DB,mono,funpay,gift_image_url,infer_funpay_type)
credentials=CredentialStore(BASE/'secrets',funpay)

def offer_plugin_row():
 rows=q("SELECT id,path FROM plugins WHERE slug=? AND status='active' ORDER BY created DESC LIMIT 1",('monochrome_funpay_offer',))
 if not rows:raise RuntimeError('Активный плагин monochrome_funpay_offer пока не готов')
 return rows[0]
def call_offer_plugin(event):
 row=offer_plugin_row();path=Path(row['path'])/'plugin.py';name='gift_offer_plugin_'+row['id']
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 result=asyncio.run(module.Plugin().handle(event,{}))
 if not isinstance(result,dict) or not result.get('allowed'):raise RuntimeError('; '.join(result.get('errors') or ['Плагин отклонил предложение']))
 return row['id'],result
def normalize_offer_draft(result):
 raw=result.get('draft') or {};fields=raw.get('fields') or {}
 return {'node_id':int(raw.get('node_id') or 3064),'funpay_type':str(raw.get('funpay_type') or 'Прочее'),'title_ru':str(fields.get('title') or '')[:100],'title_en':'','description_ru':str(fields.get('description_ru') or ''),'description_en':str(fields.get('description_en') or ''),'price':int(fields.get('price') or 0),'active':False,'deactivate_after_sale':True,'source':fields.get('source'),'preview_image_url':fields.get('preview_image_url')}
@app.get('/api/gifts/assets/{filename}',include_in_schema=False)
def gift_asset(filename:str):
 if filename!=Path(filename).name:raise HTTPException(404,'Файл не найден')
 path=Path('/root/monochrome/static/generated')/filename
 if not path.is_file():raise HTTPException(404,'Файл не найден')
 return FileResponse(path,headers={'Cache-Control':'public, max-age=3600'})
@app.get('/api/pricing/ton')
def ton_pricing():return pricing_quote(1)
@app.get('/api/gifts')
def gifts(view:str='all',x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 try:data=mono.inventory(view if view in ('all','inventory','listed') else 'all')
 except Exception as e:raise HTTPException(502,str(e)[:500])
 for item in data.get('items',[]):
  item['generated_url']=gift_image_url(item.get('generated_url'));item['funpay_type']=infer_funpay_type(item)
  ton=float(item.get('price') if item.get('price') is not None else item.get('floor') or 0);item['recommended_price']=pricing_quote(ton)['sale_price']
 return data
class GiftDraftIn(BaseModel):
 market:str=Field(min_length=1,max_length=30);item_id:str=Field(min_length=1,max_length=200);sale_price:int=Field(ge=1,le=10000000);regenerate_image:bool=False;random_bg:bool=False
def gift_draft_worker(jid,data):
 try:
  job_set(jid,'running','Получаю подарок и визуальные слои из Monochrome Panel')
  item=mono.find(data['market'],data['item_id']);generated=item.get('generated_url')
  if data['regenerate_image'] or not generated:
   job_set(jid,'running','Генерирую изображение подарка в Monochrome Panel');generated=mono.generate(data['market'],data['item_id']).get('url')
  if not generated:raise RuntimeError('Monochrome Panel не вернула изображение')
  job_set(jid,'running','Gemini формирует описание предложения')
  text=mono.listing_text(data['market'],data['item_id'],data['sale_price'],data['random_bg']).get('text')
  payload={'collection':str(item.get('collection') or ''),'model':str(item.get('model') or ''),'backdrop':str(item.get('backdrop') or ''),'market':str(item.get('market') or data['market']),'item_id':str(item.get('id') or data['item_id']),'listing_text':str(text or ''),'generated_image_url':gift_image_url(generated),'sale_price':data['sale_price'],'funpay_type':infer_funpay_type(item)}
  plugin_id,result=call_offer_plugin({'action':'prepare_offer','item':payload});draft=normalize_offer_draft(result);did=uuid.uuid4().hex
  ex('INSERT INTO gift_offer_drafts(id,market,item_id,status,title,description,price,image_url,funpay_type,plugin_id,payload,draft) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(did,payload['market'],payload['item_id'],'draft',draft['title_ru'],draft['description_ru'],draft['price'],draft['preview_image_url'],draft['funpay_type'],plugin_id,json.dumps(payload,ensure_ascii=False),json.dumps(draft,ensure_ascii=False)))
  job_set(jid,'passed','Предложение готово к проверке и публикации',{'draft_id':did,'plugin_id':plugin_id,'image_url':draft['preview_image_url'],'price':draft['price']})
 except Exception as e:job_set(jid,'failed',f'{type(e).__name__}: {e}')
@app.post('/api/gifts/drafts')
def generate_gift_draft(v:GiftDraftIn,x_panel_token:str|None=Header(None)):
 require(x_panel_token);data=v.model_dump();jid=job_new('gift-offer',data);threaded(gift_draft_worker,jid,data);return {'job_id':jid}
@app.get('/api/gifts/drafts')
def gift_drafts(x_panel_token:str|None=Header(None)):
 require(x_panel_token);return q('SELECT id,market,item_id,status,title,description,price,image_url,funpay_type,plugin_id,funpay_offer_id,funpay_url,error,created,updated FROM gift_offer_drafts ORDER BY created DESC LIMIT 50')
@app.get('/api/gifts/drafts/{draft_id}')
def gift_draft(draft_id:str,x_panel_token:str|None=Header(None)):
 require(x_panel_token);rows=q('SELECT id,market,item_id,status,title,description,price,image_url,funpay_type,plugin_id,funpay_offer_id,funpay_url,error,created,updated FROM gift_offer_drafts WHERE id=?',(draft_id,))
 if not rows:raise HTTPException(404,'Черновик не найден')
 return rows[0]
class GiftPublishIn(BaseModel):
 confirm:bool=False;title:str|None=Field(None,max_length=100);description:str|None=Field(None,max_length=10000);price:int|None=Field(None,ge=1,le=10000000)
@app.post('/api/gifts/drafts/{draft_id}/publish')
def publish_gift_draft(draft_id:str,v:GiftPublishIn,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 if v.confirm is not True:raise HTTPException(409,'Для реальной публикации укажите confirm=true')
 rows=q('SELECT * FROM gift_offer_drafts WHERE id=?',(draft_id,))
 if not rows:raise HTTPException(404,'Черновик не найден')
 row=rows[0]
 if row['status']=='published':raise HTTPException(409,'Этот черновик уже опубликован')
 payload=json.loads(row['payload']);payload['confirmed']=True
 if v.description is not None:payload['listing_text']=v.description
 if v.price is not None:payload['sale_price']=v.price
 plugin_id,result=call_offer_plugin({'action':'publish_offer','item':payload});draft=normalize_offer_draft(result)
 if v.title is not None:draft['title_ru']=v.title.strip()[:100]
 if v.description is not None:draft['description_ru']=v.description.strip()
 if v.price is not None:draft['price']=v.price
 try:published=funpay.create_offer(draft)
 except Exception as e:
  ex('UPDATE gift_offer_drafts SET error=?,updated=CURRENT_TIMESTAMP WHERE id=?',(f'{type(e).__name__}: {e}'[:1000],draft_id));raise HTTPException(502,str(e)[:1000])
 ex('UPDATE gift_offer_drafts SET status=?,title=?,description=?,price=?,draft=?,plugin_id=?,funpay_offer_id=?,funpay_url=?,error=NULL,updated=CURRENT_TIMESTAMP WHERE id=?',('published',draft['title_ru'],draft['description_ru'],draft['price'],json.dumps(draft,ensure_ascii=False),plugin_id,str(published['offer_id']),published['url'],draft_id))
 ex('INSERT INTO marketplace_events(marketplace,event_type,entity_id,payload) VALUES(?,?,?,?)',('funpay','offer.created',str(published['offer_id']),json.dumps({'draft_id':draft_id,'source':draft.get('source')},ensure_ascii=False)))
 return {'ok':True,'draft_id':draft_id,**published}

class CredentialIn(BaseModel):
 secret:str=Field(min_length=1,max_length=100000)
@app.get('/api/marketplaces/credentials')
def credential_status(x_panel_token:str|None=Header(None)):require(x_panel_token);return credentials.all_status()
@app.put('/api/marketplaces/credentials/{marketplace}')
def credential_save(marketplace:str,v:CredentialIn,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 try:return credentials.save(marketplace,v.secret)
 except CredentialError as e:raise HTTPException(400,str(e))
 except Exception as e:raise HTTPException(502,str(e)[:1000])
@app.delete('/api/marketplaces/credentials/{marketplace}')
def credential_delete(marketplace:str,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 try:return credentials.delete(marketplace)
 except CredentialError as e:raise HTTPException(400,str(e))

@app.get('/api/logs')
def action_logs(limit:int=200,card_id:str|None=None,x_panel_token:str|None=Header(None)):
 require(x_panel_token);return cards_plugin.logs(limit,card_id)

class CardPatch(BaseModel):
 title:str|None=Field(None,max_length=100);description:str|None=Field(None,max_length=10000);sale_price:int|None=Field(None,ge=1,le=10000000);image_url:str|None=None
class CardAction(BaseModel):
 marketplaces:list[str]=Field(default_factory=list);confirm:bool=False
class MarketCfg(BaseModel):
 enabled:bool=False;settings:dict=Field(default_factory=dict)
class SoldIn(BaseModel):marketplace:str;external_id:str
@app.get('/api/cards')
def cards(x_panel_token:str|None=Header(None)):require(x_panel_token);return cards_plugin.all()
@app.get('/api/cards/{card_id}')
def card(card_id:str,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 try:return cards_plugin.one(card_id)
 except KeyError as e:raise HTTPException(404,str(e))
@app.post('/api/cards/sync')
def cards_sync(x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 try:return cards_plugin.sync()
 except Exception as e:raise HTTPException(502,str(e)[:1000])
@app.patch('/api/cards/{card_id}')
def card_update(card_id:str,v:CardPatch,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 try:return cards_plugin.update(card_id,v.model_dump())
 except Exception as e:raise HTTPException(400,str(e)[:1000])
@app.post('/api/cards/{card_id}/regenerate-text')
def card_text(card_id:str,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 try:return cards_plugin.text(card_id)
 except Exception as e:raise HTTPException(502,str(e)[:1000])
@app.post('/api/cards/{card_id}/regenerate-image')
def card_image(card_id:str,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 try:return cards_plugin.image(card_id)
 except Exception as e:raise HTTPException(502,str(e)[:1000])
@app.post('/api/cards/{card_id}/publish')
def card_publish(card_id:str,v:CardAction,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 if v.confirm is not True:raise HTTPException(409,'Требуется confirm=true')
 return cards_plugin.publish(card_id,v.marketplaces)
@app.post('/api/cards/{card_id}/delist')
def card_delist(card_id:str,v:CardAction,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 if v.confirm is not True:raise HTTPException(409,'Требуется confirm=true')
 return cards_plugin.delist(card_id,v.marketplaces)
@app.get('/api/marketplaces/settings')
def market_settings(x_panel_token:str|None=Header(None)):require(x_panel_token);return cards_plugin.settings()
@app.put('/api/marketplaces/settings/{marketplace}')
def market_settings_update(marketplace:str,v:MarketCfg,x_panel_token:str|None=Header(None)):
 require(x_panel_token)
 try:return cards_plugin.set_settings(marketplace,v.enabled,v.settings)
 except Exception as e:raise HTTPException(400,str(e)[:1000])
@app.post('/api/marketplaces/sold')
def marketplace_sold(v:SoldIn,x_panel_token:str|None=Header(None)):require(x_panel_token);return cards_plugin.sold(v.marketplace,v.external_id)

@app.get('/')
def index():return FileResponse(STATIC/'index.html')
@app.get('/favicon.ico',include_in_schema=False)
def favicon():return FileResponse(STATIC/'favicon.svg',media_type='image/svg+xml')
@app.get('/api/summary')
def summary():
 deals=q('SELECT * FROM deals ORDER BY created DESC LIMIT 50');fp=q("SELECT marketplace,order_id,status,title,price,amount,buyer_username,ordered_at,updated FROM marketplace_orders WHERE marketplace='funpay' ORDER BY COALESCE(ordered_at,updated) DESC LIMIT 50");return {'markets':market_health(),'deals':deals,'funpay_orders':fp,'active':sum(x['status']=='paid' for x in fp)+sum(x['status']!='closed' for x in deals),'profit':sum((x['sell']or 0)-(x['buy']or 0) for x in deals),'jobs':q("SELECT status,count(*) count FROM jobs GROUP BY status"),'plugins':q('SELECT * FROM plugins ORDER BY created DESC')}
class ChatIn(BaseModel):text:str=Field(min_length=1,max_length=20000)
@app.post('/api/chat')
def chat(v:ChatIn,x_panel_token:str|None=Header(None)):
 require(x_panel_token);knowledge='\n\n'.join(x['content'] for x in q('SELECT content FROM knowledge ORDER BY updated DESC LIMIT 20'))
 out=agent_core.model([{'role':'system','content':SYSTEM+'\n\nБаза знаний:\n'+knowledge},{'role':'user','content':v.text}],0.15);ex('INSERT INTO runs(input,output,status) VALUES(?,?,?)',(v.text,out,'done'));return {'output':out}
class RunIn(BaseModel):code:str=Field(min_length=1,max_length=200000);timeout:int=Field(30,ge=1,le=120);network:bool=False
def run_worker(jid,data):
 job_set(jid,'running','Запуск изолированной Python-среды');r=sandbox.run_code(jid,data['code'],data['timeout'],data['network']);job_set(jid,'passed' if r['ok'] else 'failed',r['output'],{'exit_code':r['exit_code'],'network':data['network']})
@app.post('/api/python/run')
def run_python(v:RunIn,x_panel_token:str|None=Header(None)):
 require(x_panel_token);data=v.model_dump();jid=job_new('python',data);threaded(run_worker,jid,data);return {'job_id':jid}
class BuildIn(BaseModel):
 goal:str=Field(min_length=10,max_length=30000)
 max_repairs:int=Field(3,ge=0,le=5)
HIDDEN_PLUGIN_TESTS='''import asyncio,unittest\nfrom plugin import Plugin\n\nclass PipelineContractTests(unittest.TestCase):\n def setUp(self): self.p=Plugin()\n def test_contract(self):\n  self.assertTrue(callable(getattr(self.p,"health",None)))\n  self.assertTrue(callable(getattr(self.p,"score",None)))\n  self.assertTrue(callable(getattr(self.p,"handle",None)))\n  self.assertIsInstance(self.p.health({}),dict)\n def test_malformed_score_never_raises(self):\n  for value in (None,"bad",1,[],object()): self.assertIsInstance(self.p.score(value),dict)\n def test_malformed_handle_never_raises(self):\n  for value in (None,"bad",1,[],object()): self.assertIsInstance(asyncio.run(self.p.handle(value,{})),dict)\n\nif __name__=="__main__": unittest.main()\n'''
def test_plugin_candidate(jid,spec):
 sandbox.write_files(jid,{**spec['files'],'pipeline_tests/test_contract.py':HIDDEN_PLUGIN_TESTS})
 checks=[]
 for label,argv,timeout in [
  ('compile',['/usr/bin/python3','-m','py_compile','plugin.py','tests/test_plugin.py','pipeline_tests/test_contract.py'],30),
  ('generated',['/usr/bin/python3','-m','unittest','discover','-s','tests','-v'],60),
  ('hidden',['/usr/bin/python3','-m','unittest','discover','-s','pipeline_tests','-v'],45),
 ]:
  r=sandbox.run(jid,argv,timeout,False);checks.append({'name':label,**r})
  if not r['ok'] and label=='compile':break
 output='\n\n'.join(f"===== {x['name'].upper()} =====\n{x['output']}" for x in checks)
 return {'ok':len(checks)==3 and all(x['ok'] for x in checks),'output':output[-30000:],'checks':checks}
def persist_plugin_files(target,files):
 import shutil
 if target.exists():shutil.rmtree(target)
 for name,content in files.items():
  dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(content,encoding='utf-8')
def build_worker(jid,goal,max_repairs=3):
 try:
  max_repairs=max(0,min(int(max_repairs),5));history=[]
  job_set(jid,'generating','Модель проектирует многфайловый плагин',{'attempt':0,'max_repairs':max_repairs})
  knowledge='\n\n'.join(x['content'] for x in q('SELECT content FROM knowledge ORDER BY updated DESC LIMIT 20'))
  spec,raw=agent_core.build_plugin(goal,knowledge);pid=uuid.uuid4().hex
  result=None
  for attempt in range(max_repairs+1):
   phase='testing' if attempt==0 else 'repairing'
   job_set(jid,phase,('Запуск первичных тестов' if attempt==0 else f'Исправление и повторная проверка {attempt}/{max_repairs}'),{'attempt':attempt,'max_repairs':max_repairs,'slug':spec['slug'],'history':history})
   result=test_plugin_candidate(jid,spec)
   item={'attempt':attempt,'ok':result['ok'],'checks':[{'name':x['name'],'ok':x['ok'],'exit_code':x['exit_code']} for x in result['checks']],'output':result['output'][-6000:]}
   history.append(item)
   if result['ok']:break
   if attempt>=max_repairs:break
   try:
    job_set(jid,'repairing',f'Агент анализирует traceback и исправляет код: {attempt+1}/{max_repairs}',{'attempt':attempt+1,'max_repairs':max_repairs,'slug':spec['slug'],'history':history})
    spec,repair_meta=agent_core.repair_plugin(goal,knowledge,spec,result['output'],attempt+1);history[-1]['repair']=repair_meta
   except Exception as repair_error:
    history[-1]['repair_error']=f'{type(repair_error).__name__}: {repair_error}'
    if attempt+1>=max_repairs:break
  target=PLUGINS/spec['slug']/pid;persist_plugin_files(target,spec['files'])
  status='active' if result and result['ok'] else 'rejected'
  final_output=(result['output'] if result else 'No test result')
  ex('INSERT INTO plugins(id,slug,title,description,status,path,test_output) VALUES(?,?,?,?,?,?,?)',(pid,spec['slug'],spec.get('title',spec['slug']),spec.get('description',''),status,str(target),final_output))
  job_set(jid,'passed' if status=='active' else 'failed',final_output,{'plugin_id':pid,'slug':spec['slug'],'files':list(spec['files']),'test_ok':status=='active','attempts':len(history),'repairs_used':max(0,len(history)-1),'history':history})
 except Exception as e:job_set(jid,'failed',f'{type(e).__name__}: {e}')
@app.post('/api/plugins/build')
def build(v:BuildIn,x_panel_token:str|None=Header(None)):
 require(x_panel_token);jid=job_new('plugin-build',v.model_dump());threaded(build_worker,jid,v.goal,v.max_repairs);return {'job_id':jid}
@app.get('/api/jobs')
def jobs(x_panel_token:str|None=Header(None)):
 require(x_panel_token);return q('SELECT * FROM jobs ORDER BY created DESC LIMIT 100')
@app.get('/api/jobs/{jid}')
def get_job(jid:str,x_panel_token:str|None=Header(None)):
 require(x_panel_token);r=q('SELECT * FROM jobs WHERE id=?',(jid,));
 if not r:raise HTTPException(404,'job not found')
 return r[0]
@app.get('/api/plugins')
def plugins(x_panel_token:str|None=Header(None)):
 require(x_panel_token);return q('SELECT * FROM plugins ORDER BY created DESC')
class KBIn(BaseModel):title:str=Field(min_length=1,max_length=200);content:str=Field(min_length=1,max_length=200000)
@app.get('/api/knowledge')
def knowledge(x_panel_token:str|None=Header(None)):
 require(x_panel_token);return q('SELECT * FROM knowledge ORDER BY updated DESC')
@app.post('/api/knowledge')
def add_knowledge(v:KBIn,x_panel_token:str|None=Header(None)):
 require(x_panel_token);return {'id':ex('INSERT INTO knowledge(title,content) VALUES(?,?)',(v.title,v.content))}
@app.post('/mcp')
def mcp(body:dict,x_panel_token:str|None=Header(None)):
 require(x_panel_token);p=body.get('params')or{};name=p.get('name');args=p.get('arguments')or{}
 tools=[{'name':'deals.list','description':'Последние сделки','inputSchema':{'type':'object'}},{'name':'marketplaces.health','description':'Статус площадок','inputSchema':{'type':'object'}},{'name':'funpay.status','description':'Проверить реальную авторизацию FunPay','inputSchema':{'type':'object'}},{'name':'funpay.orders','description':'Получить и синхронизировать заказы FunPay','inputSchema':{'type':'object'}},{'name':'funpay.send_message','description':'Отправить сообщение FunPay; требует confirm=true','inputSchema':{'type':'object','properties':{'text':{'type':'string'},'chat_id':{'type':'integer'},'username':{'type':'string'},'confirm':{'type':'boolean'}},'required':['text','confirm']}},{'name':'python.run','description':'Запустить Python в изолированной среде','inputSchema':{'type':'object','properties':{'code':{'type':'string'}},'required':['code']}},{'name':'plugins.build','description':'Создать и протестировать многфайловый плагин','inputSchema':{'type':'object','properties':{'goal':{'type':'string'}},'required':['goal']}}]
 if body.get('method')=='tools/list':res={'tools':tools}
 elif body.get('method')=='tools/call':
  if name=='marketplaces.health':data=market_health()
  elif name=='funpay.status':data=funpay.status(live=True)
  elif name=='funpay.orders':data=sync_funpay_orders()
  elif name=='funpay.send_message':
   if not args.get('confirm'):raise HTTPException(409,'confirm=true required')
   data=funpay.send_message(args['text'],chat_id=args.get('chat_id'),username=args.get('username'))
  elif name=='deals.list':data=q('SELECT * FROM deals ORDER BY created DESC LIMIT 50')
  elif name=='python.run':jid=job_new('python',args);threaded(run_worker,jid,{'code':args['code'],'timeout':30,'network':False});data={'job_id':jid}
  elif name=='plugins.build':jid=job_new('plugin-build',args);threaded(build_worker,jid,args['goal'],args.get('max_repairs',3));data={'job_id':jid}
  else:raise HTTPException(404,'unknown tool')
  res={'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}]}
 else:raise HTTPException(400,'unknown method')
 return {'jsonrpc':'2.0','id':body.get('id'),'result':res}
