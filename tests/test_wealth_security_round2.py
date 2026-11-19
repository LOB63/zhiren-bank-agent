import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings
from scripts.risk_test_fixture import assessment_fixture

@pytest.mark.parametrize('attack',[{'risk_level':3},{'user_id':'bob'},{'version':999},{'confirm':1}])
def test_cannot_claim_grade_identity_or_consent(tmp_path,attack):
    app=create_app(Settings(db_path=tmp_path/'b.sqlite3',demo_date='2026-10-29'))
    with TestClient(app) as client:
        s=client.post('/api/login',json={'username':'alice','password':'demo2026'}).json()
        body={'loss_tolerance':'limited','experience':'some','horizon_days':30,'confirm':True,'assessment':assessment_fixture(),**attack}
        response=client.post('/api/wealth/risk-profile',headers={'X-CSRF-Token':s['csrf']},json=body)
        assert response.status_code==422
        assert not app.state.bank.risk_profile('alice')['completed']

def test_non_catalog_and_script_product_ids_cannot_execute(bank,tools,tid):
    before=bank.balance('alice')['balance_fen']
    for product in ['107389','bank_107389','https://127.0.0.1/','<script>alert(1)</script>']:
        r=tools.dispatch('alice',tid,'prepare_wealth_buy',{'product_id':product,'amount_fen':10000,'reserve_fen':0})
        assert not r['ok']
    assert bank.balance('alice')['balance_fen']==before
    assert not bank.task('alice',tid)['actions']

def test_questionnaire_auth_and_csrf_are_independent_of_chart(tmp_path):
    app=create_app(Settings(db_path=tmp_path/'b.sqlite3'))
    with TestClient(app) as c:
        assert c.get('/api/wealth/risk-questionnaire').status_code==401
        s=c.post('/api/login',json={'username':'alice','password':'demo2026'}).json()
        assert c.get('/api/wealth/risk-questionnaire').status_code==200
        body={'loss_tolerance':'limited','experience':'some','horizon_days':30,'confirm':True,'assessment':assessment_fixture()}
        assert c.post('/api/wealth/risk-profile',json=body).status_code==403
        assert c.post('/api/wealth/risk-profile',headers={'X-CSRF-Token':s['csrf'],'Origin':'https://evil.example'},json=body).status_code==403
        assert not app.state.bank.risk_profile('alice')['completed']
