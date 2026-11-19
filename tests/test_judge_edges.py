"""评委式边界检查；全部使用独立临时账本，模型故障为明确标注的协议测试桩。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent import Agent
from app.config import Settings
from app.main import create_app
from app.model import DeepSeekClient, ModelError
from app.service import BankError
from conftest import transfer


def test_cancelled_task_cannot_be_resurrected_by_late_tool_errors(bank, tid, tools):
    bank.cancel_task('alice', tid)
    for _ in range(4):
        tools.dispatch('alice', tid, 'get_balance', {})
    assert bank.task('alice', tid)['status'] == 'cancelled'
    with pytest.raises(BankError) as error:
        bank.resume_task('alice', tid)
    assert error.value.code == 'TASK_CANCELLED'


@pytest.mark.asyncio
async def test_cancel_during_model_request_discards_late_tools(bank, tid, tools):
    started, release = asyncio.Event(), asyncio.Event()
    class DelayedModel:
        async def complete(self, messages, schemas):
            started.set()
            await release.wait()
            return {'message': {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': f'late_{i}', 'type': 'function', 'function': {
                    'name': 'get_balance', 'arguments': '{}'}} for i in range(4)]},
                'usage': {}, 'finish_reason': 'tool_calls'}
    task = asyncio.create_task(Agent(bank, tools, DelayedModel()).run('alice', tid, text='查询余额'))
    await started.wait()
    bank.cancel_task('alice', tid)
    release.set()
    result = await task
    assert result['task']['status'] == 'cancelled'
    assert not any(e['event'] in ('tool_completed', 'tool_rejected') for e in bank.audit_log('alice', tid))
    assert bank.balance('alice')['balance_fen'] == 3030000


@pytest.mark.asyncio
async def test_truncated_tool_output_does_not_poison_resume_history(bank, tid, tools):
    class TruncatedModel:
        async def complete(self, messages, schemas):
            return {'message': {'role':'assistant','content':None,'tool_calls':[
                {'id':'partial_tool','type':'function','function':{
                    'name':'prepare_transfer','arguments':'{"recipient_id":'}}]},
                'finish_reason':'length','usage':{}}
    result=await Agent(bank,tools,TruncatedModel()).run('alice',tid,text='给尾号1234的李明转200元')
    assert result['task']['status']=='paused'
    assert not result['task']['actions']
    bank.resume_task('alice',tid)
    class ResumeModel:
        async def complete(self,messages,schemas):
            assert not any(c['id']=='partial_tool' for m in messages for c in m.get('tool_calls',[]))
            return {'message':{'role':'assistant','content':'请重新核对金额。'},'usage':{},'finish_reason':'stop'}
    restored=await Agent(bank,tools,ResumeModel()).run('alice',tid,text='先不要转，查清状态。')
    assert restored['model_available']
    assert bank.balance('alice')['balance_fen']==3030000


@pytest.mark.parametrize('start,end', [
    ('20260901', '20261001'), ('2026-W36-2', '2026-10-01'),
    ('2026-09-01', '20261001'),
])
def test_dates_must_match_documented_format(bank, start, end):
    with pytest.raises(BankError) as error:
        bank.transactions('alice', start, end)
    assert error.value.code == 'INVALID_DATE'


def test_maximum_year_month_returns_business_error_not_server_crash(bank):
    with pytest.raises(BankError) as error:
        bank.bill_report('alice', '9999-12')
    assert error.value.code == 'INVALID_MONTH'


def test_category_evidence_contains_only_its_actual_expense_records(bank):
    report=bank.bill_report('alice','2026-09')
    rows={r['id']:r for r in bank.transactions('alice','2026-09-01','2026-10-01')}
    for category in report['categories']:
        evidence=[rows[eid] for eid in category['entry_ids']]
        assert category['transaction_count']==len(evidence)
        assert sum(r['amount_fen'] for r in evidence)==category['amount_fen']
        assert all(r['direction']=='out' and r['category']==category['name'] for r in evidence)
    meals=next(c for c in report['categories'] if c['name']=='餐饮')
    assert meals['transaction_count']==9
    assert 'seed_alice_24320_0' not in meals['entry_ids']


@pytest.mark.parametrize('followup', ['尾号1234那个。','现在办理，还是刚才那个资金来源。','不要改用账户余额，给尾号1234那个。'])
def test_credit_funding_persists_across_clarifications(bank,tid,tools,followup):
    result=tools.dispatch('alice',tid,'prepare_transfer',{'recipient_id':'alice_li1','amount_fen':6000},
                          user_texts=['用信用卡给李明转60元。','尾号1234那个。',followup])
    assert result['error']['code']=='CREDIT_SOURCE_UNSUPPORTED'
    assert not bank.task('alice',tid)['actions']


@pytest.mark.parametrize('last',['还是用信用卡。','不是用现金，还是用信用卡。','以前用现金，这次用信用卡转账。'])
def test_later_credit_choice_supersedes_previous_cash_choice(bank,tid,tools,last):
    result=tools.dispatch('alice',tid,'prepare_transfer',{'recipient_id':'alice_li1','amount_fen':6000},
                          user_texts=['改用账户余额给尾号1234的李明转60元。',last])
    assert result['error']['code']=='CREDIT_SOURCE_UNSUPPORTED'
    assert not bank.task('alice',tid)['actions']


@pytest.mark.parametrize('cash', ['改用账户余额','用现金','从我的储蓄'])
def test_explicit_cash_change_survives_later_clarification(bank,tid,tools,cash):
    result=tools.dispatch('alice',tid,'prepare_transfer',{'recipient_id':'alice_li1','amount_fen':6000},
                          user_texts=['用信用卡给李明转60元。',cash,'尾号1234那个。'])
    assert result['ok'] and result['data']['status']=='pending'
    assert bank.balance('alice')['balance_fen']==3030000


@pytest.fixture
def judge_app(tmp_path):
    return create_app(Settings(db_path=tmp_path/'judge.sqlite3', demo_date='2026-10-11'))


def signed(client, user='alice'):
    result = client.post('/api/login', json={'username': user, 'password': 'demo2026'})
    assert result.status_code == 200
    return {'X-CSRF-Token': result.json()['csrf']}


@pytest.mark.parametrize('endpoint', [
    '/api/tasks/{tid}', '/api/actions/{aid}', '/api/audit?task_id={tid}',
    '/api/actions/{aid}/confirm', '/api/actions/{aid}/reject',
    '/api/actions/{aid}/revise', '/api/actions/{aid}/challenge',
    '/api/actions/{aid}/verify', '/api/actions/{aid}/advance',
    '/api/tasks/{tid}/cancel', '/api/tasks/{tid}/resume', '/api/tasks/{tid}/continue',
])
def test_every_object_route_rejects_other_user(judge_app, endpoint):
    bank = judge_app.state.bank
    tid = bank.create_task('alice', '跨用户检查')['id']
    action = transfer(bank, tid)
    before = bank.balance('alice')['balance_fen']
    with TestClient(judge_app) as client:
        headers = signed(client, 'bob')
        path = endpoint.format(tid=tid, aid=action['id'])
        if path.endswith('/revise'):
            body = {'version': 1, 'recipient_id': 'bob_li1', 'amount_fen': 1, 'reserve_fen': 0}
        elif path.endswith('/verify'):
            body = {'version': 1, 'code': '000000'}
        else:
            body = {'version': 1} if any(path.endswith('/'+v) for v in ('confirm','reject','challenge')) else {}
        result = client.get(path) if endpoint in ('/api/tasks/{tid}', '/api/actions/{aid}', '/api/audit?task_id={tid}') else client.post(path, headers=headers, json=body)
        assert result.status_code == 404
    assert bank.balance('alice')['balance_fen'] == before
    assert bank.action('alice', action['id'])['status'] == 'pending'


def test_foreign_repeat_decision_cannot_create_action(judge_app):
    bank = judge_app.state.bank
    tid = bank.create_task('alice', '重复询问')['id']
    first = transfer(bank, tid)
    bank.confirm('alice', first['id'], 1)
    repeat = transfer(bank, tid)['repeat_request']
    with TestClient(judge_app) as client:
        headers = signed(client, 'bob')
        result = client.post('/api/repeats/'+repeat['id']+'/decide', headers=headers, json={'version': 1, 'repeat': True})
        assert result.status_code == 404
    assert len(bank.task('alice', tid)['actions']) == 1


def test_csrf_cannot_be_reused_after_logout_and_new_session(judge_app):
    with TestClient(judge_app) as client:
        old = signed(client)
        client.post('/api/logout', headers=old, json={})
        signed(client)
        assert client.post('/api/manual/tasks', headers=old, json={'text': '旧凭证'}).status_code == 403


def test_query_injection_is_data_and_sensitive_files_are_not_served(judge_app):
    with TestClient(judge_app) as client:
        signed(client)
        assert client.get('/api/recipients', params={'query': "' OR 1=1; DROP TABLE users;--"}).json() == []
        for path in ('/.env', '/data/bank.sqlite3', '/static/../.env', '/static/%2e%2e/.env'):
            assert client.get(path).status_code in (400,404)
        assert client.get('/api/account').json()['balance_fen'] == 3030000
        assert client.get('/api/bills', params={'month': '9999-12'}).status_code == 400


def test_concurrent_different_transfers_cannot_overspend_reserved_balance(bank):
    before = bank.balance('alice')['balance_fen']
    actions = [bank.prepare('alice', bank.create_task('alice', rid)['id'], 'transfer',
               {'recipient_id': rid, 'amount_fen': 2000000, 'reserve_fen': 100000})
               for rid in ('alice_li1', 'alice_wang')]
    for action in actions:
        code = bank.challenge('alice', action['id'], 1)['demo_code']
        bank.verify('alice', action['id'], 1, code)
    def confirm(action):
        try: return bank.confirm('alice', action['id'], 1)['status']
        except BankError as error: return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, actions))
    assert sorted(results) == ['INSUFFICIENT_BALANCE', 'executed']
    assert bank.balance('alice')['balance_fen'] == before-2000000


@pytest.mark.parametrize('kind', ['cancel', 'confirm'])
def test_cancel_confirm_race_has_only_committed_outcomes(bank, tid, kind):
    action = transfer(bank, tid)
    before = bank.balance('alice')['balance_fen']
    def confirm():
        try:
            return bank.confirm('alice', action['id'], 1)
        except BankError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [lambda: bank.cancel_task('alice', tid), confirm]
        if kind == 'confirm': calls.reverse()
        results = list(pool.map(lambda fn: fn(), calls))
    final = bank.action('alice', action['id'])
    assert final['status'] in ('executed', 'cancelled')
    assert bank.task('alice', tid)['status'] == 'cancelled'
    assert bank.balance('alice')['balance_fen'] == before-(20000 if final['status']=='executed' else 0)
    with bank.db.connect() as conn:
        assert conn.execute('SELECT count(*) FROM entries WHERE action_id=?', (action['id'],)).fetchone()[0] == (final['status']=='executed')


BAD_MODEL_PAYLOADS = [
    None, {'choices': None}, {'choices': [None]},
    {'choices': [{'message': None}]},
    {'choices': [{'message': {'content': ['not text']}}]},
    {'choices': [{'message': {'content': 'ok'}}], 'usage': None},
    {'choices': [{'message': {'content': 'ok'}}], 'usage': {'total_tokens': 'many'}},
    {'choices': [{'message': {'tool_calls': 'bad'}}]},
    {'choices': [{'message': {'tool_calls': [{'id': 'x', 'function': None}]}}]},
    {'choices': [{'message': {'tool_calls': [{'id': 'x', 'type': 'function', 'function': {'name': 'get_balance', 'arguments': {}}}]}}]},
    {'choices': [{'message': {'content': None, 'tool_calls': []}}]},
]


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', BAD_MODEL_PAYLOADS)
async def test_malformed_model_response_pauses_without_dispatch(bank, tid, tools, monkeypatch, payload):
    import app.model as module
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            return httpx.Response(200, content=json.dumps(payload).encode(), headers={'content-type': 'application/json'})
    monkeypatch.setattr(module.httpx, 'AsyncClient', Client)
    model = DeepSeekClient(Settings(api_key='protocol-test-only'))
    # 直接测试业务生成阶段，范围路由不属于本用例。
    class GenerationOnly:
        async def complete(self, messages, schemas): return await model.complete(messages, schemas)
    result = await Agent(bank, tools, GenerationOnly()).run('alice', tid, text='查询余额')
    assert result['model_available'] is False
    assert result['task']['status'] == 'paused'
    assert not bank.task('alice', tid)['actions']
    assert not any(r['event']=='tool_completed' for r in bank.audit_log('alice', tid))
    assert bank.balance('alice')['balance_fen'] == 3030000
