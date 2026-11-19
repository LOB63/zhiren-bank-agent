from datetime import datetime as RealDatetime
import pytest
import app.config as config
from app.config import Settings, CST
from app.service import BankError
from conftest import transfer
from test_extended_scenarios import profile, prepare, execute


class Clock(RealDatetime):
    value = RealDatetime(2026, 10, 28, 23, 59, 59, tzinfo=CST)

    @classmethod
    def now(cls, tz=None):
        return cls.value.astimezone(tz) if tz else cls.value.replace(tzinfo=None)


def test_system_clock_advances_without_restart_and_fixed_mode_stays_reproducible(monkeypatch):
    monkeypatch.setattr(config, 'datetime', Clock)
    live = Settings()
    frozen = Settings(demo_date='2026-10-11')
    Clock.value = RealDatetime(2026, 10, 28, 23, 59, 59, tzinfo=CST)
    assert live.business_date == '2026-10-28'
    assert live.stamp().startswith('2026-10-28T23:59:59')
    Clock.value = RealDatetime(2026, 10, 29, 0, 0, 0, tzinfo=CST)
    assert live.business_date == '2026-10-29'
    assert live.stamp().startswith('2026-10-29T00:00:00')
    assert frozen.business_date == '2026-10-11'


def test_live_maturity_rechecks_at_execution_without_rewriting_lots(bank, monkeypatch):
    profile(bank)
    execute(bank, prepare(bank, 'wealth_buy', {'product_id':'demo_balanced','amount_fen':100000,'reserve_fen':0}))
    bank.settings.demo_date = None
    monkeypatch.setattr(config, 'datetime', Clock)
    Clock.value = RealDatetime(2026, 10, 17, 23, 59, 59, tzinfo=CST)
    holding = lambda: next(h for h in bank.wealth('alice')['holdings'] if h['product_id']=='demo_balanced')
    assert holding()['redeemable_fen'] == 0
    with pytest.raises(BankError): prepare(bank, 'wealth_redeem', {'product_id':'demo_balanced','amount_fen':100000})
    Clock.value = RealDatetime(2026, 10, 18, 0, 0, 0, tzinfo=CST)
    assert holding()['redeemable_fen'] == 100000
    execute(bank, prepare(bank, 'wealth_redeem', {'product_id':'demo_balanced','amount_fen':100000}))
    assert holding()['principal_fen'] == 0


@pytest.mark.parametrize('elapsed,valid', [(119.999, True), (120, False), (121, False)])
def test_otp_120_second_boundary_is_enforced_by_server(bank,tid,monkeypatch,elapsed,valid):
    import app.service as service
    clock = [1000000.0]
    monkeypatch.setattr(service.time, 'time', lambda:clock[0])
    action = transfer(bank,tid,100001)
    challenge = bank.challenge('alice',action['id'],1)
    assert challenge['expires_in'] == 120
    before = bank.balance('alice')['balance_fen']
    clock[0] += elapsed
    if valid:
        assert bank.verify('alice',action['id'],1,challenge['demo_code'])['verified']
    else:
        with pytest.raises(BankError) as error: bank.verify('alice',action['id'],1,challenge['demo_code'])
        assert error.value.code == 'CODE_EXPIRED'
    assert bank.balance('alice')['balance_fen'] == before


def test_verified_code_still_expires_before_execution(bank,tid,monkeypatch):
    import app.service as service
    clock=[1000000.0]
    monkeypatch.setattr(service.time,'time',lambda:clock[0])
    action=transfer(bank,tid,100001)
    challenge=bank.challenge('alice',action['id'],1)
    bank.verify('alice',action['id'],1,challenge['demo_code'])
    clock[0]+=120
    with pytest.raises(BankError) as error: bank.confirm('alice',action['id'],1)
    assert error.value.code=='REQUIRES_VERIFICATION'
    fresh=bank.challenge('alice',action['id'],1)
    bank.verify('alice',action['id'],1,fresh['demo_code'])
    assert bank.confirm('alice',action['id'],1)['status']=='executed'


def test_contact_tool_lists_only_current_users_saved_payees(bank,tools,tid):
    result=tools.dispatch('alice',tid,'search_recipients',{'query':''})
    assert result['ok'] and len(result['data'])==3
    assert all(r['id'].startswith('alice_') for r in result['data'])
    assert len(tools.dispatch('alice',tid,'search_recipients',{'query':'李明'})['data'])==2
    assert tools.dispatch('alice',tid,'search_recipients',{'query':'7788'})['data']==[]
    assert bank.task('alice',tid)['actions']==[]
