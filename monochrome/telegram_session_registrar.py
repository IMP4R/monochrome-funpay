#!/usr/bin/env python3
import asyncio, fcntl, json, os, sys, time
from pathlib import Path
from telethon import TelegramClient
from telethon.errors import (PasswordHashInvalidError, PhoneCodeExpiredError,
    PhoneCodeInvalidError, SessionPasswordNeededError)

ROOT = Path(os.environ.get('TG_REG_ROOT', '/root/monochrome/sessions/tonnel-reader'))
PHONE = os.environ['TG_READER_PHONE']
API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
STATE = ROOT / 'state.json'; CODE = ROOT / 'code.in'; PASSWORD = ROOT / 'password.in'
RESEND = ROOT / 'resend.request'; LOCK = ROOT / 'registrar.lock'; SESSION = ROOT / 'reader'

def atomic_json(data):
    tmp = STATE.with_suffix('.tmp'); tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.chmod(tmp, 0o600); tmp.replace(STATE)

def load_state():
    try: return json.loads(STATE.read_text())
    except Exception: return {'phone': PHONE, 'status': 'new', 'created_at': time.time()}

def take(path):
    if not path.exists(): return None
    value = path.read_text().strip(); path.unlink(missing_ok=True); return value

def update(state, status, **extra):
    state.update(phone=PHONE, status=status, updated_at=time.time(), **extra); atomic_json(state)

def public_status(state):
    return {k: state.get(k) for k in ('phone','status','sent_at','updated_at','error','me_id')}

async def run():
    ROOT.mkdir(parents=True, exist_ok=True); os.chmod(ROOT, 0o700)
    lock = open(LOCK, 'w'); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = load_state()
    if state.get('phone') not in (None, PHONE): raise RuntimeError('state belongs to another phone')
    client = TelegramClient(str(SESSION), API_ID, API_HASH)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me(); update(state, 'authorized', me_id=me.id, error=None); return
        pending = state.get('status') in ('waiting_code','waiting_2fa') and state.get('phone_code_hash')
        if RESEND.exists():
            RESEND.unlink(); pending = False
        if not pending:
            sent = await client.send_code_request(PHONE)
            update(state, 'waiting_code', phone_code_hash=sent.phone_code_hash,
                   sent_at=time.time(), delivery_type=type(sent.type).__name__, error=None)
        while True:
            state = load_state(); status = state.get('status')
            if status == 'waiting_code':
                code = take(CODE)
                if code:
                    try:
                        await client.sign_in(phone=PHONE, code=code,
                                             phone_code_hash=state['phone_code_hash'])
                    except SessionPasswordNeededError:
                        update(state, 'waiting_2fa', error=None)
                    except PhoneCodeInvalidError:
                        update(state, 'waiting_code', error='invalid_code')
                    except PhoneCodeExpiredError:
                        update(state, 'code_expired', error='explicit_resend_required')
            elif status == 'waiting_2fa':
                password = take(PASSWORD)
                if password:
                    try: await client.sign_in(password=password)
                    except PasswordHashInvalidError: update(state, 'waiting_2fa', error='invalid_2fa')
            elif status == 'code_expired':
                if RESEND.exists():
                    RESEND.unlink(); sent = await client.send_code_request(PHONE)
                    update(state, 'waiting_code', phone_code_hash=sent.phone_code_hash,
                           sent_at=time.time(), delivery_type=type(sent.type).__name__, error=None)
            if await client.is_user_authorized():
                me = await client.get_me(); update(state, 'authorized', me_id=me.id, error=None); return
            await asyncio.sleep(1)
    finally: await client.disconnect()

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'status': print(json.dumps(public_status(load_state()), ensure_ascii=False))
    else: asyncio.run(run())
