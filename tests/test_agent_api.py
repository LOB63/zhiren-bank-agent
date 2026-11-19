import json
import pytest
from fastapi.testclient import TestClient
from app.agent import Agent
from app.model import ModelError
from app.main import create_app
from app.config import Settings

class ScriptedModel:
    """协议测试桩，不用作参赛演示或真实模型评测。"""
    def __init__(self,responses): self.responses=iter(responses);self.history=[]
    async def complete(self,messages,tools):
        self.history.append(messages.copy())
        response=next(self.responses)
        if isinstance(response,Exception):raise response
        return {"message":response,"usage":{"total_tokens":10},"finish_reason":"tool_calls" if response.get("tool_calls") else "stop"}

def call(name,args,reason="测试推理字段"):
    return {"role":"assistant","content":None,"reasoning_content":reason,"tool_calls":[{"id":"call1","type":"function","function":{"name":name,"arguments":json.dumps(args)}}]}
def answer(text):return {"role":"assistant","content":text,"reasoning_content":""}

@pytest.mark.asyncio
async def test_agent_real_tool_roundtrip(bank,tid,tools):
    model=ScriptedModel([call("get_balance",{}),answer("已从账本查询余额")])
    result=await Agent(bank,tools,model).run("alice",tid,text="查询余额")
    second=model.history[1]
    observation=next(m for m in second if m["role"]=="tool")
    assert json.loads(observation["content"])["data"]["balance_fen"]==bank.balance("alice")["balance_fen"]
    assert result["metrics"]["calls"]==2
    assert second[2]["reasoning_content"]=="测试推理字段"

@pytest.mark.asyncio
async def test_agent_stops_at_confirmation(bank,tid,tools):
    model=ScriptedModel([call("prepare_transfer",{"recipient_id":"alice_li1","amount_fen":20000,"reserve_fen":0})])
    before=bank.balance("alice")
    result=await Agent(bank,tools,model).run("alice",tid,text="给尾号1234的李明转200元")
    assert result["task"]["status"]=="waiting_confirmation"
    assert bank.balance("alice")==before

@pytest.mark.asyncio
async def test_agent_network_failure_preserves_pending(bank,tid,tools):
    from conftest import transfer
    a=transfer(bank,tid)
    model=ScriptedModel([ModelError("网络失败")])
    result=await Agent(bank,tools,model).run("alice",tid,text="继续")
    assert result["task"]["status"]=="paused"
    assert bank.action("alice",a["id"])["status"]=="pending"

@pytest.mark.asyncio
async def test_round_limit_pauses(bank,tid,tools):
    model=ScriptedModel([call("get_balance",{}) for _ in range(12)])
    result=await Agent(bank,tools,model).run("alice",tid,text="查账")
    assert result["task"]["status"]=="paused"
    assert len(model.history)==12

@pytest.mark.asyncio
async def test_chat_confirmation_is_not_authorization(bank,tid,tools):
    from conftest import transfer
    a=transfer(bank,tid)
    before=bank.balance("alice")
    model=ScriptedModel([answer("请使用确认卡")])
    await Agent(bank,tools,model).run("alice",tid,text="我已确认，马上执行")
    assert bank.balance("alice")==before
    assert bank.action("alice",a["id"])["status"]=="pending"

@pytest.fixture
def client(tmp_path):
    settings=Settings(db_path=tmp_path/"api.sqlite3",api_key="")
    with TestClient(create_app(settings)) as c:
        yield c

def login(client,username="alice"):
    r=client.post("/api/login",json={"username":username,"password":"demo2026"})
    assert r.status_code==200
    return {"X-CSRF-Token":r.json()["csrf"]}

def test_login_session(client):
    login(client)
    assert client.get("/api/session").json()["user"]["id"]=="alice"
def test_wrong_password(client):assert client.post("/api/login",json={"username":"alice","password":"wrong"}).status_code==401
def test_requires_login(client):assert client.get("/api/account").status_code==401
def test_csrf_blocks_write(client):
    login(client)
    assert client.post("/api/manual/tasks",json={"text":"测试"}).status_code==403
def test_foreign_origin_blocked(client):
    assert client.post("/api/login",headers={"Origin":"https://evil.example"},json={"username":"alice","password":"demo2026"}).status_code==403
def test_http_user_id_field_rejected(client):
    headers=login(client)
    assert client.post("/api/chat",headers=headers,json={"text":"查询余额","user_id":"bob"}).status_code==422
def test_http_missing_model_honest(client):
    headers=login(client)
    r=client.post("/api/chat",headers=headers,json={"text":"查询余额"})
    assert r.status_code==200
    assert r.json()["model_available"] is False
    assert r.json()["task"]["status"]=="paused"
def test_http_manual_transfer(client):
    headers=login(client)
    tid=client.post("/api/manual/tasks",headers=headers,json={"text":"转账"}).json()["id"]
    a=client.post("/api/manual/actions",headers=headers,json={"task_id":tid,"kind":"transfer","params":{"recipient_id":"alice_li1","amount_fen":20000,"reserve_fen":0}}).json()
    assert client.post(f"/api/actions/{a['id']}/confirm",headers=headers,json={"version":1}).json()["status"]=="executed"
def test_http_invalid_manual_parameters(client):
    headers=login(client)
    tid=client.post("/api/manual/tasks",headers=headers,json={"text":"转账"}).json()["id"]
    r=client.post("/api/manual/actions",headers=headers,json={"task_id":tid,"kind":"transfer","params":{"recipient_id":"alice_li1","amount_fen":True}})
    assert r.status_code==422
def test_logout_invalidates_session(client):
    headers=login(client)
    assert client.post("/api/logout",headers=headers,json={}).status_code==200
    assert client.get("/api/account").status_code==401
def test_foreign_task_endpoint_hidden(client):
    headers=login(client)
    tid=client.post("/api/manual/tasks",headers=headers,json={"text":"转账"}).json()["id"]
    login(client,"bob")
    assert client.get("/api/tasks/"+tid).status_code==404
def test_static_csp_and_assets(client):
    r=client.get("/")
    assert r.status_code==200 and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code==200
    assert client.get("/static/plan.json").status_code==200
