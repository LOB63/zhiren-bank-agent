"""Current release: simulated wealth trading removed, real products remain read-only."""
import pytest
from app.config import Settings
from app.db import Database
from app.service import BankService, BankError
from app.tools import ToolRegistry
from scripts.risk_test_fixture import assessment_fixture


def current_bank(tmp_path):
    db=Database(Settings(db_path=tmp_path/'current.sqlite3',demo_date='2026-10-31'))
    db.seed()
    return BankService(db)


def test_current_tool_catalog_has_no_simulated_trading(tmp_path):
    bank=current_bank(tmp_path);tools=ToolRegistry(bank)
    names={x['function']['name'] for x in tools.schemas()}
    assert {'get_bank_products','get_wealth_state'}<=names
    assert not {'prepare_wealth_buy','prepare_wealth_redeem'}&names
    tid=bank.create_task('alice','买模拟灵活现金100元')['id']
    result=tools.dispatch('alice',tid,'prepare_wealth_buy',{'product_id':'demo_cash','amount_fen':10000,'reserve_fen':0})
    assert result['error']['code']=='TOOL_NOT_ALLOWED'
    assert not bank.task('alice',tid)['actions']


@pytest.mark.parametrize('kind',['wealth_buy','wealth_redeem'])
def test_direct_service_cannot_create_removed_operations(tmp_path,kind):
    bank=current_bank(tmp_path);tid=bank.create_task('alice','历史模拟交易')['id']
    before=bank.balance('alice')
    with pytest.raises(BankError) as error:
        bank.prepare('alice',tid,kind,{'product_id':'demo_cash','amount_fen':10000,'reserve_fen':0})
    assert error.value.code=='FEATURE_REMOVED'
    assert bank.balance('alice')==before
    assert not bank.task('alice',tid)['actions']


def test_old_pending_draft_cannot_execute_after_feature_removed(tmp_path):
    bank=current_bank(tmp_path)
    bank.settings.legacy_wealth_enabled=True
    bank.save_risk_profile('alice',{'confirm':True,'assessment':assessment_fixture(),
        'loss_tolerance':'substantial','experience':'experienced','horizon_days':365})
    tid=bank.create_task('alice','历史待确认申购')['id']
    action=bank.prepare('alice',tid,'wealth_buy',{'product_id':'demo_cash','amount_fen':10000,'reserve_fen':0})
    bank.settings.legacy_wealth_enabled=False
    before=bank.balance('alice')
    with pytest.raises(BankError) as error:bank.confirm('alice',action['id'],1,True)
    assert error.value.code=='FEATURE_REMOVED'
    assert bank.balance('alice')==before
    assert bank.action('alice',action['id'])['status']=='pending'
    assert bank.reject('alice',action['id'],1)['status']=='rejected'


def test_assessment_advice_remains_without_mock_product_recommendation(tmp_path):
    bank=current_bank(tmp_path)
    before=bank.balance('alice')
    bank.save_risk_profile('alice',{'confirm':True,'assessment':assessment_fixture(),
        'loss_tolerance':'substantial','experience':'experienced','horizon_days':365})
    state=bank.wealth('alice');rec=state['recommendation']
    assert state['products']==[] and state['trading_enabled'] is False
    assert rec['available'] and rec['suggested_trial_fen']>0
    assert rec['preferred_product_id'] is None and '模拟灵活现金' not in rec['summary']
    assert bank.balance('alice')==before
