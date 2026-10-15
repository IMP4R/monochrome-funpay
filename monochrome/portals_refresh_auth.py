#!/usr/bin/env python3
import subprocess,sys
p=subprocess.run(['/root/monochrome/session-venv/bin/python','/root/monochrome/telegram_fulfillment.py','refresh-auth'],text=True,capture_output=True,timeout=70)
if p.stdout:print(p.stdout.strip())
if p.returncode:
 if p.stderr:print(p.stderr,file=sys.stderr)
 raise SystemExit(p.returncode)
