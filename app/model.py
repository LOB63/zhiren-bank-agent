import asyncio
import json
from dataclasses import replace
from .scope import SCOPE_SYSTEM,scope_input
import httpx
from .config import Settings

class ModelError(Exception):
    pass

class DeepSeekClient:
    def __init__(self, settings: Settings):
        self.settings=settings

    async def complete(self, messages, tools, max_tokens=8192):
        if not self.settings.api_key:
            raise ModelError("尚未配置DEEPSEEK_API_KEY。请在D盘项目的.env中填写，重启服务后使用真实Agent。手动业务验证和测试不需要密钥。")
        if self.settings.thinking:
            messages=[{**m, "reasoning_content":m.get("reasoning_content", "")} if m["role"]=="assistant" else m for m in messages]
        body={"model":self.settings.model,"messages":messages,"tools":tools,"stream":False,"max_tokens":max_tokens,
              "thinking":{"type":"enabled" if self.settings.thinking else "disabled"}}
        if not tools:body.pop("tools")
        if self.settings.thinking:
            body["reasoning_effort"]=self.settings.effort
        else:
            body["temperature"]=0
        async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
            for attempt in range(3):
                try:
                    response=await client.post(self.settings.base_url.rstrip("/")+"/chat/completions",json=body,
                                               headers={"Authorization":"Bearer "+self.settings.api_key})
                except (httpx.TimeoutException,httpx.NetworkError,httpx.RemoteProtocolError):
                    # 此接口只生成消息，不执行工具；完整响应被解析前不会派发任何银行动作。
                    # 有界重试同一生成请求不会重发已确认的转账或其他业务操作。
                    if attempt<2:
                        await asyncio.sleep(1+attempt)
                        continue
                    raise ModelError("模型接口超时或网络不可用。任务已暂停，账户操作状态保留；恢复前请先核对状态。")
                if response.status_code in (429,502,503) and attempt<2:
                    await asyncio.sleep(1+attempt)
                    continue
                if response.status_code!=200:
                    raise ModelError(f"模型接口返回HTTP {response.status_code}。请检查本机型号、密钥、额度和接口配置。未展示原始响应以避免泄露凭证。")
                try:
                    payload=response.json()
                    return self.parse_response(payload)
                except (ValueError,KeyError,IndexError,TypeError,AttributeError):
                    raise ModelError("模型响应不符合Chat Completion格式，任务已暂停")
        raise ModelError("模型服务暂时不可用")

    def parse_response(self,payload):
        """先完整检查生成协议，再允许Agent处理任何工具；错误响应不作为完成结果。"""
        choice=payload['choices'][0]
        message=choice['message']
        if not isinstance(message,dict) or message.get('role','assistant')!='assistant':
            raise ValueError()
        for key in ('content','reasoning_content'):
            if message.get(key) is not None and not isinstance(message[key],str):
                raise ValueError()
        calls=message.get('tool_calls')
        if calls is not None:
            if not isinstance(calls,list) or len(calls)>32:raise ValueError()
            seen=set()
            for call in calls:
                if not isinstance(call,dict) or call.get('type')!='function':raise ValueError()
                cid=call.get('id');function=call.get('function')
                if not isinstance(cid,str) or not cid or cid in seen or not isinstance(function,dict):raise ValueError()
                seen.add(cid)
                if not isinstance(function.get('name'),str) or not function['name'] or not isinstance(function.get('arguments'),str):raise ValueError()
        finish=choice.get('finish_reason')
        if finish not in (None,'stop','tool_calls','length','content_filter','insufficient_system_resource'):raise ValueError()
        if finish in ('content_filter','insufficient_system_resource'):raise ValueError()
        if not calls and not (message.get('content') or '').strip() and finish!='length':raise ValueError()
        usage=payload.get('usage',{})
        if not isinstance(usage,dict):raise ValueError()
        for key in ('prompt_tokens','completion_tokens','total_tokens'):
            if key in usage and (type(usage[key]) is not int or usage[key]<0):raise ValueError()
        return {'message':{k:v for k,v in message.items() if k in ('role','content','tool_calls','reasoning_content')},
                'usage':usage,'model':payload.get('model',self.settings.model),'finish_reason':finish}

    async def classify_scope(self, text, messages):
        router=DeepSeekClient(replace(self.settings,thinking=False,request_timeout=min(self.settings.request_timeout,20)))
        response=await router.complete([{"role":"system","content":SCOPE_SYSTEM},{"role":"user","content":scope_input(text,messages)}],[],max_tokens=128)
        try:
            result=json.loads(response["message"].get("content") or "")
            if set(result)!={"scope"} or result["scope"] not in ("banking","greeting","off_topic"):
                raise ValueError()
        except (ValueError,TypeError):
            raise ModelError("暂时无法识别本次请求，请稍后重试。")
        return {"scope":result["scope"],"usage":response["usage"]}
