#!/usr/bin/env python3
import json,os,re,subprocess,time
from pathlib import Path
from urllib.parse import parse_qs,unquote,urlsplit
from curl_cffi import requests
BASE=Path('/root/monochrome');OUT=BASE/'sessions/inventory/status.json';MRKT_ROOT=BASE/'sessions/mrkt-buyer';TONNEL=BASE/'sessions/tonnel-reader';API_MRKT='https://api.tgmrkt.io/api/v1';API_PORTALS='https://portal-market.com/api'
UA='Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/127.0.0.1 Mobile Safari/537.36'
def atomic(p,d):
 p.parent.mkdir(parents=True,exist_ok=True);os.chmod(p.parent,0o700);t=p.with_suffix('.tmp');t.write_text(json.dumps(d,ensure_ascii=False,indent=2));os.chmod(t,0o600);t.replace(p)
def clean(v):return re.sub(r'\s*\([^)]*%\)\s*$','',str(v or '')).strip()
def attrs(x):return {str(a.get('type') or '').lower():a.get('value') for a in x.get('attributes') or []}
def mrkt_headers(token):return {'Accept':'application/json, text/plain, */*','Origin':'https://cdn.tgmrkt.io','Referer':'https://cdn.tgmrkt.io/','User-Agent':UA,'Authorization':token,'Cookie':'access_token='+token}
def mrkt_page(sess,token,listed,cursor=''):
 payload={'collectionNames':[],'modelNames':[],'backdropNames':[],'symbolNames':[],'lowToHigh':False,'maxPrice':None,'minPrice':None,'mintable':None,'number':None,'count':50,'cursor':cursor,'query':None,'promotedFirst':False,'isListed':listed}
 r=sess.post(API_MRKT+'/gifts',headers=mrkt_headers(token),json=payload,timeout=25)
 if r.status_code==401:
  import mrkt_buyer;token=mrkt_buyer.refresh_token(sess);r=sess.post(API_MRKT+'/gifts',headers=mrkt_headers(token),json=payload,timeout=25)
 r.raise_for_status();return r.json(),token
def mrkt_items(sess):
 token=json.loads((MRKT_ROOT/'mrkt_token.json').read_text())['token'];out=[]
 for listed in (True,False):
  cursor=''
  for _ in range(20):
   data,token=mrkt_page(sess,token,listed,cursor);batch=data.get('gifts') or []
   for x in batch:
    lid=str(x.get('id') or x.get('giftIdString') or x.get('giftId'));nano=x.get('salePrice');out.append({'market':'mrkt','id':lid,'collection':x.get('collectionName') or x.get('title'),'number':x.get('number'),'model':x.get('modelName'),'backdrop':x.get('backdropName'),'symbol':x.get('symbolName'),'status':'listed' if x.get('isOnSale') else 'inventory','price':round(float(nano)/1e9,6) if nano else None,'floor':round(float(x.get('floorPriceNanoTONsByCollection') or 0)/1e9,6) or None,'image_url':('https://cdn.tgmrkt.io/'+str(x.get('modelStickerThumbnailKey')).lstrip('/')) if x.get('modelStickerThumbnailKey') else None,'gift_url':'https://t.me/mrkt/app?startapp='+lid.replace('-','')})
   cursor=data.get('cursor') or ''
   if not cursor or not batch:break
 return out
def portal_headers():return {'Authorization':(BASE/'.auth').read_text().strip(),'Origin':'https://portal-market.com','Referer':'https://portal-market.com/','User-Agent':UA,'Accept':'application/json'}
def portals_items(sess):
 out=[]
 for status in ('listed','unlisted'):
  offset=0
  for _ in range(20):
   r=sess.get(API_PORTALS+'/nfts/owned',headers=portal_headers(),params={'offset':offset,'limit':50,'status':status},timeout=25)
   if r.status_code==401:
    subprocess.run([str(BASE/'session-venv/bin/python'),str(BASE/'portals_refresh_auth.py')],cwd=BASE,check=True,timeout=60);r=sess.get(API_PORTALS+'/nfts/owned',headers=portal_headers(),params={'offset':offset,'limit':50,'status':status},timeout=25)
   r.raise_for_status();data=r.json();batch=data.get('nfts') or []
   for x in batch:
    a=attrs(x);lid=str(x.get('id'));out.append({'market':'portals','id':lid,'collection':x.get('name'),'number':x.get('external_collection_number'),'model':a.get('model'),'backdrop':a.get('backdrop'),'symbol':a.get('symbol'),'status':'listed' if x.get('status')=='listed' else 'inventory','price':float(x['price']) if x.get('price') is not None else None,'floor':float(x['floor_price']) if x.get('floor_price') is not None else None,'image_url':x.get('photo_url'),'gift_url':'https://t.me/portals_market_bot/market?startapp=gift_'+lid})
   offset+=len(batch);total=int(data.get('total_count') or len(batch))
   if not batch or offset>=total:break
 return out
def tonnel_context():
 cap=json.loads((TONNEL/'api_capture.json').read_text());cd=json.loads((TONNEL/'cloudflare_cookies.json').read_text());u=json.loads((TONNEL/'webview.json').read_text())['url'];auth=(parse_qs(urlsplit(u).fragment).get('tgWebAppData') or [None])[0];user=json.loads(unquote(parse_qs(auth)['user'][0]));headers={k:v for k,v in cap['headers'].items() if k.lower() in ('accept','accept-language','content-type','origin','referer','user-agent')};cookies={x['name']:x['value'] for x in cd.get('cookies',[])};return cap['url'],auth,int(user['id']),headers,cookies
def tonnel_items(sess):
 endpoint,auth,uid,headers,cookies=tonnel_context();out=[]
 for listed in (True,False):
  for page in range(1,21):
   if listed:flt={'seller':uid,'buyer':{'$exists':False},'$or':[{'price':{'$exists':True}},{'auction_id':{'$exists':True}}]};sort={'message_post_time':-1,'gift_id':-1}
   else:flt={'seller':uid,'buyer':{'$exists':False},'refunded':{'$ne':True},'price':{'$exists':False}};sort={'gift_num':1,'gift_id':-1}
   body={'filter':json.dumps(flt,separators=(',',':')),'limit':30,'page':page,'sort':json.dumps(sort,separators=(',',':')),'ref':0,'user_auth':auth};r=sess.post(endpoint,headers=headers,cookies=cookies,json=body,timeout=30)
   if r.status_code in (401,403,429):
    subprocess.run(['systemctl','start','tonnel-reader-collector.service'],check=False,timeout=310);endpoint,auth,uid,headers,cookies=tonnel_context();body['user_auth']=auth;r=sess.post(endpoint,headers=headers,cookies=cookies,json=body,timeout=30)
   r.raise_for_status();batch=r.json()
   if not isinstance(batch,list):raise RuntimeError('Tonnel inventory payload invalid')
   for x in batch:
    lid=str(x.get('gift_id'));name=x.get('name');num=x.get('gift_num');slug=re.sub(r'[^A-Za-z0-9]','',str(name or '')).lower();out.append({'market':'tonnel','id':lid,'collection':name,'number':num,'model':clean(x.get('model')),'backdrop':clean(x.get('backdrop')),'symbol':clean(x.get('symbol')),'status':'listed' if x.get('price') is not None or x.get('auction_id') else 'inventory','price':float(x['price']) if x.get('price') is not None else None,'floor':None,'image_url':f'https://nft.fragment.com/gift/{slug}-{num}.large.jpg' if slug and num else None,'gift_url':'https://t.me/Tonnel_Network_bot/gifts?startapp='+lid})
   if len(batch)<30:break
 return out
def main():
 OUT.parent.mkdir(parents=True,exist_ok=True);os.chmod(OUT.parent,0o700);sess=requests.Session(impersonate='chrome124');old={}
 while True:
  try:old=json.loads(OUT.read_text())
  except Exception:old={}
  markets={};now=time.time()
  for name,fn in (('portals',portals_items),):
   try:
    items=fn(sess);markets[name]={'updated_at':time.time(),'error':None,'inventory_count':sum(x['status']=='inventory' for x in items),'listed_count':sum(x['status']=='listed' for x in items),'items':items}
   except Exception as e:
    prev=(old.get('markets') or {}).get(name) or {'items':[],'inventory_count':0,'listed_count':0};markets[name]={**prev,'error':f'{type(e).__name__}: {str(e)[:240]}','updated_at':time.time()}
  atomic(OUT,{'updated_at':time.time(),'markets':markets});old={'markets':markets};time.sleep(30)
if __name__=='__main__':main()
