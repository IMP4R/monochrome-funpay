#!/usr/bin/env python3
import asyncio,json,os,time
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path('/root/monochrome/sessions/tonnel-reader');WEB=ROOT/'webview.json';OUT=ROOT/'probe_response.json'
def atomic(p,x):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(x,ensure_ascii=False));os.chmod(t,0o600);t.replace(p)
async def main():
 url=json.loads(WEB.read_text())['url'];requests_seen=[];responses=[];tasks=[]
 async with async_playwright() as p:
  ctx=await p.chromium.launch_persistent_context(str(ROOT/'chrome-profile'),executable_path='/usr/bin/google-chrome',headless=False,locale='ru-RU',timezone_id='Europe/Moscow',viewport={'width':430,'height':900},args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-extensions','--disable-background-networking','--disable-sync','--renderer-process-limit=1','--no-first-run'])
  page=ctx.pages[0] if ctx.pages else await ctx.new_page()
  for old in list(ctx.pages)[1:]: await old.close()
  await page.goto('about:blank')
  def onreq(r):
   if 'pageGifts' in r.url: requests_seen.append({'url':r.url,'post_data':r.post_data})
  async def readresp(r):
   if 'pageGifts' not in r.url:return
   try: responses.append({'url':r.url,'status':r.status,'text':await r.text()})
   except Exception as e: responses.append({'url':r.url,'status':r.status,'error':type(e).__name__})
  page.on('request',onreq);page.on('response',lambda r:tasks.append(asyncio.create_task(readresp(r))))
  nav=await page.goto(url,wait_until='domcontentloaded',timeout=35000)
  for _ in range(50):
   if responses:break
   await page.wait_for_timeout(500)
  if tasks: await asyncio.gather(*tasks,return_exceptions=True)
  title=await page.title();body=(await page.locator('body').inner_text(timeout=5000))[:240]
  if not responses:
   raise RuntimeError(f'no API response nav={nav.status if nav else None} title={title!r} body={body!r} requests={len(requests_seen)}')
  rr=responses[-1]
  try:payload=json.loads(rr.get('text',''))
  except:payload={'non_json':rr.get('text','')[:300]}
  atomic(OUT,{'captured_at':time.time(),'status':rr['status'],'payload':payload})
  print(json.dumps({'nav_status':nav.status if nav else None,'api_status':rr['status'],'request_count':len(requests_seen),'response_count':len(responses),'shape':type(payload).__name__,'length':len(payload) if isinstance(payload,(list,dict)) else None,'keys':list(payload)[:20] if isinstance(payload,dict) else None}))
  await ctx.close()
asyncio.run(main())
