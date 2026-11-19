from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings,instance_id
from scripts.service_probe import ready


def test_instance_identity_distinguishes_database_paths(tmp_path):
    first=Settings(db_path=tmp_path/'first.sqlite3');second=Settings(db_path=tmp_path/'second.sqlite3')
    with TestClient(create_app(first)) as client:meta=client.get('/api/meta').json()
    assert meta['instance_id']==instance_id(first.db_path)
    assert meta['instance_id']!=instance_id(second.db_path)
    assert str(tmp_path) not in meta['instance_id']


def test_probe_rejects_different_instance_on_same_port(monkeypatch):
    import scripts.service_probe as module
    class Response:
        status_code=200
        def json(self):return {'simulation':True,'instance_id':'other-instance'}
    monkeypatch.setattr(module.httpx,'get',lambda *args,**kwargs:Response())
    assert not ready({'url':'http://127.0.0.1:8767/','instance_id':'expected'})
