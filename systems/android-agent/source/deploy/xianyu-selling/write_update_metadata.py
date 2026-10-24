"""Generate the public sidecar for a signed, version-checked APK artifact."""
import argparse
import hashlib
import json
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('apk',type=Path)
parser.add_argument('--code',type=int,required=True)
parser.add_argument('--version',required=True)
args=parser.parse_args()
size=args.apk.stat().st_size
if not 1<=size<=32*1024*1024 or not 1<=args.code<=2147483647:
    raise SystemExit('APK size/version outside update contract')
digest=hashlib.sha256()
with args.apk.open('rb') as source:
    for chunk in iter(lambda:source.read(65536),b''): digest.update(chunk)
value={'package_name':'com.feifei.phonebridge','version_code':args.code,'version_name':args.version,
       'size_bytes':size,'sha256':digest.hexdigest()}
args.apk.with_suffix('.update.json').write_text(json.dumps(value)+'\n')
print(json.dumps(value))
