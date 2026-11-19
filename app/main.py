from pathlib import Path
from typing import Literal
import os
from fastapi import FastAPI, Request, Response, Depends, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictBool, ValidationError
from .config import Settings, ROOT, instance_id
from .db import Database
from .service import BankService, BankError, require
from .tools import ToolRegistry, TransferArgs
from .model import DeepSeekClient
from .agent import Agent

class Input(BaseModel):
    model_config=ConfigDict(extra="forbid")
class LoginInput(Input):
    username: str = Field(max_length=100)
    password: str = Field(max_length=100)
class ChatInput(Input):
    text: str = Field(min_length=1,max_length=3000)
    task_id: str | None = None
    read_only: StrictBool = False
class VersionInput(Input):
    version: StrictInt = Field(ge=1)
class ConfirmInput(VersionInput):
    risk_acknowledged: StrictBool = False
class RiskProfileInput(Input):
    loss_tolerance: Literal["none","limited","substantial"]
    experience: Literal["none","some","experienced"]
    horizon_days: StrictInt = Field(ge=1,le=3650)
    confirm: StrictBool
    assessment: dict
class RecipientInput(Input):
    name: str = Field(min_length=1,max_length=50)
    bank_name: str = Field(min_length=2,max_length=80)
    account_number: str = Field(pattern=r"^[0-9]{12,32}$")
    phone: str = Field(pattern=r"^1[3-9][0-9]{9}$")
    confirm: StrictBool
class RepeatInput(VersionInput):
    repeat: StrictBool
class DemoResetInput(Input):
    confirm: StrictBool
class VerifyInput(VersionInput):
    code: str = Field(pattern=r"^\d{6}$")
class ReviseInput(VersionInput):
    recipient_id: str
    amount_fen: StrictInt = Field(gt=0,le=100000000)
    reserve_fen: StrictInt = Field(ge=0)
class ManualInput(Input):
    task_id: str
    kind: Literal["transfer","cancel_subscription"]
    params: dict

def create_app(settings=None, model=None):
    settings=settings or Settings.from_env()
    database=Database(settings)
    database.seed()
    if settings.demo_scenarios:
        from .scenarios import seed_scenario_users
        seed_scenario_users(database)
    bank=BankService(database)
    from .bank_market import BankCatalog
    bank.market=BankCatalog(ROOT/'data/bank-market/catalog.json')
    registry=ToolRegistry(bank)
    agent=Agent(bank,registry,model or DeepSeekClient(settings))
    app=FastAPI(title="银行Agent模拟环境",version="0.3.0")
    app.state.bank,app.state.agent=bank,agent
    cookie_name='bank_scenario_session' if settings.demo_scenarios else 'bank_session'

    @app.exception_handler(BankError)
    async def bank_error(request,exc):
        return JSONResponse(status_code=exc.status,content={"error":{"code":exc.code,"message":exc.message}})

    @app.exception_handler(ValidationError)
    async def validation_error(request,exc):
        return JSONResponse(status_code=422,content={"error":{"code":"INVALID_ARGUMENTS","message":"业务参数缺失、格式错误或包含不允许字段"}})

    @app.middleware("http")
    async def protect(request: Request,call_next):
        if request.method in ("POST","PUT","PATCH","DELETE") and request.url.path.startswith("/api/"):
            origin=request.headers.get("origin")
            if origin and origin.rstrip("/")!=str(request.base_url).rstrip("/"):
                return JSONResponse(status_code=403,content={"error":{"code":"ORIGIN_DENIED","message":"跨来源请求不允许"}})
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers["Cache-Control"]="no-store"
        return response

    def auth(request: Request):
        session=bank.session(request.cookies.get(cookie_name))
        if request.method not in ("GET","HEAD"):
            require(request.headers.get("X-CSRF-Token")==session["csrf"],"CSRF_DENIED","请求缺少有效的会话校验",403)
        return session

    @app.get("/api/meta")
    def meta():
        from .scenarios import scenario_accounts
        return {"simulation":True,"business_date":settings.business_date,"model":settings.model,"api_configured":bool(settings.api_key),"version":"0.3.0","instance_id":instance_id(settings.db_path),
                "scenario_mode":settings.demo_scenarios,"demo_accounts":scenario_accounts() if settings.demo_scenarios else []}

    @app.post("/api/login")
    def login(body: LoginInput,response: Response):
        result=bank.login(body.username,body.password)
        response.set_cookie(cookie_name,result.pop("token"),httponly=True,samesite="strict",max_age=86400)
        return result

    @app.get("/api/session")
    def session(s=Depends(auth)):
        return {"user":{"id":s["user_id"],"name":s["name"]},"csrf":s["csrf"]}

    @app.post("/api/logout")
    def logout(request: Request,response: Response,s=Depends(auth)):
        bank.logout(request.cookies[cookie_name])
        response.delete_cookie(cookie_name)
        return {"ok":True}

    @app.post("/api/demo/reset")
    async def reset_demo(body: DemoResetInput,s=Depends(auth)):
        return await agent.reset_demo_account(s["user_id"],body.confirm)

    @app.get("/api/account")
    def account(s=Depends(auth)): return bank.balance(s["user_id"])
    @app.get('/api/customer-context')
    def customer_context(s=Depends(auth)):return bank.customer_context(s['user_id'])
    @app.get("/api/wealth")
    def wealth(s=Depends(auth)): return bank.wealth(s["user_id"])
    @app.get('/api/wealth/bank-products')
    def bank_products(query: str=Query(default='',max_length=100),s=Depends(auth)):
        return bank.market.read(query)
    @app.post('/api/wealth/bank-products/refresh')
    async def refresh_bank_products(body: Input,s=Depends(auth)):
        import asyncio
        return await asyncio.to_thread(bank.market.refresh)
    @app.get('/api/wealth/risk-questionnaire')
    def risk_questionnaire(s=Depends(auth)):
        from .risk import questionnaire
        return questionnaire()
    @app.get("/api/cards")
    def cards(s=Depends(auth)): return bank.cards(s["user_id"])
    @app.post("/api/wealth/risk-profile")
    async def risk_profile(body: RiskProfileInput,s=Depends(auth)):
        lock=agent.user_lock(s["user_id"])
        require(not lock.locked(),"AGENT_BUSY","请等本轮助手处理结束后再填写问卷",409)
        async with lock:
            return bank.save_risk_profile(s["user_id"],body.model_dump())
    @app.get("/api/recipients")
    def recipients(query: str="",s=Depends(auth)): return bank.recipients(s["user_id"],query)
    @app.post("/api/recipients")
    def add_recipient(body: RecipientInput,s=Depends(auth)):
        return bank.add_recipient(s["user_id"],**body.model_dump())
    @app.get("/api/bills")
    def bills(month: str,compare_month: str|None=None,s=Depends(auth)): return bank.bill_report(s["user_id"],month,compare_month)
    @app.get("/api/transactions")
    def transactions(start_date: str,end_date: str,s=Depends(auth)): return bank.transactions(s["user_id"],start_date,end_date)
    @app.get("/api/subscriptions")
    def subscriptions(s=Depends(auth)): return {"catalog":bank.subscriptions(s["user_id"]),"candidates":bank.recurring(s["user_id"])}
    @app.get("/api/subscriptions/evidence")
    def subscription_evidence(merchant: str,amount_fen: int,s=Depends(auth)): return bank.periodic_evidence(s["user_id"],merchant,amount_fen)
    @app.get("/api/tasks")
    def tasks(s=Depends(auth)): return bank.tasks(s["user_id"])
    @app.get("/api/tasks/{tid}")
    def task(tid: str,s=Depends(auth)): return bank.task(s["user_id"],tid)
    @app.post("/api/chat")
    async def chat(body: ChatInput,s=Depends(auth)):
        tid=body.task_id or bank.create_task(s["user_id"],body.text)["id"]
        return await agent.run(s["user_id"],tid,text=body.text,read_only=body.read_only)
    @app.post("/api/tasks/{tid}/continue")
    async def continue_task(tid: str,s=Depends(auth)):
        task=bank.task(s["user_id"],tid)
        event={"task_id":tid,"actions":task["actions"],"nodes":task["nodes"]}
        return await agent.run(s["user_id"],tid,event=event)
    @app.post("/api/tasks/{tid}/cancel")
    def cancel_task(tid: str,s=Depends(auth)): return bank.cancel_task(s["user_id"],tid)
    @app.post("/api/tasks/{tid}/resume")
    def resume_task(tid: str,s=Depends(auth)): return bank.resume_task(s["user_id"],tid)
    @app.get("/api/actions/{aid}")
    def action(aid: str,s=Depends(auth)): return bank.action(s["user_id"],aid)
    @app.post("/api/actions/{aid}/confirm")
    def confirm(aid: str,body: ConfirmInput,s=Depends(auth)): return bank.confirm(s["user_id"],aid,body.version,body.risk_acknowledged)
    @app.post("/api/actions/{aid}/advance")
    async def advance(aid: str,s=Depends(auth)):
        action=bank.action(s["user_id"],aid)
        return await agent.run(s["user_id"],action["task_id"],confirmed_action_id=aid)
    @app.post("/api/actions/{aid}/reject")
    def reject(aid: str,body: VersionInput,s=Depends(auth)): return bank.reject(s["user_id"],aid,body.version)
    @app.post("/api/actions/{aid}/revise")
    def revise(aid: str,body: ReviseInput,s=Depends(auth)):
        return bank.revise(s["user_id"],aid,body.version,body.model_dump(exclude={"version"}),allow_reserve_change=True)
    @app.post("/api/repeats/{rid}/decide")
    def repeat_decision(rid: str,body: RepeatInput,s=Depends(auth)):
        return bank.decide_repeat(s["user_id"],rid,body.version,body.repeat)
    @app.post("/api/actions/{aid}/challenge")
    def challenge(aid: str,body: VersionInput,s=Depends(auth)): return bank.challenge(s["user_id"],aid,body.version)
    @app.post("/api/actions/{aid}/verify")
    def verify(aid: str,body: VerifyInput,s=Depends(auth)): return bank.verify(s["user_id"],aid,body.version,body.code)
    @app.get("/api/audit")
    def audit(task_id: str|None=None,s=Depends(auth)): return bank.audit_log(s["user_id"],task_id)

    # 手动面板仅用于检验业务后端，明确不冒充LLM或Agent。
    @app.post("/api/manual/tasks")
    def manual_task(body: ChatInput,s=Depends(auth)): return bank.create_task(s["user_id"],"[手动业务验证] "+body.text)
    @app.post("/api/manual/actions")
    def manual_action(body: ManualInput,s=Depends(auth)):
        if body.kind=="transfer":
            params=TransferArgs.model_validate(body.params).model_dump()
        else:
            from .tools import CancelArgs
            params=CancelArgs.model_validate(body.params).model_dump()
        return bank.prepare(s["user_id"],body.task_id,body.kind,params)

    app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")
    @app.get("/dev",include_in_schema=False)
    def developer_page(): return FileResponse(ROOT/"static"/"dev.html")

    @app.get("/")
    def home(): return FileResponse(ROOT/"static"/"index.html")
    return app

app=create_app()
