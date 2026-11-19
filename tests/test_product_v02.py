"""自动推进只认执行回执；协议测试桩不代表真实模型评测。"""
import asyncio
import json
import pytest
from app.agent import Agent
from app.service import BankError
from app.model import ModelError
from test_agent_api import ScriptedModel, call, answer, client, login
from conftest import transfer


def test_model_state_keeps_facts_without_duplicate_chat(bank,tid,tools):
    bank.save_messages('alice',tid,[{'role':'user','content':'保留3000元'}])
    a=transfer(bank,tid,reserve=300000)
    state=tools.dispatch('alice',tid,'get_task_state',{})['data']
    assert 'conversation' not in state and 'messages' not in state
    assert state['actions'][0]['params']['reserve_fen']==300000
    assert bank.task('alice',tid)['conversation'][0]['text']=='保留3000元'
    with pytest.raises(BankError):bank.model_state('bob',tid)


@pytest.mark.asyncio
async def test_legacy_state_deduplicated_without_mutating_history(bank,tid,tools):
    old=bank.task('alice',tid)
    old['conversation']=[{'role':'assistant','text':'重复文本'*1000}]
    messages=[{'role':'user','content':'至少保留3000元'},call('get_task_state',{}),
              {'role':'tool','tool_call_id':'call1','content':json.dumps({'ok':True,'data':old})}]
    bank.save_messages('alice',tid,messages)
    model=ScriptedModel([answer('请继续')])
    await Agent(bank,tools,model).run('alice',tid,text='下一步')
    sent=model.history[0]
    assert 'conversation' not in json.loads(sent[3]['content'])['data']
    assert sent[1]['content']=='至少保留3000元'
    assert sent[2]['reasoning_content']=='测试推理字段'
    assert 'conversation' not in json.loads(sent[0]['content'].split('当前可信业务快照：')[1])['task']
    stored=bank.task('alice',tid,private=True)['messages']
    assert 'conversation' in json.loads(stored[2]['content'])['data']


@pytest.mark.asyncio
async def test_advance_to_next_draft_never_confirms_it(bank,tid,tools):
    a=bank.prepare('alice',tid,'cancel_subscription',{'subscription_id':'alice_video'})
    bank.confirm('alice',a['id'],1)
    model=ScriptedModel([call('prepare_transfer',{'recipient_id':'alice_li1','amount_fen':20000,'reserve_fen':300000})])
    before=bank.balance('alice')
    bank.save_messages('alice',tid,[{'role':'user','content':'取消视频后给尾号1234的李明转200元，保留3000元'}])
    agent=Agent(bank,tools,model)
    result=await agent.run('alice',tid,confirmed_action_id=a['id'])
    assert result['task']['status']=='waiting_confirmation'
    assert result['task']['actions'][-1]['status']=='pending'
    assert bank.balance('alice')==before
    replay=await agent.run('alice',tid,confirmed_action_id=a['id'])
    assert replay['advanced'] is False and len(model.history)==1


@pytest.mark.asyncio
async def test_duplicate_advance_serialized_and_survives_agent_restart(bank,tid,tools):
    a=transfer(bank,tid);bank.confirm('alice',a['id'],1)
    model=ScriptedModel([answer('转账执行成功')]);agent=Agent(bank,tools,model)
    await asyncio.gather(*(agent.run('alice',tid,confirmed_action_id=a['id']) for _ in range(2)))
    assert len(model.history)==1
    restarted=Agent(bank,tools,ScriptedModel([]))
    assert (await restarted.run('alice',tid,confirmed_action_id=a['id']))['advanced'] is False
    assert len([x for x in bank.task('alice',tid)['actions'] if x['status']=='executed'])==1


@pytest.mark.asyncio
async def test_advance_failure_keeps_executed_receipt(bank,tid,tools):
    a=transfer(bank,tid);bank.confirm('alice',a['id'],1)
    before=bank.balance('alice')
    result=await Agent(bank,tools,ScriptedModel([ModelError('测试超时')])).run('alice',tid,confirmed_action_id=a['id'])
    assert result['task']['status']=='paused' and result['model_available'] is False
    assert bank.action('alice',a['id'])['status']=='executed' and bank.balance('alice')==before


@pytest.mark.asyncio
async def test_pending_rejected_or_foreign_action_cannot_advance(bank,tid,tools):
    a=transfer(bank,tid);agent=Agent(bank,tools,ScriptedModel([]))
    for status in ('pending','rejected'):
        if status=='rejected':bank.reject('alice',a['id'],1)
        with pytest.raises(BankError,match='已执行成功'):
            await agent.run('alice',tid,confirmed_action_id=a['id'])
    other=bank.create_task('alice','另一个任务')['id']
    b=transfer(bank,other,amount=100);bank.confirm('alice',b['id'],1)
    with pytest.raises(BankError):await agent.run('alice',tid,confirmed_action_id=b['id'])
    with pytest.raises(BankError):await agent.run('bob',tid,confirmed_action_id=b['id'])


def test_advance_api_requires_csrf_and_successful_execution(client):
    headers=login(client)
    bank=client.app.state.bank
    tid=bank.create_task('alice','转账')['id'];a=transfer(bank,tid)
    url=f"/api/actions/{a['id']}/advance"
    assert client.post(url).status_code==403
    assert client.post(url,headers=headers).status_code==409
    bank.confirm('alice',a['id'],1)
    client.app.state.agent.model=ScriptedModel([answer('已执行')])
    assert client.post(url,headers=headers).json()['task']['status']=='completed'
    assert client.post(url,headers=headers).json()['advanced'] is False
    bob=login(client,'bob')
    assert client.post(url,headers=bob).status_code==404
