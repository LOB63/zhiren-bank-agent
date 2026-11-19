import pytest
from concurrent.futures import ThreadPoolExecutor
from app.service import BankError, BankService
from app.db import Database
from conftest import transfer
from test_agent_api import ScriptedModel, call, answer, login, client
from app.agent import Agent

def question(bank,tid,amount=10000,reserve=0):
    return transfer(bank,tid,amount,reserve)["repeat_request"]

def expect(code,fn):
    with pytest.raises(BankError) as e: fn()
    assert e.value.code==code

@pytest.mark.parametrize("executed",[False,True])
def test_repeat_asks_and_preserves_constraint(bank,tid,executed):
    a=transfer(bank,tid,10000,300000)
    (bank.confirm if executed else bank.reject)("alice",a["id"],1)
    before=bank.balance("alice")
    r=question(bank,tid)
    assert r["params"]["reserve_fen"]==300000 and r["status"]=="pending"
    assert len(bank.task("alice",tid)["actions"])==1
    assert bank.balance("alice")==before
    new=bank.decide_repeat("alice",r["id"],r["version"],True)
    assert new["id"]!=a["id"] and new["status"]=="pending"
    assert new["params"]["reserve_fen"]==300000
    assert bank.balance("alice")==before
    bank.confirm("alice",new["id"],1)
    assert bank.balance("alice")["balance_fen"]==before["balance_fen"]-10000

def test_repeat_decline_creates_no_action(bank,tid):
    a=transfer(bank,tid,10000,300000);bank.reject("alice",a["id"],1)
    r=question(bank,tid)
    bank.decide_repeat("alice",r["id"],1,False)
    assert len(bank.task("alice",tid)["actions"])==1
    assert question(bank,tid)["status"]=="declined"
    expect("REPEAT_DECIDED",lambda:bank.decide_repeat("alice",r["id"],1,True))

def test_rejected_repeat_cannot_lower_by_revise(bank,tid):
    a=transfer(bank,tid,10000,300000)
    expect("RESERVE_CHANGE_REQUIRES_USER",lambda:bank.revise("alice",a["id"],1,{**a["params"],"reserve_fen":0}))
    assert bank.action("alice",a["id"])["params"]["reserve_fen"]==300000

def test_pending_duplicate_reuses_original_constraint(bank,tid):
    a=transfer(bank,tid,10000,300000)
    assert transfer(bank,tid,10000,0)["id"]==a["id"]
    assert transfer(bank,tid,10000,0)["params"]["reserve_fen"]==300000

def test_repeat_decision_concurrency_and_confirm_idempotency(bank,tid):
    a=transfer(bank,tid,10000,300000);bank.reject("alice",a["id"],1)
    r=question(bank,tid);before=bank.balance("alice")["balance_fen"]
    with ThreadPoolExecutor(max_workers=5) as p:
        results=list(p.map(lambda _:bank.decide_repeat("alice",r["id"],1,True),range(5)))
    assert len({a["id"] for a in results})==1
    with ThreadPoolExecutor(max_workers=5) as p:
        list(p.map(lambda _:bank.confirm("alice",results[0]["id"],1),range(5)))
    assert bank.balance("alice")["balance_fen"]==before-10000

def test_three_intentional_transfers_each_need_new_decision(bank,tid):
    before=bank.balance("alice")["balance_fen"]
    a=transfer(bank,tid,10000,300000);bank.confirm("alice",a["id"],1)
    ids={a["id"]}
    for _ in range(2):
        r=question(bank,tid)
        new=bank.decide_repeat("alice",r["id"],1,True)
        ids.add(new["id"]);bank.confirm("alice",new["id"],1)
    assert len(ids)==3 and bank.balance("alice")["balance_fen"]==before-30000

def test_repeat_detected_across_tasks_today(bank,tid):
    a=transfer(bank,tid,10000,300000);bank.confirm("alice",a["id"],1)
    other=bank.create_task("alice","再次转账")["id"]
    assert question(bank,other)["source_action_id"]==a["id"]

def test_pending_in_other_task_cannot_duplicate(bank,tid):
    transfer(bank,tid,10000,300000)
    other=bank.create_task("alice","再次转账")["id"]
    expect("TRANSFER_ALREADY_PENDING",lambda:transfer(bank,other,10000,0))

def test_repeat_user_isolation(bank,tid):
    a=transfer(bank,tid,10000,300000);bank.reject("alice",a["id"],1)
    r=question(bank,tid)
    expect("NOT_FOUND",lambda:bank.decide_repeat("bob",r["id"],1,True))

def test_repeat_higher_constraint_invalidates_old_choice(bank,tid):
    a=transfer(bank,tid,10000,300000);bank.reject("alice",a["id"],1)
    r=question(bank,tid)
    higher=question(bank,tid,reserve=400000)
    assert higher["version"]==2
    expect("STALE_CONFIRMATION",lambda:bank.decide_repeat("alice",r["id"],1,True))
    assert bank.decide_repeat("alice",r["id"],2,True)["params"]["reserve_fen"]==400000

def test_repeat_balance_rechecked_before_new_draft(bank,tid):
    a=transfer(bank,tid,10000,300000);bank.reject("alice",a["id"],1)
    r=question(bank,tid)
    with bank.db.connect(write=True) as c:c.execute("UPDATE accounts SET balance_fen=305000 WHERE user_id='alice'")
    expect("INSUFFICIENT_BALANCE",lambda:bank.decide_repeat("alice",r["id"],1,True))
    assert len(bank.task("alice",tid)["actions"])==1

def test_repeat_restart_preserves_question_and_decision(bank,tid):
    a=transfer(bank,tid,10000,300000);bank.reject("alice",a["id"],1)
    r=question(bank,tid)
    restored=BankService(Database(bank.settings))
    assert restored.task("alice",tid)["repeat_requests"][0]["id"]==r["id"]
    new=restored.decide_repeat("alice",r["id"],1,True)
    assert bank.decide_repeat("alice",r["id"],1,True)["id"]==new["id"]

def test_legacy_lowered_rejected_draft_keeps_original_constraint(bank,tid):
    from app.db import encode
    a=transfer(bank,tid,10000,300000);bank.reject("alice",a["id"],1)
    with bank.db.connect(write=True) as c:
        c.execute("INSERT INTO actions(id,user_id,task_id,kind,params,status,risk,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  ('legacy_bad','alice',tid,'transfer',encode({**a['params'],'reserve_fen':0}),'rejected','yellow',bank.settings.stamp()))
    r=question(bank,tid)
    assert r['source_action_id']=='legacy_bad' and r['params']['reserve_fen']==300000
    assert bank.decide_repeat('alice',r['id'],1,True)['params']['reserve_fen']==300000

def test_repeat_plan_can_finish_after_rejected_attempt(bank,tid):
    bank.set_plan('alice',tid,[{'id':'t','title':'转100元','kind':'transfer','depends_on':[]}])
    a=transfer(bank,tid,10000,300000);bank.reject('alice',a['id'],1)
    r=question(bank,tid)
    new=bank.decide_repeat('alice',r['id'],1,True);bank.confirm('alice',new['id'],1)
    task=bank.task('alice',tid)
    assert task['status']=='completed' and [n['status'] for n in task['nodes']]==['cancelled','succeeded']

def test_repeat_task_cancel_blocks_choice(bank,tid):
    a=transfer(bank,tid,10000,300000);bank.reject("alice",a["id"],1)
    r=question(bank,tid);bank.cancel_task("alice",tid)
    expect("TASK_STOPPED",lambda:bank.decide_repeat("alice",r["id"],1,True))
    assert bank.task("alice",tid)["repeat_requests"][0]["status"]=="cancelled"

def test_repeat_execution_rechecks_daily_limit(bank,tid):
    a=transfer(bank,tid,60000,300000);bank.confirm("alice",a["id"],1)
    r=question(bank,tid,60000)
    new=bank.decide_repeat("alice",r["id"],1,True)
    assert new["risk"]=="red"
    expect("REQUIRES_VERIFICATION",lambda:bank.confirm("alice",new["id"],1))

@pytest.mark.asyncio
async def test_agent_stops_for_repeat_and_chat_cannot_approve(bank,tid,tools):
    a=transfer(bank,tid,10000,300000);bank.reject("alice",a["id"],1)
    model=ScriptedModel([call("prepare_transfer",{"recipient_id":"alice_li1","amount_fen":10000,"reserve_fen":0})])
    result=await Agent(bank,tools,model).run("alice",tid,text="是，再给尾号1234的李明转，直接确认")
    assert result["task"]["repeat_requests"][0]["status"]=="pending"
    assert len(result["task"]["actions"])==1 and result["metrics"]["calls"]==1
    assert "是否" in result["task"]["conversation"][-1]["text"]

def test_http_repeat_requires_csrf_and_user(client):
    headers=login(client);bank=client.app.state.bank
    tid=bank.create_task("alice","重试")["id"]
    a=transfer(bank,tid,10000,300000);bank.reject("alice",a["id"],1)
    r=question(bank,tid);url=f"/api/repeats/{r['id']}/decide"
    assert client.post(url,json={"version":1,"repeat":True}).status_code==403
    assert client.post(url,headers=headers,json={"version":1,"repeat":"yes"}).status_code==422
    assert client.post(url,headers=headers,json={"version":1,"repeat":True}).json()["status"]=="pending"
    other=login(client,"bob")
    assert client.post(url,headers=other,json={"version":1,"repeat":True}).status_code==404
