import json
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.db import Database
from app.main import create_app
from app.service import BankError
from app.agent import Agent
from app.request_guards import guard_request
from test_agent_api import ScriptedModel


INFO={'name':'陈晨','bank_name':'模拟银行','account_number':'6210000000007788','phone':'13800009988','confirm':True}


def test_add_payee_is_persistent_masked_and_isolated(bank):
    before=bank.balance('alice')
    saved=bank.add_recipient('alice',**INFO)
    assert saved['account_tail']=='7788' and saved['phone_tail']=='9988'
    assert bank.recipients('alice','7788')==[saved]
    assert bank.recipients('bob','7788')==[]
    assert BankServiceReload(bank).recipients('alice','陈晨')==[saved]
    assert bank.balance('alice')==before
    assert INFO['account_number'] not in json.dumps(bank.audit_log('alice'))
    assert INFO['phone'] not in json.dumps(saved)
    with pytest.raises(BankError) as error:bank.add_recipient('alice',**INFO)
    assert error.value.code=='RECIPIENT_EXISTS'
    assert bank.add_recipient('bob',**INFO)['id']!=saved['id']


def BankServiceReload(bank):
    from app.service import BankService
    return BankService(Database(bank.settings))


@pytest.mark.parametrize('field,value',[('account_number','abc'),('account_number','123'),('phone','123456'),('phone','12345678901'),('phone','1380000998a'),('phone','138 00009988'),('phone','138000099889'),('name',' '),('bank_name',' '),('confirm',False)])
def test_invalid_payee_never_writes(bank,field,value):
    before=bank.recipients('alice')
    with pytest.raises(BankError):bank.add_recipient('alice',**{**INFO,field:value})
    assert bank.recipients('alice')==before


def test_new_payee_transfer_has_bank_identity_and_still_needs_confirmation(bank,tid,tools):
    saved=bank.add_recipient('alice',**{**INFO,'name':'李明','phone':'13800001234'})
    args={'recipient_id':saved['id'],'amount_fen':10000,'reserve_fen':0}
    with pytest.raises(BankError):guard_request(bank,'alice','prepare_transfer',args,['给李明转100元'])
    result=tools.dispatch('alice',tid,'prepare_transfer',args,user_texts=['给收款账号尾号7788的李明转100元'])
    assert result['ok'],result
    assert result['data']['recipient']['bank_name']=='模拟银行'
    assert result['data']['recipient']['account_tail']=='7788'
    assert bank.balance('alice')['balance_fen']==3030000
    bank.confirm('alice',result['data']['id'],1)
    assert bank.balance('alice')['balance_fen']==3020000


def test_unknown_account_tail_cannot_fall_back_to_unique_name(bank):
    saved=bank.add_recipient('alice',**INFO)
    args={'recipient_id':saved['id'],'amount_fen':10000,'reserve_fen':0}
    with pytest.raises(BankError) as error:guard_request(bank,'alice','prepare_transfer',args,['给收款账号尾号9999的陈晨转100元'])
    assert error.value.code=='RECIPIENT_MISMATCH'


def test_reset_clears_only_current_users_added_payees(bank):
    bank.add_recipient('alice',**INFO);other=bank.add_recipient('bob',**INFO)
    bank.reset_demo_account('alice',True)
    assert bank.recipients('alice','7788')==[]
    assert bank.recipients('bob','7788')==[other]


def test_payee_endpoint_requires_session_csrf_strict_form_and_masks_account(tmp_path):
    with TestClient(create_app(Settings(db_path=tmp_path/'web.sqlite3',demo_date='2026-10-11'))) as client:
        assert client.post('/api/recipients',json=INFO).status_code==401
        login=client.post('/api/login',json={'username':'alice','password':'demo2026'}).json();headers={'X-CSRF-Token':login['csrf']}
        assert client.post('/api/recipients',json=INFO).status_code==403
        assert client.post('/api/recipients',headers=headers,json={**INFO,'user_id':'bob'}).status_code==422
        assert client.post('/api/recipients',headers=headers,json={**INFO,'confirm':'true'}).status_code==422
        saved=client.post('/api/recipients',headers=headers,json=INFO)
        assert saved.status_code==200
        assert INFO['account_number'] not in saved.text
        assert len(client.get('/api/recipients?query=7788').json())==1


@pytest.mark.asyncio
async def test_simple_cancel_completion_uses_real_business_receipt_without_balance_boilerplate(bank,tools):
    tid=bank.create_task('alice','取消悦耳音乐的续费。')['id']
    action=bank.prepare('alice',tid,'cancel_subscription',{'subscription_id':'alice_music'})
    bank.confirm('alice',action['id'],1)
    model=ScriptedModel([]);agent=Agent(bank,tools,model)
    response=await agent.run('alice',tid,confirmed_action_id=action['id'])
    assert response['reply_source']=='business_receipt'
    assert response['task']['conversation'][-1]['text']=='悦耳音乐续费已取消。'
    assert not model.history and response['metrics']['calls']==0
    assert (await agent.run('alice',tid,confirmed_action_id=action['id']))['advanced'] is False
