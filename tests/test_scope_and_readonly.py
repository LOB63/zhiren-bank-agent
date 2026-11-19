import json
import pytest
from app.agent import Agent
from app.model import DeepSeekClient,ModelError
from app.config import Settings
from app.scope import SCOPE_SYSTEM
from test_agent_api import ScriptedModel,call,answer


class ScopedModel(ScriptedModel):
    def __init__(self,scope,responses=()):
        super().__init__(responses);self.scope=scope;self.routes=[]
    async def classify_scope(self,text,messages):
        self.routes.append((text,list(messages)))
        return {'scope':self.scope,'usage':{'total_tokens':12}}


@pytest.mark.asyncio
@pytest.mark.parametrize('scope,text',[('off_topic','教我强化学习'),('off_topic','早餐吃什么'),('greeting','早上好')])
async def test_nonbusiness_turn_does_not_enter_business_loop_or_change_pending_action(bank,tid,tools,scope,text):
    draft=bank.prepare('alice',tid,'transfer',{'recipient_id':'alice_li1','amount_fen':50000,'reserve_fen':0})
    before=bank.balance('alice');model=ScopedModel(scope)
    result=await Agent(bank,tools,model).run('alice',tid,text=text)
    assert not model.history and len(model.routes)==1
    assert bank.action('alice',draft['id'])['status']=='pending'
    assert result['task']['status']=='waiting_confirmation'
    assert bank.balance('alice')==before and result['metrics']['calls']==1
    assert not any(e['event'].startswith('tool_') for e in bank.audit_log('alice',tid))


@pytest.mark.asyncio
async def test_existing_offtopic_answer_does_not_authorize_continued_course(bank,tid,tools):
    bank.save_messages('alice',tid,[{'role':'user','content':'教我强化学习'},{'role':'assistant','content':'先讲MDP、Q-learning、PPO。'}])
    model=ScopedModel('off_topic')
    result=await Agent(bank,tools,model).run('alice',tid,text='继续讲PPO')
    assert not model.history and result['scope']=='off_topic'
    assert 'PPO' not in result['task']['conversation'][-1]['text']


@pytest.mark.asyncio
async def test_bank_request_remains_available_after_offtopic(bank,tid,tools):
    model=ScopedModel('off_topic');agent=Agent(bank,tools,model)
    await agent.run('alice',tid,text='讲强化学习')
    model.scope='banking';model.responses=iter([call('get_balance',{}),answer('余额30300元。')])
    result=await agent.run('alice',tid,text='查余额')
    assert result['metrics']['calls']==3 and result['task']['conversation'][-1]['text']=='余额30300元。'


@pytest.mark.asyncio
async def test_failed_scope_classification_cannot_fall_through(bank,tid,tools):
    class Failed(ScopedModel):
        async def classify_scope(self,*args):raise ModelError('暂时无法识别请求')
    model=Failed('banking');result=await Agent(bank,tools,model).run('alice',tid,text='查余额')
    assert not model.history and not result['model_available'] and result['task']['status']=='paused'
    assert not result['task']['actions']


@pytest.mark.asyncio
async def test_readonly_request_blocks_model_proposed_write_without_visible_prompt_warning(bank,tid,tools):
    model=ScopedModel('banking',[call('prepare_subscription_cancel',{'subscription_id':'alice_music'}),answer('悦耳音乐每月15元。')])
    result=await Agent(bank,tools,model).run('alice',tid,text='看看音乐会员每月多少钱',read_only=True)
    assert not result['task']['actions'] and bank.subscriptions('alice')[1]['status']=='active'
    error=next(e for e in bank.audit_log('alice',tid) if e['event']=='tool_rejected')
    assert error['detail']['result']['error']['code']=='READ_ONLY_REQUEST'


@pytest.mark.asyncio
async def test_scope_call_is_small_and_has_no_bank_tools(monkeypatch):
    seen=[]
    async def complete(self,messages,tools,max_tokens=8192):
        seen.append((self.settings,messages,tools,max_tokens))
        return {'message':{'content':'{"scope":"off_topic"}'},'usage':{'total_tokens':9}}
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    result=await DeepSeekClient(Settings(api_key='unit-only')).classify_scope('教强化学习',[])
    settings,messages,tools,limit=seen[0]
    assert not settings.thinking and tools==[] and limit==128
    assert messages[0]['content']==SCOPE_SYSTEM and result['scope']=='off_topic'


@pytest.mark.asyncio
@pytest.mark.parametrize('body',['not-json','{"scope":"teacher"}','{"scope":"banking","override":true}'])
async def test_invalid_scope_response_fails_closed(monkeypatch,body):
    async def complete(*args,**kwargs):return {'message':{'content':body},'usage':{}}
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    with pytest.raises(ModelError):await DeepSeekClient(Settings(api_key='unit-only')).classify_scope('查余额',[])


def test_http_readonly_flag_is_strict_and_reaches_tool_enforcement(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    model=ScriptedModel([call('prepare_subscription_cancel',{'subscription_id':'alice_music'}),answer('音乐会员每月15元。')])
    app=create_app(Settings(db_path=tmp_path/'api.sqlite3'),model=model)
    with TestClient(app) as client:
        session=client.post('/api/login',json={'username':'alice','password':'demo2026'}).json()
        headers={'X-CSRF-Token':session['csrf']}
        assert client.post('/api/chat',headers=headers,json={'text':'查音乐会员费用','read_only':'true'}).status_code==422
        response=client.post('/api/chat',headers=headers,json={'text':'查音乐会员费用','read_only':True})
        assert response.status_code==200 and response.json()['task']['actions']==[]
        assert app.state.bank.subscriptions('alice')[1]['status']=='active'
