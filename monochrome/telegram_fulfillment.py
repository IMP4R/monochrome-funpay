#!/usr/bin/env python3
import asyncio,json,os,re,sys
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from telethon import TelegramClient,functions,types,utils
BASE=Path('/root/monochrome');SESSION=Path('/root/monochrome/sessions/portals-account/reader')
def env():
 for line in (BASE/'.telegram_reader.env').read_text().splitlines():
  if '=' in line and not line.lstrip().startswith('#'):
   k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))
env()
def client():return TelegramClient(str(SESSION),int(os.environ['TG_API_ID']),os.environ['TG_API_HASH'])
async def run():
 cmd=sys.argv[1] if len(sys.argv)>1 else 'status';c=client();await c.connect()
 if not await c.is_user_authorized():raise RuntimeError('Telegram session is not authorized')
 me=await c.get_me()
 if cmd=='status':out={'ok':True,'id':me.id,'username':me.username}
 elif cmd=='validate':
  u=sys.argv[2].lstrip('@');e=await c.get_entity(u);out={'ok':True,'id':e.id,'username':getattr(e,'username',None),'bot':bool(getattr(e,'bot',False))}
 elif cmd=='send':
  u=sys.argv[2].lstrip('@');text=sys.stdin.read();m=await c.send_message(u,text);out={'ok':True,'id':m.id,'username':u}
 elif cmd=='refresh-auth':
  bot=await c.get_entity('portals_market_bot');peer=await c.get_input_entity(bot);app=types.InputBotAppShortName(bot_id=utils.get_input_user(bot),short_name='market')
  view=await c(functions.messages.RequestAppWebViewRequest(peer=peer,app=app,platform='android',write_allowed=True,fullscreen=True));u=urlsplit(view.url);data=(parse_qs(u.fragment).get('tgWebAppData') or parse_qs(u.query).get('tgWebAppData') or [None])[0]
  if not data:raise RuntimeError('Portals tgWebAppData missing')
  p=BASE/'.auth';t=p.with_suffix('.tmp');t.write_text('tma '+data);os.chmod(t,0o600);t.replace(p);out={'ok':True,'auth_length':len(data),'telegram_id':me.id}
 else:raise ValueError('unknown command')
 await c.disconnect();print(json.dumps(out,ensure_ascii=False))
asyncio.run(run())
