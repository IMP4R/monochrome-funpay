import base64,io,json,os,re,requests,time
from PIL import Image,ImageFilter,ImageOps
class GGselAdapter:
 def __init__(self):self.base='https://seller.ggsel.com/api_sellers/v2'
 def _key(self):
  try:return json.load(open('/root/flip-agent-panel/secrets/ggsel.json'))['api_token']
  except:return os.getenv('GGSEL_API_KEY','')
 def _h(self):return {'Authorization':self._key(),'Accept':'application/json','Content-Type':'application/json'}
 def _cover(self,url):
  if not url:raise RuntimeError('Для GGsel нужна обложка')
  if url.startswith('/'):url='http://127.0.0.1:8091'+url
  r=requests.get(url,timeout=45);r.raise_for_status()
  im=ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert('RGB')
  side=min(im.size);left=(im.width-side)//2;top=(im.height-side)//2
  im=im.crop((left,top,left+side,top+side)).resize((1200,1200),Image.Resampling.LANCZOS)
  im=im.filter(ImageFilter.UnsharpMask(radius=1.2,percent=115,threshold=2))
  out=io.BytesIO();im.save(out,format='JPEG',quality=96,subsampling=0,optimize=True,progressive=True)
  return 'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode()
 def _get(self,oid):
  r=requests.get(self.base+f'/offers/{oid}',headers=self._h(),timeout=30)
  if not r.ok:raise RuntimeError(f'GGsel get HTTP {r.status_code}: {r.text[:1000]}')
  return r.json().get('data') or r.json()
 def create_offer(self,card,cfg):
  if not self._key():raise RuntimeError('GGsel API token не задан')
  cat=int(cfg.get('category_id') or 145517);cover=self._cover(card.get('image_url'))
  en=f"Collectible Telegram gift: {card.get('collection') or card.get('model') or card['title']}. Model: {card.get('model') or 'original'}. Background: {card.get('backdrop') or 'original'}. After payment, send the recipient's exact @username."
  body={'category_id':cat,'title_ru':card['title'][:120],'title_en':card['title'][:120],'description_ru':card['description'],'description_en':en,'instructions_ru':'После оплаты отправьте точный @username получателя в чат заказа.','instructions_en':"After payment, send the recipient's exact @username in the order chat.",'cover_image_ru':cover,'cover_image_en':cover,'price':float(card['sale_price']),'currency':'RUB','quantity':1,'min_quantity':1,'max_quantity':1,'delivery':'manual','is_autoselling':False,'is_unlimited_quantity':False}
  r=requests.post(self.base+'/offers',headers=self._h(),json=body,timeout=90)
  if r.status_code not in (200,201):raise RuntimeError(f'GGsel create HTTP {r.status_code}: {r.text[:1200]}')
  d=r.json().get('data') or r.json();oid=str(d['id'])
  a=requests.post(self.base+'/offers/batch_activate',headers=self._h(),json={'offer_ids':[int(oid)]},timeout=30)
  if a.status_code!=200:raise RuntimeError(f'GGsel activate HTTP {a.status_code}: {a.text[:1000]}')
  z={}
  for _ in range(25):
   time.sleep(1);z=self._get(oid)
   if z.get('status')=='active' and z.get('cover_image_ru_url'):break
  if z.get('status')!='active':raise RuntimeError(f"GGsel offer not active: {z.get('status')}")
  if not z.get('cover_image_ru_url'):raise RuntimeError('GGsel не применил обложку')
  seller_id=str(cfg.get('seller_id') or '0')
  verify_url='https://ggsel.net/en/sellers/'+seller_id
  public_verified=False
  # Публичная страница GGsel индексируется с задержкой и не является источником истины.
  # Ошибка/кэш страницы не должны превращать уже созданный active offer в локальную ошибку.
  for _ in range(12):
   try:
    q=requests.get(verify_url,headers={'User-Agent':'Mozilla/5.0'},timeout=45)
    public_verified=q.status_code==200 and str(oid) in q.text
    if public_verified:break
   except requests.RequestException:pass
   time.sleep(2)
  public='https://ggsel.net/sellers/'+seller_id
  return {'offer_id':oid,'url':public,'status':'active','cover_url':z.get('cover_image_ru_url'),'public_verified':public_verified}
 def find_offers(self,card,active_only=True):
  r=requests.get(self.base+'/offers',headers=self._h(),timeout=45)
  if not r.ok:raise RuntimeError(f'GGsel list HTTP {r.status_code}: {r.text[:1000]}')
  data=r.json().get('data') or []
  model=str(card.get('model') or '').casefold();backdrop=str(card.get('backdrop') or '').casefold()
  out=[]
  for offer in data:
   text=' '.join(str(offer.get(k) or '') for k in ('title_ru','title_en','description_ru','description_en')).casefold()
   if model and backdrop and model in text and backdrop in text and (not active_only or offer.get('status')=='active'):out.append(offer)
  return sorted(out,key=lambda x:int(x.get('id') or 0),reverse=True)
 def remove_offer(self,oid):
  oid=int(oid)
  r=requests.post(self.base+'/offers/batch_delete',headers=self._h(),json={'offer_ids':[oid]},timeout=30)
  if r.status_code!=200:raise RuntimeError(f'GGsel delete HTTP {r.status_code}: {r.text[:1000]}')
  # Проверяем снятие через Seller API. Публичная страница может оставаться в кэше.
  last=None
  for _ in range(15):
   q=requests.get(self.base+f'/offers/{oid}',headers=self._h(),timeout=30)
   if q.status_code==404:return {'removed':True,'offer_id':str(oid),'verified':True}
   if not q.ok:raise RuntimeError(f'GGsel verify delete HTTP {q.status_code}: {q.text[:1000]}')
   last=q.json().get('data') or q.json()
   if last.get('status')!='active':return {'removed':True,'offer_id':str(oid),'verified':True,'status':last.get('status')}
   time.sleep(1)
  raise RuntimeError(f"GGsel offer {oid} всё ещё active после удаления: {last.get('status') if last else 'unknown'}")
ggsel=GGselAdapter()
