"""真实业务状态的情境验收；协议测试桩与模型实测分开记录。"""
from datetime import date,timedelta
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.scenarios import SCENARIOS,seed_scenario_users
from app.service import BankService,BankError
from app.tools import ToolRegistry
from app.main import create_app


@pytest.fixture
def scenario_bank(tmp_path):
    settings=Settings(db_path=tmp_path/'scenarios.sqlite3',demo_date='2026-10-28',demo_scenarios=True,legacy_wealth_enabled=True)
    db=Database(settings);db.seed();seed_scenario_users(db)
    return BankService(db)


@pytest.mark.parametrize('uid',SCENARIOS)
def test_scenario_balances_reconcile_and_history_does_not_overdraw(scenario_bank,uid):
    bank=scenario_bank;context=bank.customer_context(uid)
    with bank.db.connect() as conn:
        rows=conn.execute('SELECT * FROM entries WHERE user_id=? ORDER BY at,id',(uid,)).fetchall()
    balance=context['opening_cash_fen']
    for row in rows:
        balance+=row['amount_fen'] if row['direction']=='in' else -row['amount_fen']
        assert balance>=0
    assert balance==bank.balance(uid)['balance_fen']==SCENARIOS[uid]['balance_fen']
    prior_months={r['at'][:7] for r in rows if r['at'][:7]<'2026-10'}
    assert len(prior_months)==12 and min(prior_months)=='2025-10'
    assert len(rows)>350 and len(bank.recipients(uid))==5


def test_seed_is_opt_in_idempotent_and_does_not_change_original_users(bank):
    before={u:(bank.balance(u),bank.transactions(u,'2026-09-01','2026-10-01'),bank.cards(u)) for u in ('alice','bob')}
    assert bank.customer_context('alice')['available'] is False
    seed_scenario_users(bank.db)
    first={u:bank.customer_context(u) for u in SCENARIOS}
    seed_scenario_users(bank.db);bank.db.seed()
    assert first=={u:bank.customer_context(u) for u in SCENARIOS}
    assert before=={u:(bank.balance(u),bank.transactions(u,'2026-09-01','2026-10-01'),bank.cards(u)) for u in ('alice','bob')}


def test_same_phone_tail_is_disambiguated_by_name_but_bare_tail_is_not(scenario_bank):
    bank=scenario_bank;tools=ToolRegistry(bank)
    for user_text,rid,ok in [('给手机尾号1234的李明转200元','salary_li1',True),
                             ('给手机尾号1234的王芳转200元','salary_wang',True),
                             ('给尾号1234的转200元','salary_li1',False),
                             ('给手机尾号1234的李明转200元','salary_wang',False)]:
        tid=bank.create_task('salary',user_text)['id']
        result=tools.dispatch('salary',tid,'prepare_transfer',{'recipient_id':rid,'amount_fen':20000},user_texts=[user_text])
        assert result['ok'] is ok
        if result['ok']:bank.reject('salary',result['data']['id'],1)
    assert bank.balance('salary')['balance_fen']==4200000


def test_identical_name_and_phone_requires_bank_account_tail(scenario_bank):
    bank=scenario_bank;tools=ToolRegistry(bank)
    for text,ok in [('给手机尾号9988的陈晨转20元',False),('给收款账号尾号6677的陈晨转20元',True)]:
        tid=bank.create_task('salary',text)['id']
        result=tools.dispatch('salary',tid,'prepare_transfer',{'recipient_id':'salary_chen2','amount_fen':2000},user_texts=[text])
        assert result['ok'] is ok


def test_context_is_owned_readonly_and_never_implies_completed_payment(scenario_bank):
    bank=scenario_bank;tools=ToolRegistry(bank)
    ctx=bank.customer_context('upcoming')
    assert ctx['reserve_target_fen']==1300000 and ctx['upcoming_outlays'][0]['amount_fen']==1200000
    assert ctx['upcoming_outlays'][0]['due_date']=='2026-11-04'
    bank.settings.demo_date='2026-11-05'
    ctx=bank.customer_context('upcoming')
    assert ctx['upcoming_outlays'][0]['timing_status']=='due_or_past_unverified'
    assert ctx['upcoming_outlays'][0]['payment_status']=='unverified'
    tid=bank.create_task('salary','只读资料')['id']
    assert tools.dispatch('salary',tid,'get_customer_context',{})['data']['scenario']=='salary'
    assert tools.dispatch('salary',tid,'get_customer_context',{'user_id':'upcoming'})['error']['code']=='INVALID_ARGUMENTS'
    assert 'save_customer_context' not in {x['function']['name'] for x in tools.schemas()}


def test_real_holdings_and_card_states_control_operations(scenario_bank):
    bank=scenario_bank
    tid=bank.create_task('freelance','冻结卡支付')['id']
    with pytest.raises(BankError) as err:
        bank.prepare('freelance',tid,'card_payment',{'card_id':'freelance_card2','merchant_id':'demo_supermarket','amount_fen':1000,'channel':'offline'})
    assert err.value.code=='CARD_FROZEN'
    tid=bank.create_task('upcoming','锁定持仓')['id']
    with pytest.raises(BankError) as err:
        bank.prepare('upcoming',tid,'wealth_redeem',{'product_id':'demo_growth','amount_fen':10000})
    assert err.value.code=='INSUFFICIENT_HOLDINGS'
    tid=bank.create_task('upcoming','按登记预留')['id']
    with pytest.raises(BankError) as err:
        bank.prepare('upcoming',tid,'transfer',{'recipient_id':'upcoming_wang','amount_fen':500000,'reserve_fen':1300000})
    assert err.value.code=='INSUFFICIENT_BALANCE'
    assert not bank.risk_profile('salary')['completed']


def test_reset_restores_only_selected_scenario_and_invalidates_old_actions(scenario_bank):
    bank=scenario_bank
    other=(bank.balance('freelance'),bank.cards('freelance'),bank.customer_context('freelance'))
    session=bank.login('salary','demo2026')
    tid=bank.create_task('salary','取消订阅')['id']
    a=bank.prepare('salary',tid,'cancel_subscription',{'subscription_id':'salary_music'})
    bank.confirm('salary',a['id'],1)
    result=bank.reset_demo_account('salary',True)
    assert result['account']['balance_fen']==4200000
    assert all(s['status']=='active' for s in bank.subscriptions('salary'))
    assert bank.customer_context('salary')['available'] and not bank.tasks('salary')
    assert bank.session(session['token'])['user_id']=='salary'
    assert other==(bank.balance('freelance'),bank.cards('freelance'),bank.customer_context('freelance'))
    with pytest.raises(BankError):bank.confirm('salary',a['id'],1)
    backup=json.loads((bank.settings.db_path.parent/'backups'/result['backup_file']).read_text(encoding='utf-8'))
    assert backup['tables']['customer_contexts'][0]['user_id']=='salary'


def test_scenario_cookie_and_context_api_are_isolated_from_regular_app(tmp_path):
    regular=create_app(Settings(db_path=tmp_path/'regular.sqlite3',demo_date='2026-10-28'))
    lab=create_app(Settings(db_path=tmp_path/'lab.sqlite3',demo_date='2026-10-28',demo_scenarios=True,legacy_wealth_enabled=True))
    with TestClient(regular) as first,TestClient(lab) as second:
        first.post('/api/login',json={'username':'alice','password':'demo2026'})
        second.cookies.update(first.cookies)
        assert second.get('/api/customer-context').status_code==401
        login=second.post('/api/login',json={'username':'upcoming','password':'demo2026'}).json()
        assert 'bank_scenario_session' in second.cookies
        context=second.get('/api/customer-context').json()
        assert context['scenario']=='upcoming'
        assert second.get('/api/meta').json()['scenario_mode']
        assert first.get('/api/customer-context').json()['available'] is False
        assert second.post('/api/demo/reset',json={'confirm':True}).status_code==403
        assert second.post('/api/demo/reset',headers={'X-CSRF-Token':login['csrf']},json={'confirm':True,'user_id':'salary'}).status_code==422
        assert first.post('/api/login',json={'username':'salary','password':'demo2026'}).status_code==401


@pytest.mark.parametrize('name,args',[
    ('prepare_transfer',{'recipient_id':'alice_li1','amount_fen':20000}),
    ('prepare_subscription_cancel',{'subscription_id':'alice_video'}),
])
def test_unresolved_subscription_selection_blocks_writes_until_user_selects(bank,tid,tools,name,args):
    request='取消我选中的两项，再给尾号1234的李明转200元。'
    result=tools.dispatch('alice',tid,name,args,user_texts=[request])
    assert result['error']['code']=='SELECTION_REQUIRED'
    assert not bank.task('alice',tid)['actions']
    result=tools.dispatch('alice',tid,name,args,user_texts=[request,'继续。'])
    assert result['error']['code']=='SELECTION_REQUIRED'
    result=tools.dispatch('alice',tid,name,args,user_texts=[request,'我选择星光视频和悦耳音乐。'])
    assert result['ok']
