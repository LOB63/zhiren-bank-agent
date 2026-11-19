import httpx
import pytest
import app.model as module
from app.config import Settings
from app.model import DeepSeekClient,ModelError


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",[httpx.ConnectError,httpx.ReadTimeout,httpx.RemoteProtocolError])
async def test_generation_retry_resends_identical_messages_without_executing_tools(monkeypatch,failure):
    requests=[]
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            requests.append(kwargs['json'])
            if len(requests)<3:raise failure('test network failure')
            return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}]},request=httpx.Request('POST',url))
    async def no_delay(*args):pass
    monkeypatch.setattr(module.httpx,'AsyncClient',Client);monkeypatch.setattr(module.asyncio,'sleep',no_delay)
    result=await DeepSeekClient(Settings(api_key='unit-test-only')).complete([{'role':'user','content':'查询余额'}],[])
    assert result['message']['content']=='ok' and len(requests)==3
    assert requests[0]==requests[1]==requests[2]


@pytest.mark.asyncio
async def test_network_retry_is_bounded_and_pauses_on_exhaustion(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):calls.append(1);raise httpx.ReadTimeout('unit timeout')
    async def no_delay(*args):pass
    monkeypatch.setattr(module.httpx,'AsyncClient',Client);monkeypatch.setattr(module.asyncio,'sleep',no_delay)
    with pytest.raises(ModelError):await DeepSeekClient(Settings(api_key='unit-test-only')).complete([],[])
    assert len(calls)==3
