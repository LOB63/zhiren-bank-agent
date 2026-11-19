from scripts.risk_test_fixture import assessment_fixture
import json
import pytest
from app.db import Database
from app.service import BankService, BankError


def profile(bank,uid="alice",loss="substantial",experience="experienced",days=365):
    return bank.save_risk_profile(uid,{"loss_tolerance":loss,"experience":experience,"horizon_days":days,"confirm":True,"assessment":assessment_fixture()})


def prepare(bank,kind,params,uid="alice"):
    tid=bank.create_task(uid,"[手动业务验证] 六场景验收")["id"]
    return bank.prepare(uid,tid,kind,params)


def execute(bank,action):
    if action["risk"]=="red" and bank.action("alice",action["id"])["status"]=="pending":
        code=bank.challenge("alice",action["id"],action["version"])["demo_code"]
        bank.verify("alice",action["id"],action["version"],code)
    return bank.confirm("alice",action["id"],action["version"],risk_acknowledged=True)


def test_wealth_requires_real_user_form_and_valid_profile(bank,tools,tid):
    args={"product_id":"demo_cash","amount_fen":10000,"reserve_fen":0}
    with pytest.raises(BankError,match="问卷"):prepare(bank,"wealth_buy",args)
    assert not tools.dispatch("alice",tid,"save_risk_profile",{})["ok"]
    with pytest.raises(BankError):bank.save_risk_profile("alice",{"confirm":False})
    profile(bank)
    a=prepare(bank,"wealth_buy",args)
    assert a["status"]=="pending" and bank.wealth("alice")["holdings"][0]["principal_fen"]==0
    with pytest.raises(BankError) as err:bank.confirm("alice",a["id"],1)
    assert err.value.code=="RISK_ACK_REQUIRED"
    with pytest.raises(BankError) as err:bank.confirm("alice",a["id"],1,True)
    assert err.value.code=="REQUIRES_VERIFICATION"


@pytest.mark.parametrize("loss,experience,days,level",[("none","experienced",365,1),("substantial","none",365,1),("substantial","some",365,2),("substantial","experienced",3,1),("substantial","experienced",365,3)])
def test_profile_rules_are_programmatic(bank,loss,experience,days,level):
    assert profile(bank,loss=loss,experience=experience,days=days)["risk_level"]==level


def test_purchase_and_partial_redemption_money_conservation_and_no_double_income(bank):
    profile(bank);before=bank.balance("alice")["balance_fen"]
    buy=prepare(bank,"wealth_buy",{"product_id":"demo_cash","amount_fen":10000,"reserve_fen":300000})
    done=execute(bank,buy)
    assert bank.balance("alice")["balance_fen"]==before-10000
    assert bank.confirm("alice",buy["id"],1,True)["result"]==done["result"]
    assert bank.wealth("alice")["holdings"][0]["principal_fen"]==10000
    redeem=prepare(bank,"wealth_redeem",{"product_id":"demo_cash","amount_fen":3500})
    execute(bank,redeem);execute(bank,redeem)
    assert bank.balance("alice")["balance_fen"]==before-6500
    assert bank.wealth("alice")["holdings"][0]["principal_fen"]==6500
    report=bank.bill_report("alice","2026-10")
    assert report["income_fen"]==report["expense_fen"]==0
    assert len(report["capital_flows"])==2
    with pytest.raises(BankError):prepare(bank,"wealth_redeem",{"product_id":"demo_cash","amount_fen":6501})


def test_lock_period_and_maturity_are_enforced(bank):
    profile(bank);buy=prepare(bank,"wealth_buy",{"product_id":"demo_balanced","amount_fen":100000,"reserve_fen":0});execute(bank,buy)
    with pytest.raises(BankError) as err:prepare(bank,"wealth_redeem",{"product_id":"demo_balanced","amount_fen":1})
    assert err.value.code=="INSUFFICIENT_HOLDINGS"
    bank.settings.demo_date="2026-10-18"
    execute(bank,prepare(bank,"wealth_redeem",{"product_id":"demo_balanced","amount_fen":100000}))
    assert bank.balance("alice")["balance_fen"]==3030000


@pytest.mark.parametrize("product,amount,reserve,code",[("demo_growth",100000,0,"PRODUCT_UNSUITABLE"),("demo_cash",9999,0,"BELOW_MINIMUM"),("demo_cash",10000,3030000,"INSUFFICIENT_BALANCE"),("invalid",10000,0,"NOT_FOUND")])
def test_purchase_rejections(bank,product,amount,reserve,code):
    profile(bank,loss="limited")
    with pytest.raises(BankError) as err:prepare(bank,"wealth_buy",{"product_id":product,"amount_fen":amount,"reserve_fen":reserve})
    assert err.value.code==code


def test_profile_change_invalidates_prepared_purchase(bank):
    profile(bank);a=prepare(bank,"wealth_buy",{"product_id":"demo_growth","amount_fen":100000,"reserve_fen":0})
    profile(bank,loss="none")
    with pytest.raises(BankError) as err:execute(bank,a)
    assert err.value.code=="PROFILE_CHANGED" and bank.balance("alice")["balance_fen"]==3030000


def test_card_purchase_repayment_and_separate_cash_credit_ledgers(bank):
    cid="alice_card";before=bank.balance("alice")["balance_fen"]
    payment=prepare(bank,"card_payment",{"card_id":cid,"amount_fen":23450,"merchant_id":"demo_supermarket","channel":"offline"});execute(bank,payment);execute(bank,payment)
    assert bank.balance("alice")["balance_fen"]==before
    assert bank.cards("alice")["cards"][0]["used_fen"]==23450
    pay=prepare(bank,"card_repay",{"card_id":cid,"amount_fen":10000,"reserve_fen":300000});execute(bank,pay);execute(bank,pay)
    assert bank.balance("alice")["balance_fen"]==before-10000
    assert bank.cards("alice")["cards"][0]["used_fen"]==13450
    report=bank.bill_report("alice","2026-10")
    assert report["expense_fen"]==23450 and len(report["capital_flows"])==1
    with bank.db.connect() as conn:assert bank._daily_total(conn,"alice")==0


def test_freeze_unlock_lost_card_are_real_constraints(bank):
    cid="alice_card"
    def payment():return prepare(bank,"card_payment",{"card_id":cid,"amount_fen":1000,"merchant_id":"demo_supermarket","channel":"offline"})
    execute(bank,prepare(bank,"card_freeze",{"card_id":cid}))
    with pytest.raises(BankError) as err:payment()
    assert err.value.code=="CARD_FROZEN"
    execute(bank,prepare(bank,"card_unlock",{"card_id":cid}));execute(bank,payment())
    execute(bank,prepare(bank,"card_lost",{"card_id":cid}))
    for kind in ("card_unlock","card_payment","card_limit"):
        params={"card_id":cid,"amount_fen":1,"merchant_id":"demo_supermarket","channel":"offline","credit_limit_fen":500000,"single_limit_fen":100000,"daily_limit_fen":200000}
        with pytest.raises(BankError) as err:prepare(bank,kind,params)
        assert err.value.code=="CARD_LOST"
    again=BankService(Database(bank.settings));again.db.seed()
    assert again.cards("alice")["cards"][0]["status"]=="lost"


def test_online_switch_and_old_card_drafts_invalidate(bank):
    pay=prepare(bank,"card_payment",{"card_id":"alice_card","amount_fen":1000,"merchant_id":"demo_supermarket","channel":"online"})
    execute(bank,prepare(bank,"card_online",{"card_id":"alice_card","enabled":False}))
    with pytest.raises(BankError) as err:execute(bank,pay)
    assert err.value.code=="CARD_CHANGED"
    with pytest.raises(BankError) as err:prepare(bank,"card_payment",{"card_id":"alice_card","amount_fen":1000,"merchant_id":"demo_supermarket","channel":"online"})
    assert err.value.code=="ONLINE_DISABLED"
    execute(bank,prepare(bank,"card_payment",{"card_id":"alice_card","amount_fen":1000,"merchant_id":"demo_supermarket","channel":"offline"}))
    execute(bank,prepare(bank,"card_online",{"card_id":"alice_card","enabled":True}))


def test_card_limits_daily_boundaries_and_credit_not_cash(bank):
    execute(bank,prepare(bank,"card_limit",{"card_id":"alice_card","credit_limit_fen":600000,"single_limit_fen":10000,"daily_limit_fen":15000}))
    assert bank.balance("alice")["balance_fen"]==3030000
    for amount in (10000,5000):execute(bank,prepare(bank,"card_payment",{"card_id":"alice_card","amount_fen":amount,"merchant_id":"demo_supermarket","channel":"offline"}))
    with pytest.raises(BankError) as err:prepare(bank,"card_payment",{"card_id":"alice_card","amount_fen":1,"merchant_id":"demo_supermarket","channel":"offline"})
    assert err.value.code=="CARD_PAYMENT_LIMIT"
    for credit,single,daily in [(1000001,100,100),(100,200,200),(1,1,1)]:
        with pytest.raises(BankError):prepare(bank,"card_limit",{"card_id":"alice_card","credit_limit_fen":credit,"single_limit_fen":single,"daily_limit_fen":daily})


def test_application_cap_and_idempotence(bank):
    a=prepare(bank,"card_apply",{});execute(bank,a);execute(bank,a)
    assert len(bank.cards("alice")["cards"])==2
    assert bank.balance("alice")["balance_fen"]==3030000
    with pytest.raises(BankError):prepare(bank,"card_apply",{})


def test_user_isolation_reset_backup_and_new_tables(bank):
    profile(bank);execute(bank,prepare(bank,"wealth_buy",{"product_id":"demo_cash","amount_fen":10000,"reserve_fen":0}))
    execute(bank,prepare(bank,"card_freeze",{"card_id":"alice_card"}))
    before=bank.cards("bob")
    with pytest.raises(BankError):prepare(bank,"card_unlock",{"card_id":"bob_card"})
    assert bank.wealth("bob")["holdings"][0]["principal_fen"]==0
    out=bank.reset_demo_account("alice",True)
    assert bank.cards("bob")==before and bank.cards("alice")["cards"][0]["status"]=="active"
    assert not bank.risk_profile("alice")["completed"] and bank.wealth("alice")["holdings"][0]["principal_fen"]==0
    backup=json.loads((bank.settings.db_path.parent/"backups"/out["backup_file"]).read_text(encoding="utf-8"))
    assert backup["tables"]["investment_lots"] and backup["tables"]["risk_profiles"] and backup["tables"]["cards"]


def test_model_cannot_supply_internal_versions_or_identity(bank,tools,tid):
    assert not tools.dispatch("alice",tid,"prepare_wealth_buy",{"product_id":"demo_cash","amount_fen":10000,"profile_version":1})["ok"]
    assert not tools.dispatch("alice",tid,"prepare_card_control",{"card_id":"alice_card","operation":"unlock","card_version":1})["ok"]
    assert not tools.dispatch("alice",tid,"get_cards",{"user_id":"bob"})["ok"]


def test_cross_scenario_dependencies_apply_to_new_kinds(bank,tid):
    bank.set_plan("alice",tid,[{"id":"freeze","title":"冻结卡片","kind":"card","depends_on":[]},{"id":"buy","title":"申购现金示例","kind":"wealth","depends_on":["freeze"]}])
    profile(bank)
    with pytest.raises(BankError) as err:bank.prepare("alice",tid,"wealth_buy",{"product_id":"demo_cash","amount_fen":10000,"reserve_fen":0})
    assert err.value.code=="DEPENDENCY_PENDING"
    a=bank.prepare("alice",tid,"card_freeze",{"card_id":"alice_card"});execute(bank,a)
    b=bank.prepare("alice",tid,"wealth_buy",{"product_id":"demo_cash","amount_fen":10000,"reserve_fen":0});execute(bank,b)
    assert bank.task("alice",tid)["status"]=="completed"


def test_loss_intolerance_cannot_buy_non_guaranteed_product(bank):
    profile(bank,loss="none")
    assert all(not p["eligible"] for p in bank.wealth("alice")["products"])
    with pytest.raises(BankError) as err:prepare(bank,"wealth_buy",{"product_id":"demo_cash","amount_fen":10000,"reserve_fen":0})
    assert err.value.code=="PRODUCT_UNSUITABLE"


def test_http_profile_and_confirm_cannot_bypass_identity_or_consent(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Settings
    app=create_app(Settings(db_path=tmp_path/'http.sqlite3',legacy_wealth_enabled=True))
    with TestClient(app) as client:
        assert client.get('/api/wealth').status_code==401
        session=client.post('/api/login',json={'username':'alice','password':'demo2026'}).json()
        headers={'X-CSRF-Token':session['csrf']}
        body={'loss_tolerance':'limited','experience':'some','horizon_days':30,'confirm':True,'assessment':assessment_fixture()}
        assert client.post('/api/wealth/risk-profile',json=body).status_code==403
        assert client.post('/api/wealth/risk-profile',headers=headers,json={**body,'user_id':'bob'}).status_code==422
        assert client.post('/api/wealth/risk-profile',headers=headers,json={**body,'confirm':1}).status_code==422
        assert client.post('/api/wealth/risk-profile',headers=headers,json=body).status_code==200
        bank=app.state.bank;a=prepare(bank,'wealth_buy',{'product_id':'demo_cash','amount_fen':10000,'reserve_fen':0})
        assert client.post('/api/actions/'+a['id']+'/confirm',headers=headers,json={'version':1}).json()['error']['code']=='RISK_ACK_REQUIRED'
        assert client.post('/api/actions/'+a['id']+'/confirm',headers=headers,json={'version':1,'risk_acknowledged':1}).status_code==422
        assert not bank.risk_profile('bob')['completed']


@pytest.mark.asyncio
@pytest.mark.parametrize('tool,args,kind',[
 ('prepare_wealth_buy',{'product_id':'demo_cash','amount_fen':10000,'reserve_fen':0},'wealth_buy'),
 ('prepare_card_apply',{},'card_apply'),
 ('prepare_card_control',{'card_id':'alice_card','operation':'freeze'},'card_freeze'),
 ('prepare_card_limit',{'card_id':'alice_card','credit_limit_fen':600000,'single_limit_fen':100000,'daily_limit_fen':200000},'card_limit'),
 ('prepare_card_payment',{'card_id':'alice_card','amount_fen':1000,"merchant_id":"demo_supermarket",'channel':'offline'},'card_payment'),
])
async def test_agent_stops_before_new_business_execution(bank,tid,tools,tool,args,kind):
    from app.agent import Agent
    from test_agent_api import ScriptedModel,call
    profile(bank);before=bank.balance('alice')['balance_fen']
    model=ScriptedModel([call(tool,args)])
    result=await Agent(bank,tools,model).run('alice',tid,text='在模拟超市线下消费10元，其他操作按此协议测试明确请求')
    assert result['task']['status']=='waiting_confirmation'
    assert result['task']['actions'][0]['kind']==kind
    assert result['task']['actions'][0]['status']=='pending'
    assert bank.balance('alice')['balance_fen']==before
    assert bank.cards('alice')['cards'][0]['used_fen']==0


def test_card_daily_limit_counts_execution_date_not_draft_date(bank):
    execute(bank,prepare(bank,'card_limit',{'card_id':'alice_card','credit_limit_fen':500000,'single_limit_fen':10000,'daily_limit_fen':10000}))
    old=prepare(bank,'card_payment',{'card_id':'alice_card','amount_fen':10000,"merchant_id":"demo_supermarket",'channel':'offline'})
    bank.settings.demo_date='2026-10-12'
    execute(bank,old)
    assert bank.cards('alice')['cards'][0]['today_spent_fen']==10000
    with pytest.raises(BankError) as err:prepare(bank,'card_payment',{'card_id':'alice_card','amount_fen':1,"merchant_id":"demo_supermarket",'channel':'offline'})
    assert err.value.code=='CARD_PAYMENT_LIMIT'
