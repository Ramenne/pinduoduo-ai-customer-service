"""Native local bootstrap for an isolated copy; never uses production credentials."""
import argparse,os,secrets,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
NAME='pdd' if '拼多多' in ROOT.name else 'xianyu'
API=18089 if NAME=='pdd' else 8089
WS=18090 if NAME=='pdd' else 8090
SCHED=18091 if NAME=='pdd' else 8091
FRONT=19102 if NAME=='pdd' else 9000
parser=argparse.ArgumentParser()
parser.add_argument('action',choices=['init-env','init-db','install','backend','websocket','scheduler','frontend','browser-install'])
args=parser.parse_args()
def run(cmd,cwd=ROOT,env=None):return subprocess.run(cmd,cwd=cwd,env=env,check=True)
if args.action=='init-env':
 p=ROOT/'runtime.env'
 if p.exists():raise SystemExit('runtime.env already exists; edit it instead of overwriting')
 values={'MYSQL_HOST':'127.0.0.1','MYSQL_PORT':'3306','MYSQL_USER':'root','MYSQL_PASSWORD':'CHANGE_TO_YOUR_LOCAL_MYSQL_PASSWORD',
  'MYSQL_DATABASE':'oin_'+NAME+'_local','REDIS_HOST':'127.0.0.1','REDIS_PORT':'6379','REDIS_PASSWORD':'','REDIS_DB':'3' if NAME=='pdd' else '4',
  'JWT_SECRET_KEY':secrets.token_hex(32),'DATA_ENCRYPT_KEY':secrets.token_hex(32),
  'INTERNAL_SERVICE_TOKEN':secrets.token_hex(32),'INTERNAL_API_TOKEN':secrets.token_hex(32),
  'HOST':'127.0.0.1','BACKEND_WEB_HOST':'127.0.0.1','BACKEND_WEB_PORT':str(API),'WEBSOCKET_HOST':'127.0.0.1','WEBSOCKET_PORT':str(WS),'SCHEDULER_PORT':str(SCHED),
  'BACKEND_WEB_API_PREFIX':'/api/v1','BACKEND_WEB_SERVICE_URL':f'http://127.0.0.1:{API}',
  'WEBSOCKET_SERVICE_URL':f'http://127.0.0.1:{WS}','SCHEDULER_SERVICE_URL':f'http://127.0.0.1:{SCHED}',
  'DEFAULT_ADMIN_USERNAME':'admin','DEFAULT_ADMIN_PASSWORD':secrets.token_urlsafe(18),'SQL_ECHO':'false',
  'DB_POOL_SIZE':'10','DB_MAX_OVERFLOW':'10','REDIS_POOL_SIZE':'20','ENVIRONMENT':'development','QR_LOGIN_HEADLESS':'false'}
 # Both directions share the same service-to-service token.
 values['INTERNAL_API_TOKEN']=values['INTERNAL_SERVICE_TOKEN']
 p.write_text('\n'.join(k+'='+v for k,v in values.items())+'\n');p.chmod(0o600)
 print('Created runtime.env. Edit local MySQL/Redis settings; new PDD admin password is in this file.')
 raise SystemExit(0)
if args.action=='install':
 run([sys.executable,'-m','pip','install','-r','requirements-production.txt'])
 raise SystemExit(0)
if args.action=='browser-install':
 run([sys.executable,'-m','playwright','install','chromium'])
 if NAME=='xianyu':run([sys.executable,'-m','patchright','install','chromium'])
 raise SystemExit(0)
if not (ROOT/'runtime.env').exists():raise SystemExit('Run init-env, then edit runtime.env first')
from dotenv import dotenv_values
values={k:v for k,v in dotenv_values(ROOT/'runtime.env').items() if v is not None}
env=os.environ.copy();env.update(values)
env['PYTHONPATH']=str(ROOT/'source')+os.pathsep+str(ROOT/'source/websocket')
if args.action=='init-db':
 import pymysql
 from pymysql.constants import CLIENT
 db=values['MYSQL_DATABASE']
 if not db.replace('_','').isalnum() or not db.endswith('_local'):raise SystemExit('Only *_local databases are supported by this helper')
 connection=pymysql.connect(host=values['MYSQL_HOST'],port=int(values['MYSQL_PORT']),user=values['MYSQL_USER'],password=values['MYSQL_PASSWORD'],charset='utf8mb4',autocommit=True,client_flag=CLIENT.MULTI_STATEMENTS)
 with connection:
  with connection.cursor() as c:
   c.execute('CREATE DATABASE IF NOT EXISTS `'+db+'` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
   c.execute('USE `'+db+'`');c.execute('SHOW TABLES')
   if c.fetchall():raise SystemExit('Target database is not empty; refusing to overwrite it')
   c.execute((ROOT/'database/schema.sql').read_text())
   while c.nextset():pass
   if NAME=='xianyu':
    c.execute("INSERT INTO xy_system_settings (`key`,`value`,`description`) VALUES (%s,%s,%s)",('login_captcha_enabled','false','Local clean-room bootstrap only; configure captcha before public deployment'))
 print('Fresh local database structure imported. Start backend to seed dictionaries, roles and a new admin.')
elif args.action=='frontend':
 env.update(VITE_DEV_PROXY_TARGET=f'http://127.0.0.1:{API}',VITE_API_BASE_URL='/api/v1')
 npm='npm.cmd' if os.name=='nt' else 'npm'
 run([npm,'ci','--no-audit','--no-fund'],ROOT/'source/frontend',env)
 run([npm,'run','dev','--','--host','127.0.0.1','--port',str(FRONT)],ROOT/'source/frontend',env)
else:
 service='backend-web' if args.action=='backend' and NAME=='xianyu' else args.action
 run([sys.executable,'main.py'],ROOT/'source'/service,env)
