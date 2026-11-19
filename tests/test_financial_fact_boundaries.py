from conftest import transfer


def test_two_intended_same_amount_transfers_remain_expenses_not_recoverable_cash(bank,tid,tools):
    # 复现用户的501元两笔转账，后端仍按两次独立授权计账。
    before=bank.balance('alice')['balance_fen']
    first=transfer(bank,tid,amount=25050);bank.confirm('alice',first['id'],1)
    question=transfer(bank,tid,amount=25050)['repeat_request']
    second=bank.decide_repeat('alice',question['id'],question['version'],True)
    bank.confirm('alice',second['id'],1)
    report=tools.dispatch('alice',tid,'get_bill_report',{'month':'2026-10'})['data']
    assert report['expense_fen']==50100 and report['income_fen']==0
    duplicate=next(a for a in report['anomalies'] if a['type']=='possible_duplicate')
    assert len(duplicate['entry_ids'])==2 and duplicate['confirmed'] is False
    assert duplicate['recoverable_amount_fen'] is None
    balance=tools.dispatch('alice',tid,'get_balance',{})['data']
    assert balance['balance_fen']==before-50100==2979900
    assert balance['balance_yuan']=='29,799.00'
    assert balance['investment_capacity_fen'] is None
    assert report['period']['status']=='current_partial_month'
    exact=tools.dispatch('alice',tid,'list_transactions',{'start_date':'2026-04-11','end_date':'2026-10-12','amount_fen':25050})
    assert exact['ok'] and len(exact['data'])==2
    assert all(row['amount_yuan']=='250.50' and row['category']=='转账' for row in exact['data'])
    assert not tools.dispatch('alice',tid,'list_transactions',{'start_date':'2026-10-01','end_date':'2026-10-12','amount_fen':True})['ok']
    found=tools.dispatch('alice',tid,'find_transactions_by_amount',{'amount_fen':25050})['data']
    assert len(found['matches'])==2 and not found['truncated']
    assert all(row['amount_yuan']=='250.50' for row in found['matches'])
    assert not bank.find_transactions_by_amount('bob',25050)['matches']


def test_historical_current_and_future_months_keep_distinct_scope(bank):
    past=bank.bill_report('alice','2026-09')
    current=bank.bill_report('alice','2026-10')
    future=bank.bill_report('alice','2026-11')
    assert past['period']['status']=='historical_month' and past['expense_fen']==561500
    assert current['period']['status']=='current_partial_month'
    assert future['period']['status']=='future_month'
    assert future['period']['as_of_date']=='2026-10-11'
    # 无账单的未来月份仅返回已存记录为0，不创建退款、预期工资或投资额度。
    assert future['transaction_count']==0 and future['limitations']
    assert bank.balance('alice')['balance_fen']==3030000
