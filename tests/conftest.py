import pytest
from app.config import Settings
from app.db import Database
from app.service import BankService
from app.tools import ToolRegistry

@pytest.fixture
def bank(tmp_path):
    settings=Settings(db_path=tmp_path/"bank.sqlite3",api_key="",demo_date="2026-10-11",legacy_wealth_enabled=True)
    database=Database(settings)
    database.seed()
    return BankService(database)

@pytest.fixture
def tid(bank):
    return bank.create_task("alice","测试任务")["id"]

@pytest.fixture
def tools(bank):
    return ToolRegistry(bank)

def transfer(bank,tid,amount=20000,reserve=0,uid="alice"):
    return bank.prepare(uid,tid,"transfer",{"recipient_id":uid+"_li1","amount_fen":amount,"reserve_fen":reserve})
