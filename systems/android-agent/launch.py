import os,sys,subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
os.environ.setdefault('OIN_ENVIRONMENT','development')
os.environ.setdefault('OIN_PUBLIC_BASE_URL','https://localhost:18080')
os.environ.setdefault('OIN_BIND_HOST','127.0.0.1')
os.environ.setdefault('OIN_BIND_PORT','18080')
os.environ.setdefault('OIN_DATABASE_PATH',str(root/'runtime/oin-development.db'))
os.environ.setdefault('OIN_DATABASE_AUTO_MIGRATE','true')
os.environ['PYTHONPATH']=str(root/'source/services/control-plane/src')
(root/'runtime').mkdir(exist_ok=True)
subprocess.run([sys.executable,'-m','oin_control_plane'],cwd=root,env=os.environ,check=True)
