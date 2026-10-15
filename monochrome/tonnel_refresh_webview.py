#!/usr/bin/env python3
import asyncio,json,os,time
from pathlib import Path
from telethon import TelegramClient,functions,types,utils
ROOT=Path(os.environ.get('TONNEL_ROOT','/root/monochrome/sessions/tonnel-reader'))
async def main():
 c=TelegramClient(str(ROOT/'reader'),int(os.environ['TG_API_ID']),os.environ['TG_API_HASH']);await c.connect();assert await c.is_user_authorized()
 bot=await c.get_entity('Tonnel_Network_bot');peer=await c.get_input_entity(bot);app=types.InputBotAppShortName(bot_id=utils.get_input_user(bot),short_name='gifts')
 view=await c(functions.messages.RequestAppWebViewRequest(peer=peer,app=app,platform='android',write_allowed=False,fullscreen=True))
 p=ROOT/'webview.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps({'created_at':time.time(),'bot':'Tonnel_Network_bot','short_name':'gifts','url':view.url,'host':'marketplace.tonnel.network'}));os.chmod(tmp,0o600);tmp.replace(p)
 await c.disconnect();print('WEBVIEW_REFRESHED')
asyncio.run(main())
