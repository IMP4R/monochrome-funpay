#!/usr/bin/env python3
import asyncio,json,os,time
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
ROOT=Path(os.environ.get('TONNEL_ROOT','/root/monochrome/sessions/tonnel-reader'));WEBVIEW=ROOT/'webview.json';PROFILE=Path(os.environ.get('TONNEL_PROFILE',str(ROOT/'chrome-profile')))
STATE=ROOT/'browser_state.json';CAPTURE=ROOT/'api_capture.json';RESPONSE=ROOT/'latest_response.json';COOKIES=ROOT/'cloudflare_cookies.json'
def atomic(p,d):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(d,ensure_ascii=False));os.chmod(t,0o600);t.replace(p)
async def main():
 url=json.loads(WEBVIEW.read_text())['url'];PROFILE.mkdir(parents=True,exist_ok=True);os.chmod(PROFILE,0o700);captured=[];responses=[];tasks=[];started=time.time();error=None;title='';body='';current='';status=None
 async with async_playwright() as p:
  ctx=await p.chromium.launch_persistent_context(str(PROFILE),executable_path='/usr/bin/google-chrome',headless=os.environ.get('TONNEL_HEADFUL')!='1',locale='ru-RU',timezone_id='Europe/Moscow',viewport={'width':430,'height':900},args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--no-first-run'])
  page=ctx.pages[0] if ctx.pages else await ctx.new_page()
  for old in list(ctx.pages)[1:]:
   try:await old.close()
   except Exception:pass
  try:await page.goto('about:blank',wait_until='commit',timeout=10000)
  except Exception:pass
  def req(r):
   if 'pageGifts' in r.url:captured.append({'url':r.url,'method':r.method,'headers':r.headers,'post_data':r.post_data,'captured_at':time.time()})
  async def readresp(r):
   if 'pageGifts' not in r.url:return
   try:responses.append({'status':r.status,'payload':json.loads(await r.text())})
   except Exception as exc:responses.append({'status':r.status,'error':type(exc).__name__})
  page.on('request',req);page.on('response',lambda r:tasks.append(asyncio.create_task(readresp(r))))
  try:
   try:
    nav=await page.goto(url,wait_until='domcontentloaded',timeout=45000);status=nav.status if nav else None
   except Exception as exc:error=f'{type(exc).__name__}: {str(exc)[:160]}'
   for tick in range(60):
    if responses and responses[-1].get('status')==200 and isinstance(responses[-1].get('payload'),list):break
    if page.is_closed():error=error or 'browser_page_closed';break
    try:
     title=await page.title();current=page.url
     if tick%5==0:atomic(COOKIES,{'updated_at':time.time(),'title':title,'page_url':current,'cookies':await ctx.cookies()})
     await page.wait_for_timeout(1000)
    except Exception as exc:error=error or f'{type(exc).__name__}: {str(exc)[:160]}';break
   if tasks:await asyncio.gather(*tasks,return_exceptions=True)
   try:current=page.url;title=await page.title();body=(await page.locator('body').inner_text(timeout=5000))[:500]
   except Exception as exc:error=error or f'{type(exc).__name__}: {str(exc)[:160]}'
  finally:
   cookies=[];storage={}
   try:cookies=await ctx.cookies();storage=await ctx.storage_state();atomic(COOKIES,{'updated_at':time.time(),'title':title,'page_url':current,'cookies':cookies})
   except Exception:pass
   ok=bool(responses) and responses[-1].get('status')==200 and isinstance(responses[-1].get('payload'),list)
   atomic(STATE,{'created_at':time.time(),'cookies':cookies,'storage':storage,'page_url':current,'title':title,'cloudflare':('just a moment' in title.lower() or 'cdn-cgi' in current.lower()),'error':None if ok else error or 'pageGifts_not_captured'})
   if captured:atomic(CAPTURE,captured[-1])
   if responses:atomic(RESPONSE,{'captured_at':time.time(),**responses[-1]})
   try:await ctx.close()
   except Exception:pass
 ok=bool(responses) and responses[-1].get('status')==200 and isinstance(responses[-1].get('payload'),list)
 print(json.dumps({'status':status,'title':title[:100],'page_host':urlsplit(current).netloc,'cloudflare':('just a moment' in title.lower() or 'cdn-cgi' in current.lower()),'api_captured':bool(captured),'api_response':responses[-1].get('status') if responses else None,'items':len(responses[-1].get('payload',[])) if ok else 0,'body_prefix':body[:120],'error':None if ok else error or 'pageGifts_not_captured','seconds':round(time.time()-started,1)},ensure_ascii=False))
 if not ok:raise SystemExit(2)
asyncio.run(main())
