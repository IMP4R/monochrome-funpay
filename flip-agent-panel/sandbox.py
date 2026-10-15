import os, re, shutil, subprocess, uuid
from pathlib import Path
ROOT=Path('/var/lib/flip-agent/workspaces')
ROOT.mkdir(parents=True,exist_ok=True)
def safe_name(value):
 value=re.sub(r'[^a-zA-Z0-9_.-]+','-',value).strip('-')[:80]
 return value or uuid.uuid4().hex
def workspace(job_id):
 p=ROOT/safe_name(job_id);p.mkdir(mode=0o777,parents=True,exist_ok=True);os.chmod(p,0o777);return p
def write_files(job_id,files):
 root=workspace(job_id)
 for name,content in files.items():
  name=name.replace('\\','/').lstrip('/')
  if '..' in name.split('/') or not re.fullmatch(r'[A-Za-z0-9_./-]{1,160}',name):raise ValueError('unsafe path')
  p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
  current=p.parent
  while current==root or root in current.parents:
   os.chmod(current,0o777)
   if current==root:break
   current=current.parent
  p.write_text(content,encoding='utf-8');os.chmod(p,0o644)
 return root
def run(job_id,argv,timeout=30,network=False):
 root=workspace(job_id);unit='flip-run-'+safe_name(job_id)[:40]
 props=['DynamicUser=yes','NoNewPrivileges=yes','PrivateTmp=yes','PrivateDevices=yes','ProtectSystem=strict','ProtectHome=yes','ProtectKernelTunables=yes','ProtectKernelModules=yes','ProtectControlGroups=yes','RestrictSUIDSGID=yes','LockPersonality=yes','MemoryMax=384M','TasksMax=64','CPUQuota=100%','ReadWritePaths='+str(root)]
 if not network:props += ['PrivateNetwork=yes','RestrictAddressFamilies=AF_UNIX']
 cmd=['systemd-run','--wait','--pipe','--collect','--quiet','--unit='+unit,'--working-directory='+str(root),'--property=RuntimeMaxSec='+str(max(1,min(timeout,120)))]
 for p in props:cmd += ['--property='+p]
 cmd += ['--']+list(argv)
 try:
  r=subprocess.run(cmd,text=True,capture_output=True,timeout=min(timeout+10,140))
  out=(r.stdout+r.stderr)[-20000:]
  return {'ok':r.returncode==0,'exit_code':r.returncode,'output':out}
 except subprocess.TimeoutExpired as e:return {'ok':False,'exit_code':124,'output':((e.stdout or '')+(e.stderr or '')+'\nTIMEOUT')[-20000:]}
def run_code(job_id,code,timeout=30,network=False):
 write_files(job_id,{'main.py':code})
 return run(job_id,['/usr/bin/python3','-I','main.py'],timeout,network)
def cleanup(job_id):
 shutil.rmtree(ROOT/safe_name(job_id),ignore_errors=True)
