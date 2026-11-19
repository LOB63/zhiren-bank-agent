import pytest
from app.errors import BankError
from scripts.risk_test_fixture import assessment_fixture

def save(bank,uid='alice',**changes):
    return bank.save_risk_profile(uid,{'loss_tolerance':'substantial','experience':'experienced',
        'horizon_days':365,'confirm':True,'assessment':assessment_fixture(**changes)})

@pytest.mark.parametrize('change',[{'loss_capacity':'none'},{'age_band':'minor'}])
def test_capacity_overrides_willingness(bank,change):
    save(bank,**change)
    assert not any(p['eligible'] for p in bank.wealth('alice')['products'])
    assert bank.wealth('alice')['recommendation']['suggested_trial_fen']==0

@pytest.mark.parametrize('change',[{'emergency':'under1'},{'debt':'high'}])
def test_urgent_finances_receive_no_new_investment_advice(bank,change):
    p=save(bank,**change);assert p['risk_level']==1
    assert bank.wealth('alice')['recommendation']['suggested_trial_fen']==0

@pytest.mark.parametrize('change',[{'monthly_essential_fen':True},{'monthly_essential_fen':0},{'monthly_essential_fen':1.2},{'goal':'invalid'},{'user_id':'bob'}])
def test_invalid_or_extra_answers_do_not_save(bank,change):
    with pytest.raises(BankError):save(bank,**change)
    assert not bank.risk_profile('alice')['completed']

def test_advice_reads_liabilities_and_is_not_execution(bank):
    before=bank.balance('alice')['balance_fen'];save(bank)
    w=bank.wealth('alice');r=w['recommendation']
    assert r['reserve_fen']==900000 and 0<r['suggested_trial_fen']<=r['cash_ceiling_fen']
    assert bank.balance('alice')['balance_fen']==before
    with bank.db.connect() as c:assert c.execute('SELECT count(*) FROM actions').fetchone()[0]==0

def test_legacy_profile_must_be_reassessed(bank):
    save(bank)
    with bank.db.connect(write=True) as c:c.execute('UPDATE risk_profiles SET assessment_json=NULL')
    assert not bank.risk_profile('alice')['assessment_current']
    assert not any(p['eligible'] for p in bank.wealth('alice')['products'])

def test_new_assessment_invalidates_pending_purchase(bank):
    save(bank);tid=bank.create_task('alice','测试')['id']
    a=bank.prepare('alice',tid,'wealth_buy',{'product_id':'demo_cash','amount_fen':10000,'reserve_fen':0})
    save(bank,loss_capacity='small')
    with pytest.raises(BankError) as e:bank.confirm('alice',a['id'],a['version'],True)
    assert e.value.code=='PROFILE_CHANGED'

def test_large_upcoming_outlay_means_no_new_investment(bank,tmp_path):
    from app.config import Settings
    from app.main import create_app
    app=create_app(Settings(db_path=tmp_path/'s.sqlite3',demo_date='2026-10-29',demo_scenarios=True))
    b=app.state.bank;save(b,'upcoming')
    r=b.wealth('upcoming')['recommendation']
    assert r['cash_ceiling_fen']==0 and r['suggested_trial_fen']==0

def test_expired_profile_has_no_advice(bank):
    save(bank)
    with bank.db.connect(write=True) as c:c.execute("UPDATE risk_profiles SET expires_at='2020-01-01'")
    assert not bank.wealth('alice')['recommendation']['available']
