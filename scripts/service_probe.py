"""检查当前目录、当前数据库和配置端口的实例；不输出密钥。"""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from app.config import Settings,instance_id


def config():
    settings=Settings.from_env()
    host=os.getenv('APP_HOST','127.0.0.1')
    browser_host='127.0.0.1' if host in ('0.0.0.0','::') else host
    if ':' in browser_host:browser_host='['+browser_host+']'
    port=int(os.getenv('APP_PORT','8765'))
    return {'host':host,'port':port,'url':f'http://{browser_host}:{port}/','instance_id':instance_id(settings.db_path)}


def ready(configuration):
    try:
        response=httpx.get(configuration['url']+'api/meta',timeout=2,trust_env=False)
        meta=response.json()
        return response.status_code==200 and meta.get('simulation') is True and meta.get('instance_id')==configuration['instance_id']
    except (httpx.HTTPError,ValueError):
        return False


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',action='store_true');args=parser.parse_args()
    configuration=config()
    if args.config:print(json.dumps(configuration))
    else:sys.exit(0 if ready(configuration) else 1)
