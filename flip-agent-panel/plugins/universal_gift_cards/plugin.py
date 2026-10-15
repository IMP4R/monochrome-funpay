import hashlib,json,os,re,sqlite3,uuid,threading,time
from ggsel_adapter import ggsel
from playerok_adapter import playerok
from pricing import calculate_sale_price,coingecko_ton_rub
class GiftCardsPlugin:
 M=('funpay','ggsel','playerok')
 def __init__(self,db_path,mono,funpay,img,kind):self.path=str(db_path);self.mono=mono;self.funpay=funpay;self.img=img;self.kind=kind;self._generation_lock=threading.Lock()
 def db(self):c=sqlite3.connect(self.path,timeout=30);c.row_factory=sqlite3.Row;return c
 def init(self):
  c=self.db();c.executescript('''CREATE TABLE IF NOT EXISTS universal_gift_cards(id TEXT PRIMARY KEY,source_market TEXT,source_item_id TEXT,collection TEXT,model TEXT,backdrop TEXT,symbol TEXT,source_price REAL,title TEXT,description TEXT,image_url TEXT,sale_price INTEGER,status TEXT DEFAULT 'ready',raw TEXT DEFAULT '{}',text_version INTEGER DEFAULT 1,image_version INTEGER DEFAULT 1,created TEXT DEFAULT CURRENT_TIMESTAMP,updated TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(source_market,source_item_id));CREATE TABLE IF NOT EXISTS gift_card_listings(id TEXT PRIMARY KEY,card_id TEXT,marketplace TEXT,external_id TEXT,url TEXT,status TEXT,error TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,updated TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(card_id,marketplace));CREATE TABLE IF NOT EXISTS marketplace_settings(marketplace TEXT PRIMARY KEY,enabled INTEGER,settings TEXT,updated TEXT DEFAULT CURRENT_TIMESTAMP);''')
  c.execute("CREATE TABLE IF NOT EXISTS marketplace_action_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,card_id TEXT,marketplace TEXT,action TEXT,status TEXT,detail TEXT,external_id TEXT,url TEXT,payload TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)")
  cols={x['name'] for x in c.execute('PRAGMA table_info(universal_gift_cards)')}
  for name,sql in [('text_generated','INTEGER NOT NULL DEFAULT 0'),('image_generated','INTEGER NOT NULL DEFAULT 0'),('generation_state',"TEXT NOT NULL DEFAULT 'idle'"),('generation_error','TEXT'),('price_auto','INTEGER NOT NULL DEFAULT 1'),('price_rate','REAL'),('price_calculated_at','TEXT')]:
   if name not in cols:c.execute(f'ALTER TABLE universal_gift_cards ADD COLUMN {name} {sql}')
  c.execute("UPDATE universal_gift_cards SET image_generated=1 WHERE COALESCE(json_extract(raw,'$.generated_url'),'')!=''")
  ds={'funpay':{'category_id':3064,'markup':0,'auto_delist':True},'ggsel':{'category_id':'','markup':0,'auto_delist':True},'playerok':{'category_id':'','markup':0,'auto_delist':True}}
  for m in self.M:c.execute('INSERT OR IGNORE INTO marketplace_settings(marketplace,enabled,settings) VALUES(?,?,?)',(m,1 if m=='funpay' else 0,json.dumps(ds[m])))
  c.commit();c.close()
 def sync(self):
  d=self.mono.inventory('all');c=self.db();new=upd=0;rate=coingecko_ton_rub()
  for x in d.get('items',[]):
   market=str(x.get('market') or 'unknown');iid=str(x.get('id') or '')
   if not iid:continue
   cid=hashlib.sha256(f'{market}:{iid}'.encode()).hexdigest()[:24];ton=float(x.get('price') if x.get('price') is not None else x.get('floor') or 0);price=calculate_sale_price(ton,rate);title=f"MONOCHROME · {x.get('model') or x.get('collection') or 'Подарок'} ({x.get('backdrop') or 'без фона'})"[:100];image=self.img(x.get('generated_url') or x.get('image_url') or x.get('model_img') or '');desc=f"Коллекционный Telegram-подарок {x.get('collection') or ''} · {x.get('model') or ''}. Фон: {x.get('backdrop') or ''}. После оплаты отправьте точный @username получателя."
   if c.execute('SELECT 1 FROM universal_gift_cards WHERE id=?',(cid,)).fetchone():c.execute('UPDATE universal_gift_cards SET collection=?,model=?,backdrop=?,symbol=?,source_price=?,sale_price=CASE WHEN COALESCE(price_auto,1)=1 THEN ? ELSE sale_price END,price_rate=CASE WHEN COALESCE(price_auto,1)=1 THEN ? ELSE price_rate END,price_calculated_at=CASE WHEN COALESCE(price_auto,1)=1 THEN CURRENT_TIMESTAMP ELSE price_calculated_at END,raw=?,image_url=CASE WHEN image_url IS NULL OR image_url="" THEN ? ELSE image_url END,updated=CURRENT_TIMESTAMP WHERE id=?',(x.get('collection'),x.get('model'),x.get('backdrop'),x.get('symbol'),ton,price,rate,json.dumps(x,ensure_ascii=False,default=str),image,cid));upd+=1
   else:c.execute('INSERT INTO universal_gift_cards(id,source_market,source_item_id,collection,model,backdrop,symbol,source_price,title,description,image_url,sale_price,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(cid,market,iid,x.get('collection'),x.get('model'),x.get('backdrop'),x.get('symbol'),ton,title,desc,image,price,json.dumps(x,ensure_ascii=False,default=str)));new+=1
  c.commit();c.close();self.ensure_async();return {'ok':True,'total':len(d.get('items',[])),'created':new,'updated':upd,'generation_started':True}
 def one(self,id):
  c=self.db();r=c.execute('SELECT * FROM universal_gift_cards WHERE id=?',(id,)).fetchone()
  if not r:c.close();raise KeyError('Карточка не найдена')
  z=dict(r);z['raw']=json.loads(z['raw'] or '{}');z['listings']=[dict(a) for a in c.execute('SELECT marketplace,external_id,url,status,error,updated FROM gift_card_listings WHERE card_id=?',(id,))];c.close();return z
 def all(self):
  c=self.db();ids=[x['id'] for x in c.execute("SELECT id FROM universal_gift_cards ORDER BY CASE status WHEN 'listed' THEN 0 WHEN 'sold' THEN 2 ELSE 1 END,updated DESC")];c.close();return [self.one(x) for x in ids]
 def update(self,id,d):
  current=self.one(id);a=[];v=[]
  for k in ('title','description','sale_price','image_url'):
   if d.get(k) is not None:a.append(k+'=?');v.append(d[k])
  if d.get('sale_price') is not None and int(d['sale_price'])!=int(current['sale_price']):a.append('price_auto=?');v.append(0)
  if a:c=self.db();c.execute('UPDATE universal_gift_cards SET '+','.join(a)+',updated=CURRENT_TIMESTAMP WHERE id=?',v+[id]);c.commit();c.close()
  return self.one(id)
 def text(self,id):
  x=self.one(id);raw=x.get('raw') or {}
  from agent_core import model,one_json
  expected=f"{{color emoji}}{{theme emoji}} MONOCHROME · {x.get('model')} ({x.get('backdrop')})"
  base=(f"Коллекция: {x.get('collection')}; модель: {x.get('model')}; фон: {x.get('backdrop')}; "
        f"HEX фона: {raw.get('backdrop_hex')}; символ: {x.get('symbol')}. Шаблон title: {expected}. "
        "Не меняй имена модели и фона. Pitcher в Liberty Figure — бейсбольный питчер, не кувшин.")
  rules=("Верни ТОЛЬКО JSON с единственным ключом title. title максимум 90 символов, обязательно MONOCHROME и ровно 2 эмодзи в начале. "
         "Первый эмодзи — цветовой круг/сердце максимально близкий к HEX фона; второй — тема модели или символа. "
         "Не генерируй description. Не используй случайные огонь, ракету или блёстки.")
  last='';title=''
  for attempt in range(3):
   try:
    obj=one_json(model([{'role':'system','content':'Ты арт-директор названий коллекционных Telegram-подарков. Генерируй только короткий title и осмысленные эмодзи.'},{'role':'user','content':base+'\n'+rules+(f"\nПредыдущая ошибка: {last}. Исправь JSON." if last else '')}],0.3,350))
    title=str(obj.get('title') or '').strip();emojis=re.findall(r'[\U0001F300-\U0001FAFF\u2600-\u27BF]',title)
    if len(title)>90 or 'MONOCHROME' not in title.upper():raise ValueError('title должен содержать MONOCHROME и быть до 90 символов')
    if len(emojis)!=2 or not title.startswith(''.join(emojis[:2])):raise ValueError('в начале title должно быть ровно 2 эмодзи')
    if str(x.get('model')) not in title or str(x.get('backdrop')) not in title:raise ValueError('title должен сохранить model и backdrop')
    break
   except Exception as e:last=str(e);title=''
  if not title:raise RuntimeError('Gemini не сформировал корректное название: '+last)
  r=self.mono.listing_text(x['source_market'],x['source_item_id'],x['sale_price'],False)
  t=str(r.get('text') or '').strip()
  if not t:raise RuntimeError('Старая генерация не вернула описание')
  c=self.db();c.execute("UPDATE universal_gift_cards SET title=?,description=?,text_version=text_version+1,text_generated=1,generation_state='idle',generation_error=NULL,updated=CURRENT_TIMESTAMP WHERE id=?",(title,t,id));c.commit();c.close();return self.one(id)
 def image(self,id):
  x=self.one(id);r=self.mono.generate(x['source_market'],x['source_item_id']);u=self.img(r.get('url') or '')
  if not u:raise RuntimeError('Изображение не получено')
  c=self.db();c.execute("UPDATE universal_gift_cards SET image_url=?,image_version=image_version+1,image_generated=1,generation_state='idle',generation_error=NULL,updated=CURRENT_TIMESTAMP WHERE id=?",(u,id));c.commit();c.close();return self.one(id)
 def ensure_async(self):
  if not self._generation_lock.locked():threading.Thread(target=self.ensure_missing,daemon=True).start()
 def ensure_missing(self):
  if not self._generation_lock.acquire(False):return
  try:
   for attempt in range(3):
    c=self.db();rows=[dict(x) for x in c.execute("SELECT id,text_generated,image_generated FROM universal_gift_cards WHERE text_generated=0 OR image_generated=0 ORDER BY created")];c.close()
    if not rows:break
    for row in rows:
     try:
      c=self.db();c.execute("UPDATE universal_gift_cards SET generation_state='generating',generation_error=NULL WHERE id=?",(row['id'],));c.commit();c.close()
      fresh=self.one(row['id'])
      if not fresh['text_generated']:self.text(row['id'])
      fresh=self.one(row['id'])
      if not fresh['image_generated']:self.image(row['id'])
     except Exception as e:
      c=self.db();c.execute("UPDATE universal_gift_cards SET generation_state='error',generation_error=? WHERE id=?",(f'{type(e).__name__}: {e}'[:1000],row['id']));c.commit();c.close()
    if attempt<2:time.sleep(5*(attempt+1))
  finally:self._generation_lock.release()
 def settings(self):
  c=self.db();out=[]
  for r in c.execute('SELECT * FROM marketplace_settings ORDER BY marketplace'):
   z=dict(r);z['enabled']=bool(z['enabled']);z['settings']=json.loads(z['settings'] or '{}');z['connected']=self.funpay.status(False).get('connected',False) if z['marketplace']=='funpay' else bool(os.getenv('GGSEL_API_KEY') if z['marketplace']=='ggsel' else os.getenv('PLAYEROK_TOKEN'));out.append(z)
  c.close();return out
 def set_settings(self,m,en,cfg):
  if m not in self.M:raise ValueError('Неизвестный магазин')
  c=self.db();c.execute('INSERT INTO marketplace_settings(marketplace,enabled,settings) VALUES(?,?,?) ON CONFLICT(marketplace) DO UPDATE SET enabled=excluded.enabled,settings=excluded.settings,updated=CURRENT_TIMESTAMP',(m,int(en),json.dumps(cfg,ensure_ascii=False)));c.commit();c.close();return next(x for x in self.settings() if x['marketplace']==m)
 def _log(self,card_id,marketplace,action,status,detail='',external_id=None,url=None,payload=None):
  c=self.db();c.execute('INSERT INTO marketplace_action_logs(card_id,marketplace,action,status,detail,external_id,url,payload) VALUES(?,?,?,?,?,?,?,?)',(card_id,marketplace,action,status,str(detail or '')[:2000],str(external_id) if external_id is not None else None,url,json.dumps(payload or {},ensure_ascii=False,default=str)));c.commit();c.close()
 def logs(self,limit=200,card_id=None):
  c=self.db();sql='SELECT id,card_id,marketplace,action,status,detail,external_id,url,created FROM marketplace_action_logs';args=[]
  if card_id:sql+=' WHERE card_id=?';args.append(card_id)
  sql+=' ORDER BY id DESC LIMIT ?';args.append(max(1,min(int(limit),500)));rows=[dict(x) for x in c.execute(sql,args)];c.close();return rows
 def _listing_error(self,card_id,marketplace,error):
  c=self.db();c.execute("INSERT INTO gift_card_listings(id,card_id,marketplace,status,error) VALUES(?,?,?,'error',?) ON CONFLICT(card_id,marketplace) DO UPDATE SET status=CASE WHEN gift_card_listings.status='active' THEN 'active' ELSE 'error' END,error=excluded.error,updated=CURRENT_TIMESTAMP",(uuid.uuid4().hex,card_id,marketplace,str(error)[:2000]));c.commit();c.close()
 def publish(self,id,markets):
  card=self.one(id);cfg={x['marketplace']:x for x in self.settings()};res=[]
  if not markets:
   self._log(id,'none','publish','error','Не выбрана площадка');return {'ok':False,'results':[{'marketplace':'none','ok':False,'error':'Не выбрана площадка'}],'card':self.one(id)}
  for m in markets:
   try:
    if m not in cfg:raise RuntimeError('Неизвестная площадка')
    card=self.one(id);old=next((x for x in card['listings'] if x['marketplace']==m and x['status']=='active'),None)
    if old:self._log(id,m,'publish','reused','Активная публикация уже существует',old.get('external_id'),old.get('url'));res.append({'marketplace':m,'ok':True,'existing':True,**old});continue
    if not cfg[m]['enabled']:raise RuntimeError('Магазин выключен')
    en=f"Collectible Telegram gift: {card.get('collection') or card.get('model') or card['title']}. Model: {card.get('model') or 'original'}. Background: {card.get('backdrop') or 'original'}. After payment, send the recipient's exact @username in chat."
    if m=='funpay':out=self.funpay.create_offer({'node_id':int(cfg[m]['settings'].get('category_id') or 3064),'funpay_type':self.kind(card['raw']),'title_ru':card['title'],'title_en':card['title'],'description_ru':card['description'],'description_en':en,'price':card['sale_price'],'image_url':card.get('image_url')})
    elif m=='ggsel':out=ggsel.create_offer(card,cfg[m]['settings'])
    elif m=='playerok':out=playerok.create_offer(card,cfg[m]['settings'])
    else:raise RuntimeError('Неизвестная площадка')
    c=self.db();c.execute("INSERT INTO gift_card_listings(id,card_id,marketplace,external_id,url,status,error) VALUES(?,?,?,?,?,'active',NULL) ON CONFLICT(card_id,marketplace) DO UPDATE SET external_id=excluded.external_id,url=excluded.url,status='active',error=NULL,updated=CURRENT_TIMESTAMP",(uuid.uuid4().hex,id,m,str(out['offer_id']),out['url']));c.execute("UPDATE universal_gift_cards SET status='listed',updated=CURRENT_TIMESTAMP WHERE id=?",(id,));c.commit();c.close();self._log(id,m,'publish','success','Товар опубликован',out['offer_id'],out['url']);res.append({'marketplace':m,'ok':True,'external_id':str(out['offer_id']),'url':out['url']})
   except Exception as e:
    error=f'{type(e).__name__}: {e}';self._listing_error(id,m,error);self._log(id,m,'publish','error',error);res.append({'marketplace':m,'ok':False,'error':error})
  return {'ok':all(x['ok'] for x in res),'results':res,'card':self.one(id)}
 def delist(self,id,markets=None,sold=False):
  card=self.one(id);rows=[x for x in card['listings'] if x['status'] in ('active','error') and x.get('external_id') and (not markets or x['marketplace'] in markets)];res=[]
  if not rows:self._log(id,','.join(markets or []) or 'all','delist','skipped','Активные публикации не найдены')
  for x in rows:
   try:
    if x['marketplace']=='funpay':self.funpay.remove_offer(x['external_id'])
    elif x['marketplace']=='ggsel':ggsel.remove_offer(x['external_id'])
    elif x['marketplace']=='playerok':playerok.remove_offer(x['external_id'])
    else:raise RuntimeError('Неизвестная площадка')
    c=self.db();c.execute('UPDATE gift_card_listings SET status=?,error=NULL,updated=CURRENT_TIMESTAMP WHERE card_id=? AND marketplace=?',('sold' if sold else 'removed',id,x['marketplace']));c.commit();c.close();self._log(id,x['marketplace'],'delist','success','Товар снят',x.get('external_id'),x.get('url'));res.append({'marketplace':x['marketplace'],'ok':True})
   except Exception as e:
    error=f'{type(e).__name__}: {e}';c=self.db();c.execute('UPDATE gift_card_listings SET error=?,updated=CURRENT_TIMESTAMP WHERE card_id=? AND marketplace=?',(error,id,x['marketplace']));c.commit();c.close();self._log(id,x['marketplace'],'delist','error',error,x.get('external_id'),x.get('url'));res.append({'marketplace':x['marketplace'],'ok':False,'error':error})
  c=self.db();left=c.execute("SELECT count(*) FROM gift_card_listings WHERE card_id=? AND status='active'",(id,)).fetchone()[0];c.execute('UPDATE universal_gift_cards SET status=?,updated=CURRENT_TIMESTAMP WHERE id=?',('sold' if sold else ('listed' if left else 'ready'),id));c.commit();c.close();return {'ok':all(x['ok'] for x in res),'results':res,'card':self.one(id)}
 def sold(self,m,external,slug=None):
  c=self.db()
  if m=='playerok' and slug:
   r=c.execute("SELECT card_id,status FROM gift_card_listings WHERE marketplace=? AND (external_id=? OR url LIKE ?) ORDER BY updated DESC LIMIT 1",(m,str(external or ''),'%/'+str(slug))).fetchone()
  else:r=c.execute('SELECT card_id,status FROM gift_card_listings WHERE marketplace=? AND external_id=?',(m,str(external))).fetchone()
  if not r:c.close();return {'ok':False,'matched':False}
  id=r['card_id']
  if r['status']=='sold':c.close();return {'ok':True,'matched':True,'already_processed':True,'card':self.one(id)}
  c.execute("UPDATE gift_card_listings SET status='sold',error=NULL,updated=CURRENT_TIMESTAMP WHERE card_id=? AND marketplace=?",(id,m));c.commit();c.close();return {'matched':True,**self.delist(id,[x for x in self.M if x!=m],True)}
