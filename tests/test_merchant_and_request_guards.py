from scripts.risk_test_fixture import assessment_fixture
import pytest
from app.service import BankError


@pytest.mark.parametrize('text', ['给李明拿500','给李明转500元','给李明转1234元'])
def test_ambiguous_person_cannot_be_selected_by_model(bank,tid,tools,text):
    out=tools.dispatch('alice',tid,'prepare_transfer',{'recipient_id':'alice_li1','amount_fen':50000,'reserve_fen':0},user_texts=[text])
    assert out['error']['code']=='RECIPIENT_AMBIGUOUS'
    assert not bank.task('alice',tid)['actions']


def test_latest_named_person_does_not_reuse_old_selection(bank,tid,tools):
    texts=['给尾号1234的李明转200元','现在给李明转500元']
    out=tools.dispatch('alice',tid,'prepare_transfer',{'recipient_id':'alice_li1','amount_fen':50000},user_texts=texts)
    assert out['error']['code']=='RECIPIENT_AMBIGUOUS'


def test_explicit_tail_clarification_allows_only_correct_recipient(bank,tid,tools):
    texts=['给李明转500元','尾号5678那个']
    out=tools.dispatch('alice',tid,'prepare_transfer',{'recipient_id':'alice_li1','amount_fen':50000},user_texts=texts)
    assert out['error']['code']=='RECIPIENT_AMBIGUOUS'
    out=tools.dispatch('alice',tid,'prepare_transfer',{'recipient_id':'alice_li2','amount_fen':50000},user_texts=texts)
    assert out['ok'] and out['data']['recipient']['phone_tail']=='5678'


@pytest.mark.parametrize('text',['从信用卡里拿500转给尾号1234李明','信用卡转给尾号1234李明500元','用信用卡买模拟灵活现金100元'])
def test_credit_source_cannot_be_silently_switched_to_cash(bank,tid,tools,text):
    name='prepare_wealth_buy' if '理财' in text or '灵活现金' in text else 'prepare_transfer'
    args={'product_id':'demo_cash','amount_fen':10000} if name=='prepare_wealth_buy' else {'recipient_id':'alice_li1','amount_fen':50000}
    out=tools.dispatch('alice',tid,name,args,user_texts=[text])
    assert out['error']['code']=='CREDIT_SOURCE_UNSUPPORTED' and not bank.task('alice',tid)['actions']


def test_cash_switch_requires_user_statement_and_still_needs_confirmation(bank,tid,tools):
    out=tools.dispatch('alice',tid,'prepare_transfer',{'recipient_id':'alice_li1','amount_fen':50000},user_texts=['从信用卡转500','改用账户余额给尾号1234的李明转500元'])
    assert out['ok'] and out['data']['status']=='pending' and bank.balance('alice')['balance_fen']==3030000


@pytest.mark.parametrize('merchant',['李明','demo_cash','investment','unknown'])
def test_person_or_investment_cannot_be_registered_as_card_merchant(bank,tid,merchant):
    with pytest.raises(BankError) as err:bank.prepare('alice',tid,'card_payment',{'card_id':'alice_card','amount_fen':50000,'merchant_id':merchant,'channel':'online'})
    assert err.value.code=='MERCHANT_NOT_ALLOWED' and bank.cards('alice')['cards'][0]['used_fen']==0


def test_even_valid_merchant_cannot_replace_person_intent(bank,tid,tools):
    args={'card_id':'alice_card','merchant_id':'demo_supermarket','amount_fen':50000,'channel':'online'}
    out=tools.dispatch('alice',tid,'prepare_card_payment',args,user_texts=['给李明拿500元'])
    assert out['error']['code']=='MERCHANT_NOT_AUTHORIZED'
    out=tools.dispatch('alice',tid,'prepare_card_payment',args,user_texts=['在模拟超市套现500转给李明'])
    assert out['error']['code']=='CARD_PURPOSE_DENIED'


def test_legacy_free_text_merchant_is_rejected_by_tool_schema(bank,tid,tools):
    out=tools.dispatch('alice',tid,'prepare_card_payment',{'card_id':'alice_card','merchant':'李明','amount_fen':50000,'channel':'online'},user_texts=['李明'])
    assert out['error']['code']=='INVALID_ARGUMENTS'


def test_previously_created_free_text_merchant_draft_cannot_execute(bank,tid):
    from app.db import encode
    a=bank.prepare('alice',tid,'card_payment',{'card_id':'alice_card','merchant_id':'demo_supermarket','amount_fen':50000,'channel':'online'})
    legacy={**a['params'],'merchant':'李明'};legacy.pop('merchant_id')
    with bank.db.connect(write=True) as conn:conn.execute('UPDATE actions SET params=? WHERE id=?',(encode(legacy),a['id']))
    with pytest.raises(BankError) as err:bank.confirm('alice',a['id'],1)
    assert err.value.code=='MERCHANT_NOT_ALLOWED' and bank.cards('alice')['cards'][0]['used_fen']==0


def test_periodic_evidence_is_owner_scoped_and_not_cancellation(bank):
    out=bank.periodic_evidence('alice','房租收款',240000)
    assert len(out['payments'])==6 and all(p['id'].startswith('seed_alice_') for p in out['payments'])
    assert out['classification']=='periodic_payment_candidate'
    with pytest.raises(BankError):bank.periodic_evidence('alice','unknown',240000)


def test_wealth_matching_returns_explanations_and_preserves_holdings(bank):
    bank.save_risk_profile('alice',{'loss_tolerance':'limited','experience':'some','horizon_days':3,'confirm':True,'assessment':assessment_fixture()})
    state=bank.wealth('alice')
    assert state['products'][0]['eligible']
    assert not state['products'][1]['eligible']
    assert any('锁定期' in reason for reason in state['products'][1]['eligibility_reasons'])
