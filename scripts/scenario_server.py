"""启动独立的三情境演示服务；普通8765账本和会话不受影响。"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import ROOT,Settings
from app.main import create_app
import uvicorn

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8768)
    parser.add_argument('--db',type=Path,default=ROOT/'data/scenario-lab/bank.sqlite3')
    parser.add_argument('--date',default='system')
    args=parser.parse_args()
    target=args.db.resolve()
    allowed=((ROOT/'data/scenario-lab').resolve(),(ROOT/'artifacts').resolve())
    if not any(target.is_relative_to(p) for p in allowed):raise SystemExit('Scenario database must stay inside scenario-lab or artifacts')
    settings=Settings.from_env();settings.db_path=target;settings.demo_scenarios=True
    settings.demo_date=None if args.date=='system' else args.date
    uvicorn.run(create_app(settings),host='127.0.0.1',port=args.port)
