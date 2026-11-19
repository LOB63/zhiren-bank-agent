import asyncio
import json
from pathlib import Path
import pytest
from app.agent import Agent
from app.service import BankError
from test_agent_api import client,login,answer
from conftest import transfer


def account_state(bank,uid):
    return {'balance':bank.balance(uid),'bills':bank.bill_report(uid,'2026-09'),
            'subscriptions':bank.subscriptions(uid),'tasks':bank.tasks(uid),'audit':bank.audit_log(uid)}


def test_reset_current_account_backups_data_and_keeps_other_user_and_session(bank,tid):
    session=bank.login('alice','demo2026')
    first=transfer(bank,tid,amount=12345);bank.confirm('alice',first['id'],1)
    sub=bank.prepare('alice',tid,'cancel_subscription',{'subscription_id':'alice_music'});bank.confirm('alice',sub['id'],1)
    pending=transfer(bank,tid,amount=1850)
    bobtid=bank.create_task('bob','另一用户的数据')['id'];other=transfer(bank,bobtid,amount=22200,uid='bob');bank.confirm('bob',other['id'],1)
    bob_before=account_state(bank,'bob')
    result=bank.reset_demo_account('alice',True)
    assert result['scope']=='current_user_only' and result['account']['balance_fen']==3030000
    assert bank.tasks('alice')==[] and all(s['status']=='active' for s in bank.subscriptions('alice'))
    assert account_state(bank,'bob')==bob_before
    assert bank.session(session['token'])['user_id']=='alice'
    for aid in [first['id'],pending['id']]:
        with pytest.raises(BankError):bank.confirm('alice',aid,1)
    saved=json.loads((bank.settings.db_path.parent/'backups'/result['backup_file']).read_text(encoding='utf-8'))
    assert saved['user_id']=='alice' and saved['tables']['accounts'][0]['balance_fen']==3030000-12345
    assert all(r['user_id']=='alice' for rows in saved['tables'].values() for r in rows)
    assert 'sessions' not in saved['tables'] and 'users' not in saved['tables']
    assert bank.audit_log('alice')[0]['event']=='demo_account_reset'


def test_reset_requires_explicit_confirmation(bank,tid):
    draft=transfer(bank,tid);before=account_state(bank,'alice')
    with pytest.raises(BankError):bank.reset_demo_account('alice',False)
    assert account_state(bank,'alice')==before and bank.action('alice',draft['id'])['status']=='pending'


def test_reset_rolls_back_if_seed_fails(bank,tid,monkeypatch):
    draft=transfer(bank,tid);bank.confirm('alice',draft['id'],1);before=account_state(bank,'alice')
    def fail(*args):raise RuntimeError('test reseed failure')
    monkeypatch.setattr(bank.db,'seed_user_ledger',fail)
    with pytest.raises(RuntimeError):bank.reset_demo_account('alice',True)
    assert account_state(bank,'alice')==before


def test_no_delete_if_backup_write_fails(bank,tid,monkeypatch):
    draft=transfer(bank,tid);bank.confirm('alice',draft['id'],1);before=account_state(bank,'alice')
    original=Path.open
    def fail_backup(path,*args,**kwargs):
        if path.parent.name=='backups':raise OSError('test backup failure')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',fail_backup)
    with pytest.raises(OSError):bank.reset_demo_account('alice',True)
    assert account_state(bank,'alice')==before


@pytest.mark.asyncio
async def test_reset_blocked_while_this_users_agent_is_running(bank,tid,tools):
    entered=asyncio.Event();release=asyncio.Event()
    class SlowModel:
        async def complete(self,*args):
            entered.set();await release.wait()
            return {'message':answer('已完成查询'),'usage':{},'finish_reason':'stop'}
    agent=Agent(bank,tools,SlowModel());running=asyncio.create_task(agent.run('alice',tid,text='查询'))
    await entered.wait()
    try:
        with pytest.raises(BankError,match='正在处理'):
            await agent.reset_demo_account('alice',True)
        # 其他用户不受这一轮模型请求阻塞。
        assert (await agent.reset_demo_account('bob',True))['user_id']=='bob'
    finally:release.set();await running
    assert (await agent.reset_demo_account('alice',True))['account']['balance_fen']==3030000


def test_model_has_no_reset_tool(bank,tid,tools):
    first=transfer(bank,tid);bank.confirm('alice',first['id'],1);before=bank.balance('alice')
    assert not tools.dispatch('alice',tid,'reset_demo_account',{'confirm':True})['ok']
    assert bank.balance('alice')==before and bank.tasks('alice')


def test_http_reset_csrf_strict_boolean_and_user_scope(client):
    headers=login(client);bank=client.app.state.bank
    tid=bank.create_task('alice','保留的测试')['id'];draft=transfer(bank,tid);bank.confirm('alice',draft['id'],1)
    assert client.post('/api/demo/reset',json={'confirm':True}).status_code==403
    assert client.post('/api/demo/reset',headers=headers,json={'confirm':'true'}).status_code==422
    assert client.post('/api/demo/reset',headers=headers,json={'confirm':True,'user_id':'bob'}).status_code==422
    assert client.post('/api/demo/reset',headers=headers,json={'confirm':False}).status_code==400
    assert bank.balance('alice')['balance_fen']==3010000
    result=client.post('/api/demo/reset',headers=headers,json={'confirm':True})
    assert result.status_code==200 and result.json()['user_id']=='alice'
    assert client.get('/api/session').status_code==200 and client.get('/api/tasks').json()==[]
