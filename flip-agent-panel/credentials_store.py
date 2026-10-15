from __future__ import annotations
import base64,html,json,os,re,time,requests
from pathlib import Path

class CredentialError(ValueError):pass
class CredentialStore:
 def __init__(self,base,funpay):
  self.base=Path(base);self.base.mkdir(parents=True,exist_ok=True);os.chmod(self.base,0o700);self.funpay=funpay
  migrations={'funpay':('FUNPAY_GOLDEN_KEY','golden_key'),'ggsel':('GGSEL_API_KEY','api_token')}
  for m,(envkey,field) in migrations.items():
   value=os.getenv(envkey)
   if value and not self.path(m).exists():
    p=self.path(m);p.write_text(json.dumps({field:value},ensure_ascii=False));os.chmod(p,0o600)
  if os.getenv('PLAYEROK_COOKIES') and not self.path('playerok').exists():
   cookies=self.parse_cookies(os.environ['PLAYEROK_COOKIES'])
   if cookies.get('token'):
    p=self.path('playerok');p.write_text(json.dumps({'cookies':cookies,'cookie_header':'; '.join(f'{k}={v}' for k,v in cookies.items()),'jwt':self.jwt_meta(cookies['token'])},ensure_ascii=False));os.chmod(p,0o600)
 def path(self,m):
  if m not in ('funpay','ggsel','ggsel_office','playerok'):raise CredentialError('Неизвестная площадка')
  return self.base/f'{m}.json'
 @staticmethod
 def parse_cookies(raw):
  raw=html.unescape(str(raw or '')).replace('Cookie:','').replace('cookie:','').replace('\\_','_').replace('\n',';')
  out={}
  for part in raw.split(';'):
   part=part.strip()
   if not part or '=' not in part:continue
   k,v=part.split('=',1);k=k.strip().strip('* ');v=v.strip()
   # Repair rich-text emphasis around ddg/ttp cookie names without touching values.
   k=k.replace('*','').replace('<i>','').replace('</i>','')
   if re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',k) and v:out[k]=v
  return out
 @staticmethod
 def jwt_meta(token):
  try:
   payload=token.split('.')[1];payload+='='*(-len(payload)%4);d=json.loads(base64.urlsafe_b64decode(payload));return {'subject':d.get('sub'),'expires_at':d.get('exp'),'expired':bool(d.get('exp') and d['exp']<time.time())}
  except Exception:return {'subject':None,'expires_at':None,'expired':None}
 def load(self,m):
  p=self.path(m)
  if not p.exists():return None
  return json.loads(p.read_text())
 def save(self,m,secret):
  secret=str(secret or '').strip()
  if m=='funpay':
   if len(secret)<20 or any(x.isspace() for x in secret):raise CredentialError('Некорректный golden_key')
   data={'golden_key':secret}
  elif m=='ggsel':
   secret=re.sub(r'^Bearer\s+','',secret,flags=re.I).strip()
   if len(secret)<8:raise CredentialError('Некорректный API token')
   data={'api_token':secret}
  elif m=='ggsel_office':
   cookies=self.parse_cookies(secret)
   if not cookies:raise CredentialError('В строке не найдены cookies')
   data={'cookies':cookies,'cookie_header':'; '.join(f'{k}={v}' for k,v in cookies.items())}
  elif m=='playerok':
   cookies=self.parse_cookies(secret)
   if not cookies.get('token'):raise CredentialError('В cookies не найден token')
   data={'cookies':cookies,'cookie_header':'; '.join(f'{k}={v}' for k,v in cookies.items()),'jwt':self.jwt_meta(cookies['token'])}
  else:raise CredentialError('Неизвестная площадка')
  p=self.path(m);tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False));os.chmod(tmp,0o600);tmp.replace(p);os.chmod(p,0o600);self.apply(m);return self.status(m,live=(m in ('funpay','ggsel_office')))
 def apply(self,m):
  d=self.load(m)
  if not d:return
  if m=='funpay':
   os.environ['FUNPAY_GOLDEN_KEY']=d['golden_key'];self.funpay._account=None;self.funpay._write_session=None;self.funpay._connected_at=0
  elif m=='ggsel':os.environ['GGSEL_API_KEY']=d['api_token']
  elif m=='ggsel_office':os.environ['GGSEL_OFFICE_COOKIES']=d['cookie_header']
  else:
   os.environ['PLAYEROK_TOKEN']=d['cookies']['token'];os.environ['PLAYEROK_COOKIES']=d['cookie_header']
 def apply_all(self):
  for m in ('funpay','ggsel','ggsel_office','playerok'):
   try:self.apply(m)
   except Exception:pass
 def delete(self,m):
  p=self.path(m)
  if p.exists():p.unlink()
  if m=='funpay':os.environ.pop('FUNPAY_GOLDEN_KEY',None);self.funpay._account=None;self.funpay._write_session=None
  elif m=='ggsel':os.environ.pop('GGSEL_API_KEY',None)
  elif m=='ggsel_office':os.environ.pop('GGSEL_OFFICE_COOKIES',None)
  else:os.environ.pop('PLAYEROK_TOKEN',None);os.environ.pop('PLAYEROK_COOKIES',None)
  return self.status(m)
 def status(self,m,live=False):
  d=self.load(m)
  if not d:return {'marketplace':m,'saved':False,'connected':False,'detail':'Данные не сохранены'}
  if m=='funpay':
   r=self.funpay.status(live=live);return {'marketplace':m,'saved':True,'connected':bool(r.get('connected')),'detail':r.get('detail'),'account_id':r.get('account_id'),'username':r.get('username')}
  if m=='ggsel':return {'marketplace':m,'saved':True,'connected':None,'detail':'Seller API token сохранён','masked':'••••'+d['api_token'][-4:]}
  if m=='ggsel_office':
   c=d.get('cookies',{});connected=None;detail='Cookies Seller Office сохранены'
   if live:
    try:
     r=requests.get('https://seller.ggsel.com/api/v1/users/me',headers={'Cookie':d.get('cookie_header',''),'Accept':'application/json','User-Agent':'Mozilla/5.0'},timeout=25)
     connected=r.status_code==200;detail='Seller Office подключён' if connected else f'Seller Office HTTP {r.status_code}'
    except Exception as e:connected=False;detail=f'Ошибка проверки: {type(e).__name__}'
   return {'marketplace':m,'saved':True,'connected':connected,'detail':detail,'cookie_count':len(c),'cookie_names':sorted(c)}
  c=d.get('cookies',{});meta=self.jwt_meta(c.get('token',''));return {'marketplace':m,'saved':True,'connected':False if meta.get('expired') else None,'detail':'Cookies разобраны' if not meta.get('expired') else 'Token истёк','cookie_count':len(c),'cookie_names':sorted(c),'has_token':bool(c.get('token')),'ddg_cookies':sorted(k for k in c if 'ddg' in k.lower()),'expires_at':meta.get('expires_at')}
 def all_status(self):return [self.status(m) for m in ('funpay','ggsel','ggsel_office','playerok')]
