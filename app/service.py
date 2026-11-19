"""模型不可信；账户身份、金额计算、确认与记账由此模块决定。"""
from collections import defaultdict
from datetime import date, timedelta
import hashlib
import hmac
import json
import secrets
import time
import uuid
import re
from .db import Database, encode, password_hash

from .errors import BankError, require
from .extended import ExtendedBankMixin, EXTENDED_KINDS, WEALTH_KINDS, CARD_KINDS

def yuan_text(fen):
    """整数分生成显示金额，避免模型自行挪动小数点。"""
    absolute=abs(fen)
    return ("-" if fen<0 else "")+f"{absolute//100:,}.{absolute%100:02d}"

class BankService(ExtendedBankMixin):
    def __init__(self, database: Database):
        self.db, self.settings = database, database.settings

    def audit(self, conn, uid, event, detail, task_id=None, action_id=None):
        conn.execute("INSERT INTO audit(user_id,task_id,action_id,at,event,detail) VALUES(?,?,?,?,?,?)",
                     (uid,task_id,action_id,self.settings.stamp(),event,encode(detail)))

    def login(self, username, password):
        with self.db.connect(write=True) as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            require(row and hmac.compare_digest(row["password_hash"], password_hash(password,row["id"])),
                    "LOGIN_FAILED", "用户名或密码错误",401)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(),row["id"],csrf,time.time()+86400))
            return {"token":token,"csrf":csrf,"user":{"id":row["id"],"name":row["name"]}}

    def session(self, token):
        require(bool(token),"LOGIN_REQUIRED","请先登录模拟账户",401)
        with self.db.connect() as conn:
            row = conn.execute("SELECT s.*,u.name FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires>?",
                               (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
            require(row is not None,"LOGIN_REQUIRED","登录已过期",401)
            return dict(row)

    def logout(self, token):
        with self.db.connect(write=True) as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def reset_demo_account(self, uid, confirmed):
        require(confirmed is True,"RESET_CONFIRMATION_REQUIRED","请先明确确认重置当前模拟账户")
        from .scenarios import SCENARIOS,seed_scenario_ledger
        scenario=uid in SCENARIOS and self.settings.demo_scenarios
        require(uid in ("alice","bob") or scenario,"DEMO_ONLY","仅允许重置内置模拟账户",403)
        tables=("customer_contexts","risk_profiles","investment_lots","cards","audit","transfer_repeats","actions","tasks","entries","subscriptions","recipient_details","recipients","accounts")
        backup_dir=self.settings.db_path.parent/"backups"
        backup_dir.mkdir(parents=True,exist_ok=True)
        backup_file=backup_dir/f"{uid}-{uuid.uuid4().hex}.json"
        with self.db.connect(write=True) as conn:
            require(conn.execute("SELECT 1 FROM users WHERE id=?",(uid,)).fetchone(),"NOT_FOUND","模拟用户不存在",404)
            snapshot={"format_version":1,"user_id":uid,"business_date":self.settings.business_date,
                      "tables":{table:[dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE user_id=?",(uid,))] for table in tables}}
            # 备份成功后才删除；身份、密码哈希和会话凭证不写入备份。
            with backup_file.open("x",encoding="utf-8") as handle:
                handle.write(encode(snapshot))
            for table in tables:
                conn.execute(f"DELETE FROM {table} WHERE user_id=?",(uid,))
            if scenario:seed_scenario_ledger(self.db,conn,uid)
            else:self.db.seed_user_ledger(conn,uid)
            self.audit(conn,uid,"demo_account_reset",{"backup_file":backup_file.name,"scope":"current_user_only"})
        return {"reset":True,"user_id":uid,"scope":"current_user_only","backup_file":backup_file.name,
                "account":self.balance(uid)}

    def balance(self, uid):
        with self.db.connect() as conn:
            row = conn.execute("SELECT balance_fen FROM accounts WHERE user_id=?",(uid,)).fetchone()
            require(row is not None,"NOT_FOUND","账户不存在",404)
            return {"balance_fen":row[0],"balance_yuan":yuan_text(row[0]),"currency":"CNY","business_date":self.settings.business_date,
                    "balance_kind":"simulated_account_book_balance","investment_capacity_fen":None,
                    "limitations":["仅本账户账面余额，未核对全部负债、近期必要支出及需保留资金，不能当作可投资额度"]}

    def customer_context(self,uid):
        with self.db.connect() as conn:
            row=conn.execute('SELECT data FROM customer_contexts WHERE user_id=?',(uid,)).fetchone()
        if not row:return {'available':False,'message':'尚未登记额外财务资料，请按本次业务需要向用户核实。'}
        result=json.loads(row[0]);result['available']=True
        result['reserve_target_yuan']=yuan_text(result['reserve_target_fen'])
        result['as_of_date']=self.settings.business_date
        for item in result['upcoming_outlays']:
            item['amount_yuan']=yuan_text(item['amount_fen'])
            item['timing_status']='upcoming' if item['due_date']>self.settings.business_date else 'due_or_past_unverified'
        result['limitations']=['登记资料不是冻结资金，不是完整负债证明；到期不等于已支付。','期初持仓是情境初始状态，不是本次助手执行结果。']
        return result

    def recipients(self, uid, query=""):
        with self.db.connect() as conn:
            rows=conn.execute("SELECT r.id,r.name,r.phone_tail,d.bank_name,substr(d.account_number,-4) account_tail FROM recipients r LEFT JOIN recipient_details d ON d.recipient_id=r.id AND d.user_id=r.user_id WHERE r.user_id=? ORDER BY r.rowid",(uid,)).fetchall()
            return [dict(row) for row in rows if any(query in (row[key] or '') for key in ('name','phone_tail','bank_name','account_tail'))]

    def add_recipient(self,uid,name,bank_name,account_number,phone,confirm):
        require(confirm is True,'RECIPIENT_CONFIRMATION_REQUIRED','请核对收款信息并勾选确认')
        name,bank_name=name.strip(),bank_name.strip()
        require(1<=len(name)<=50 and 2<=len(bank_name)<=80 and not any(ord(c)<32 for c in name+bank_name),'INVALID_RECIPIENT','请填写有效姓名和开户银行')
        require(bool(re.fullmatch(r'[0-9]{12,32}',account_number)),'INVALID_ACCOUNT','收款账号须为12至32位数字')
        require(bool(re.fullmatch(r'1[3-9][0-9]{9}',phone)),'INVALID_PHONE','请填写11位手机号码')
        rid='recipient_'+uuid.uuid4().hex
        with self.db.connect(write=True) as conn:
            require(not conn.execute('SELECT 1 FROM recipient_details WHERE user_id=? AND account_number=?',(uid,account_number)).fetchone(),'RECIPIENT_EXISTS','这个收款账号已经保存，请在列表中查找',409)
            conn.execute('INSERT INTO recipients VALUES(?,?,?,?)',(rid,uid,name,phone[-4:]))
            conn.execute('INSERT INTO recipient_details VALUES(?,?,?,?)',(rid,uid,bank_name,account_number))
            self.audit(conn,uid,'recipient_added',{'recipient_id':rid,'name':name,'bank_name':bank_name,'account_tail':account_number[-4:]})
        return next(r for r in self.recipients(uid) if r['id']==rid)

    def find_transactions_by_amount(self, uid, amount_fen):
        require(type(amount_fen) is int and amount_fen>0,"INVALID_AMOUNT","筛选金额必须是正整数分")
        with self.db.connect() as conn:
            rows=conn.execute("SELECT * FROM entries WHERE user_id=? AND amount_fen=? AND substr(at,1,10)<=? ORDER BY at,id LIMIT 201",
                              (uid,amount_fen,self.settings.business_date)).fetchall()
        return {"amount_fen":amount_fen,"amount_yuan":yuan_text(amount_fen),"as_of_date":self.settings.business_date,
                "scope":"本账户截至模拟业务日的已记录交易，含当日；不含其他账户",
                "matches":[{**dict(r),"amount_yuan":yuan_text(r["amount_fen"])} for r in rows[:200]],
                "truncated":len(rows)>200,"limitations":["同金额不代表重复或可追回；未找到仅指本系统记录，不可推断外部交易不存在"]}

    def transactions(self, uid, start, end, recipient_id=None, amount_fen=None):
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if first.isoformat()!=start or last.isoformat()!=end:
                raise ValueError()
        except ValueError:
            raise BankError("INVALID_DATE","日期必须是YYYY-MM-DD")
        require(last > first,"INVALID_RANGE","结束日期必须晚于开始日期")
        require((last-first).days<=186,"DATE_RANGE_TOO_LARGE","一次最多查询186天")
        require(amount_fen is None or (type(amount_fen) is int and amount_fen>0),"INVALID_AMOUNT","筛选金额必须是正整数分")
        with self.db.connect() as conn:
            if recipient_id:
                require(conn.execute("SELECT 1 FROM recipients WHERE id=? AND user_id=?",(recipient_id,uid)).fetchone(),
                        "NOT_FOUND","收款人不存在",404)
            rows = conn.execute("SELECT * FROM entries WHERE user_id=? AND substr(at,1,10)>=? AND substr(at,1,10)<? ORDER BY at,id",
                                (uid,start,end)).fetchall()
            return [{**dict(row),"amount_yuan":yuan_text(row["amount_fen"])} for row in rows
                    if (recipient_id is None or row["recipient_id"]==recipient_id) and (amount_fen is None or row["amount_fen"]==amount_fen)]

    def bill_report(self, uid, month, compare_month=None):
        def report(period):
            try:
                first = date.fromisoformat(period+"-01")
                if f'{first.year:04d}-{first.month:02d}'!=period:
                    raise ValueError()
                last = date(first.year+1,1,1) if first.month==12 else date(first.year,first.month+1,1)
            except ValueError:
                raise BankError("INVALID_MONTH","月份必须是YYYY-MM")
            all_rows = self.transactions(uid,first.isoformat(),last.isoformat())
            capital_flows=[r for r in all_rows if r["category"] in ("理财申购","理财赎回","信用卡还款")]
            rows=[r for r in all_rows if r not in capital_flows]
            cats, merchants = defaultdict(int),defaultdict(list)
            income = expense = 0
            for row in rows:
                if row["direction"]=="in":
                    income += row["amount_fen"]
                else:
                    expense += row["amount_fen"]
                    cats[row["category"]] += row["amount_fen"]
                    merchants[(row["merchant"],row["at"][:10],row["amount_fen"])].append(row["id"])
            anomalies = [{"type":"large_payment","reason":"单笔支出不低于1000元","entry_ids":[row["id"]]} for row in rows if row["direction"]=="out" and row["amount_fen"]>=100000]
            anomalies += [{"type":"possible_duplicate","reason":"同日同商户同金额的多笔交易",
                           "merchant":key[0],"date":key[1],"amount_fen":key[2],"amount_yuan":yuan_text(key[2]),
                           "confirmed":False,"recoverable_amount_fen":None,"entry_ids":ids} for key,ids in merchants.items() if len(ids)>1]
            today=date.fromisoformat(self.settings.business_date)
            period_status="historical_month" if last<=today else "future_month" if first>today else "current_partial_month"
            return {"month":period,"capital_flows":capital_flows,"capital_flow_note":"理财申赎本金与信用卡还款单列，不计为工资收入或重复消费；信用卡消费计支出，现金变化另看回执。","income_fen":income,"expense_fen":expense,"net_fen":income-expense,
                    "income_yuan":yuan_text(income),"expense_yuan":yuan_text(expense),"net_yuan":yuan_text(income-expense),
                    "categories":[{"name":k,"amount_fen":v,
                                   "transaction_count":sum(r['direction']=='out' and r['category']==k for r in rows),
                                   "entry_ids":[r['id'] for r in rows if r['direction']=='out' and r['category']==k]}
                                  for k,v in sorted(cats.items(), key=lambda kv:-kv[1])],
                    "entry_ids":[row["id"] for row in rows],"anomalies":anomalies,"transaction_count":len(rows),
                    "period":{"start_date":first.isoformat(),"end_date_exclusive":last.isoformat(),"as_of_date":today.isoformat(),"status":period_status},
                    "limitations":["只统计本系统已记录账单，不代表完整财务状况或全部未来支出",
                                   "未记录收入不能用于断定工资发放状态或未来收入",
                                   "历史支出不是经确认的未来必要生活费；异常候选不能增加可用余额"]}
        result = report(month)
        if compare_month:
            comparison=report(compare_month)
            result["comparison"] = comparison
            result["expense_change_fen"] = result["expense_fen"]-comparison["expense_fen"]
        return result

    def subscriptions(self, uid):
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM subscriptions WHERE user_id=? ORDER BY next_at",(uid,))]

    def recurring(self, uid):
        with self.db.connect() as conn:
            entries=conn.execute("SELECT * FROM entries WHERE user_id=? AND direction='out' ORDER BY at",(uid,)).fetchall()
        grouped=defaultdict(list)
        for row in entries:
            grouped[(row["merchant"],row["amount_fen"])].append(row)
        catalog=self.subscriptions(uid)
        result=[]
        for (merchant, amount), rows in grouped.items():
            dates=[date.fromisoformat(row["at"][:10]) for row in rows]
            gaps=[(b-a).days for a,b in zip(dates,dates[1:])]
            if len(rows)<3 or sum(28<=gap<=35 for gap in gaps)/len(gaps)<0.75:
                continue
            match=next((sub for sub in catalog if sub["merchant"]==merchant and sub["amount_fen"]==amount),None)
            result.append({"merchant":merchant,"amount_fen":amount,"observations":len(rows),"interval_days":gaps,
                           "entry_ids":[row["id"] for row in rows],"classification":"confirmed_subscription" if match else "periodic_payment_candidate",
                           "subscription":match,"explanation":"依据历史金额及28至35天间隔生成候选，只有订阅目录中的项目可申请取消"})
        return result

    def periodic_evidence(self, uid, merchant, amount_fen):
        candidate=next((c for c in self.recurring(uid) if c["merchant"]==merchant and c["amount_fen"]==amount_fen),None)
        require(candidate is not None,"NOT_FOUND","当前账户没有匹配的周期付款候选",404)
        with self.db.connect() as conn:
            rows=conn.execute("SELECT id,at,amount_fen,note FROM entries WHERE user_id=? AND merchant=? AND amount_fen=? AND direction='out' ORDER BY at,id",(uid,merchant,amount_fen)).fetchall()
        ids=set(candidate["entry_ids"])
        return {"merchant":merchant,"amount_fen":amount_fen,"payments":[dict(r) for r in rows if r["id"] in ids],"classification":candidate["classification"]}

    def create_task(self, uid, goal):
        tid=uuid.uuid4().hex
        with self.db.connect(write=True) as conn:
            conn.execute("INSERT INTO tasks(id,user_id,goal,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",(tid,uid,goal,"active",self.settings.stamp(),self.settings.stamp()))
            self.audit(conn,uid,"task_created",{"goal":goal},tid)
        return self.task(uid,tid)

    def _task(self, conn, uid, tid):
        row=conn.execute("SELECT * FROM tasks WHERE id=? AND user_id=?",(tid,uid)).fetchone()
        require(row is not None,"NOT_FOUND","任务不存在",404)
        return row

    def task(self, uid, tid, private=False):
        with self.db.connect() as conn:
            row=self._task(conn,uid,tid)
            result=dict(row)
            result["nodes"]=json.loads(result["nodes"])
            messages=json.loads(result.pop("messages"))
            if private:
                result["messages"]=messages
            else:
                result["conversation"]=[{"role":m["role"],"text":m.get("content") or ""} for m in messages if m["role"] in ("user","assistant") and m.get("content")]
            result["actions"]=[self._public_action(conn,r) for r in conn.execute("SELECT * FROM actions WHERE task_id=? AND user_id=? ORDER BY created_at,rowid",(tid,uid))]
            result["repeat_requests"]=[self._public_repeat(conn,r) for r in conn.execute("SELECT * FROM transfer_repeats WHERE task_id=? AND user_id=? ORDER BY rowid",(tid,uid))]
            return result

    def tasks(self, uid):
        with self.db.connect() as conn:
            ids=[r[0] for r in conn.execute("SELECT id FROM tasks WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 30",(uid,))]
        return [self.task(uid,tid) for tid in ids]

    def model_state(self, uid, tid):
        """事实快照不重复携带聊天；完整对话仍保存在数据库并提供给界面。"""
        task=self.task(uid,tid)
        task.pop("conversation",None)
        return task

    def save_messages(self, uid, tid, messages):
        with self.db.connect(write=True) as conn:
            self._task(conn,uid,tid)
            conn.execute("UPDATE tasks SET messages=?,updated_at=? WHERE id=?",(encode(messages),self.settings.stamp(),tid))

    def set_plan(self, uid, tid, nodes):
        require(1<=len(nodes)<=10,"INVALID_PLAN","计划必须有1至10步")
        known=set()
        allowed={"analysis","subscription","transfer","wealth","card"}
        for node in nodes:
            require(node["id"] not in known and node["kind"] in allowed,"INVALID_PLAN","步骤编号重复或类型不允许")
            require(all(dep in known for dep in node["depends_on"]),"INVALID_PLAN","依赖只能引用前面的步骤，不能成环")
            known.add(node["id"])
        with self.db.connect(write=True) as conn:
            task=self._task(conn,uid,tid)
            require(task["status"] not in ("cancelled","paused"),"TASK_STOPPED","任务已停止")
            old=json.loads(task["nodes"])
            old_by_id={n["id"]:n for n in old}
            requested={n["id"]:n for n in nodes}
            for prior in old:
                if prior["status"] in ("succeeded","waiting_confirmation","cancelled"):
                    new=requested.get(prior["id"])
                    require(new is not None and new["kind"]==prior["kind"] and new["depends_on"]==prior["depends_on"],
                            "PLAN_LOCKED","已完成或待确认步骤及其依赖不能覆盖，只能补充尚未执行步骤")
            shaped=[{**n,"status":old_by_id.get(n["id"],{}).get("status","pending"),
                     "action_id":old_by_id.get(n["id"],{}).get("action_id"),
                     "evidence":old_by_id.get(n["id"],{}).get("evidence")} for n in nodes]
            # 用户补充选择前已经完成的只读分析可以被新计划引用，不能凭模型声称完成。
            evidence=conn.execute("SELECT id,detail FROM audit WHERE user_id=? AND task_id=? AND event='analysis_completed' ORDER BY id DESC LIMIT 1",(uid,tid)).fetchone()
            if evidence:
                for node in shaped:
                    if node["kind"]=="analysis" and node["status"]=="pending" and not node["depends_on"]:
                        node["status"]="succeeded"
                        node["evidence"]={"audit_id":evidence["id"],**json.loads(evidence["detail"])}
                        break
            conn.execute("UPDATE tasks SET nodes=?,status='active' WHERE id=?",(encode(shaped),tid))
            self.audit(conn,uid,"plan_created",shaped,tid)
        return {"nodes":shaped}

    def mark_analysis(self, uid, tid, report=None):
        with self.db.connect(write=True) as conn:
            task=self._task(conn,uid,tid)
            if report is not None:
                self.audit(conn,uid,"analysis_completed",{"month":report["month"],"entry_ids":report["entry_ids"]},tid)
                audit_id=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            nodes=json.loads(task["nodes"])
            for node in nodes:
                if node["kind"]=="analysis" and node["status"]=="pending" and all(next(n for n in nodes if n["id"]==dep)["status"]=="succeeded" for dep in node["depends_on"]):
                    node["status"]="succeeded"
                    if report is not None:
                        node["evidence"]={"audit_id":audit_id,"month":report["month"],"entry_ids":report["entry_ids"]}
                    break
            conn.execute("UPDATE tasks SET nodes=? WHERE id=?",(encode(nodes),tid))

    def _node_for_action(self, conn, uid, tid, kind, aid):
        task=self._task(conn,uid,tid)
        nodes=json.loads(task["nodes"])
        if not nodes:
            return
        desired="wealth" if kind in WEALTH_KINDS else "card" if kind in CARD_KINDS else "subscription" if kind=="cancel_subscription" else "transfer"
        candidates=[n for n in nodes if n["kind"]==desired and n["status"] in ("pending","waiting_confirmation") and (n["action_id"] in (None,aid))]
        require(bool(candidates),"PLAN_STEP_MISSING","计划中没有待执行的对应业务步骤")
        node=candidates[0]
        require(all(next(n for n in nodes if n["id"]==dep)["status"]=="succeeded" for dep in node["depends_on"]),"DEPENDENCY_PENDING","前置步骤尚未完成")
        node.update(status="waiting_confirmation",action_id=aid)
        conn.execute("UPDATE tasks SET nodes=? WHERE id=?",(encode(nodes),tid))

    def _complete_node(self, conn, task_id, aid):
        row=conn.execute("SELECT nodes,goal FROM tasks WHERE id=?",(task_id,)).fetchone()
        nodes=json.loads(row[0])
        for node in nodes:
            if node["action_id"]==aid:
                node["status"]="succeeded"
        complete=bool(nodes) and any(n["status"]=="succeeded" for n in nodes) and all(n["status"] in ("succeeded","cancelled") for n in nodes)
        complete=complete or row["goal"].startswith("[手动业务验证]")
        conn.execute("UPDATE tasks SET nodes=?,status=? WHERE id=?",(encode(nodes),"completed" if complete else "active",task_id))

    def _daily_total(self, conn, uid):
        return conn.execute("SELECT coalesce(sum(amount_fen),0) FROM entries WHERE user_id=? AND action_id IS NOT NULL AND category='转账' AND direction='out' AND substr(at,1,10)=?",(uid,self.settings.business_date)).fetchone()[0]

    def _validate_params(self, conn, uid, kind, params):
        if kind in EXTENDED_KINDS:
            return self._validate_extended(conn,uid,kind,params)
        if kind=="transfer":
            amount, reserve=params["amount_fen"],params.get("reserve_fen",0)
            require(type(amount) is int and amount>0,"INVALID_AMOUNT","金额必须是正整数分")
            require(type(reserve) is int and reserve>=0,"INVALID_RESERVE","保留余额必须为非负整数分")
            require(amount<=100000000,"AMOUNT_LIMIT","单笔模拟转账上限100万元")
            recipient=conn.execute("SELECT 1 FROM recipients WHERE id=? AND user_id=?",(params["recipient_id"],uid)).fetchone()
            require(recipient is not None,"NOT_FOUND","收款人不存在",404)
            balance=conn.execute("SELECT balance_fen FROM accounts WHERE user_id=?",(uid,)).fetchone()[0]
            require(balance-amount>=reserve,"INSUFFICIENT_BALANCE","余额不足或不满足保留余额约束")
            return "red" if self._daily_total(conn,uid)+amount>100000 else "yellow"
        require(kind=="cancel_subscription","INVALID_KIND","操作类型不允许")
        subscription=conn.execute("SELECT * FROM subscriptions WHERE id=? AND user_id=?",(params["subscription_id"],uid)).fetchone()
        require(subscription is not None,"NOT_FOUND","订阅不存在",404)
        require(subscription["cancellable"]==1,"NOT_CANCELLABLE","该周期付款不能在此取消")
        require(subscription["status"]=="active","ALREADY_CANCELLED","订阅已经取消")
        return "yellow"

    def prepare(self, uid, tid, kind, params):
        if kind in WEALTH_KINDS:
            require(self.settings.legacy_wealth_enabled,"FEATURE_REMOVED","模拟理财交易已移除，当前仅支持风险评估和银行产品查询",410)
        aid=uuid.uuid4().hex
        with self.db.connect(write=True) as conn:
            task=self._task(conn,uid,tid)
            require(task["status"] not in ("cancelled","paused"),"TASK_STOPPED","任务已停止")
            if kind=="transfer":
                self._validate_params(conn,uid,kind,params)
                matches=[]
                for prior in conn.execute("SELECT * FROM actions WHERE user_id=? AND kind='transfer' AND (task_id=? OR substr(created_at,1,10)=?) ORDER BY rowid DESC",(uid,tid,self.settings.business_date)):
                    old=json.loads(prior["params"])
                    if old["recipient_id"]==params["recipient_id"] and old["amount_fen"]==params["amount_fen"]:
                        matches.append(prior)
                pending=next((r for r in matches if r["status"]=="pending"),None)
                if pending:
                    require(pending["task_id"]==tid,"TRANSFER_ALREADY_PENDING","另一个任务已有同收款人、同金额的待确认转账，请先处理原草稿")
                    old=json.loads(pending["params"])
                    require(params.get("reserve_fen",0)<=old.get("reserve_fen",0),"USE_REVISE","已有待确认草稿，请通过修改操作提高保留余额")
                    return self._public_action(conn,pending)
                closed=next((r for r in matches if r["task_id"]==tid or r["status"]=="executed"),None)
                if closed:
                    same_task=[r for r in matches if r["task_id"]==tid]
                    source=same_task[0] if same_task else closed
                    if same_task and "repeat_request_id" not in json.loads(source["params"]):
                        # 修复前的旧草稿也可能已降低约束；恢复原任务记录中的最低要求。
                        params={**params,"reserve_fen":max(params.get("reserve_fen",0),*(json.loads(r["params"]).get("reserve_fen",0) for r in same_task))}
                    return self._request_repeat(conn,uid,tid,source,params)
            if kind in EXTENDED_KINDS:
                params=self._bind_extended(conn,uid,kind,params)
            existing=conn.execute("SELECT * FROM actions WHERE task_id=? AND kind=? AND params=?",(tid,kind,encode(params))).fetchone()
            if existing and existing["status"] in ("pending","executed"):
                return self._public_action(conn,existing)
            risk=self._validate_params(conn,uid,kind,params)
            self._node_for_action(conn,uid,tid,kind,aid)
            if existing:
                require(False,"ACTION_REJECTED","该操作已被拒绝或取消，请明确修改请求后新建操作")
            conn.execute("INSERT INTO actions(id,user_id,task_id,kind,params,status,risk,created_at) VALUES(?,?,?,?,?,?,?,?)",(aid,uid,tid,kind,encode(params),"pending",risk,self.settings.stamp()))
            conn.execute("UPDATE tasks SET status='waiting_confirmation' WHERE id=?",(tid,))
            self.audit(conn,uid,"action_prepared",{"kind":kind,"params":params,"risk":risk,"version":1},tid,aid)
            return self._public_action(conn,conn.execute("SELECT * FROM actions WHERE id=?",(aid,)).fetchone())

    def _public_repeat(self, conn, row):
        result={k:row[k] for k in ("id","task_id","source_action_id","source_version","version","status","result_action_id","created_at")}
        result["params"]=json.loads(row["params"])
        source=self._action(conn,row["user_id"],row["source_action_id"])
        result["source_status"]=source["status"]
        result["source_created_at"]=source["created_at"]
        result["recipient"]=self._public_action(conn,source)["recipient"]
        return result

    def _request_repeat(self, conn, uid, tid, source, requested):
        old=json.loads(source["params"])
        params={k:old[k] for k in ("recipient_id","amount_fen")}
        params["reserve_fen"]=max(old.get("reserve_fen",0),requested.get("reserve_fen",0))
        row=conn.execute("SELECT * FROM transfer_repeats WHERE task_id=? AND source_action_id=?",(tid,source["id"])).fetchone()
        if not row:
            rid=uuid.uuid4().hex
            conn.execute("INSERT INTO transfer_repeats(id,user_id,task_id,source_action_id,source_version,params,created_at) VALUES(?,?,?,?,?,?,?)",
                         (rid,uid,tid,source["id"],source["version"],encode(params),self.settings.stamp()))
            self.audit(conn,uid,"transfer_repeat_requested",{"source_action_id":source["id"],"params":params},tid)
            row=conn.execute("SELECT * FROM transfer_repeats WHERE id=?",(rid,)).fetchone()
        elif row["status"]=="pending" and params["reserve_fen"]>json.loads(row["params"])["reserve_fen"]:
            conn.execute("UPDATE transfer_repeats SET params=?,version=version+1 WHERE id=?",(encode(params),row["id"]))
            row=conn.execute("SELECT * FROM transfer_repeats WHERE id=?",(row["id"],)).fetchone()
        if row["status"]=="approved":
            return self._public_action(conn,self._action(conn,uid,row["result_action_id"]))
        conn.execute("UPDATE tasks SET status='awaiting_input' WHERE id=?",(tid,))
        return {"repeat_confirmation_required":True,"repeat_request":self._public_repeat(conn,row)}

    def decide_repeat(self, uid, rid, version, repeat):
        with self.db.connect(write=True) as conn:
            row=conn.execute("SELECT * FROM transfer_repeats WHERE id=? AND user_id=?",(rid,uid)).fetchone()
            require(row is not None,"NOT_FOUND","重复转账询问不存在",404)
            require(row["version"]==version,"STALE_CONFIRMATION","重复转账内容已改变，请刷新核对",409)
            task=self._task(conn,uid,row["task_id"])
            require(task["status"] not in ("paused","cancelled"),"TASK_STOPPED","任务已停止")
            if row["status"]=="approved":
                require(repeat,"REPEAT_DECIDED","已选择重新创建，请拒绝新的转账草稿")
                return self._public_action(conn,self._action(conn,uid,row["result_action_id"]))
            if row["status"]=="declined":
                require(not repeat,"REPEAT_DECIDED","已选择不再转账，请新建对话重新提出需求")
                return {"status":"declined"}
            require(row["status"]=="pending","REPEAT_DECIDED","询问已关闭")
            source=self._action(conn,uid,row["source_action_id"])
            require(source["version"]==row["source_version"] and source["status"] in ("executed","rejected","cancelled"),"STALE_CONFIRMATION","原操作状态已改变")
            if not repeat:
                conn.execute("UPDATE transfer_repeats SET status='declined' WHERE id=?",(rid,))
                nodes=json.loads(task["nodes"])
                node=next((n for n in nodes if n["kind"]=="transfer" and n["status"]=="pending" and n["action_id"] is None),None)
                if node: node["status"]="cancelled"
                conn.execute("UPDATE tasks SET nodes=?,status='awaiting_input' WHERE id=?",(encode(nodes),row["task_id"]))
                self.audit(conn,uid,"transfer_repeat_declined",{"repeat_request_id":rid},row["task_id"])
                return {"status":"declined"}
            params=json.loads(row["params"])
            # 新的尝试编号由可信用户决定产生，不由模型改变金额/约束来产生。
            params.update(repeat_of=source["id"],repeat_request_id=rid)
            risk=self._validate_params(conn,uid,"transfer",params)
            aid=uuid.uuid4().hex
            nodes=json.loads(task["nodes"])
            candidate=next((n for n in nodes if n["kind"]=="transfer" and n["status"]=="pending" and n["action_id"] is None),None)
            if candidate:
                candidate["title"]=f"重新发起转账{params['amount_fen']/100:.2f}元，保留{params['reserve_fen']/100:.2f}元"
                conn.execute("UPDATE tasks SET nodes=? WHERE id=?",(encode(nodes),row["task_id"]))
            if nodes and not any(n["kind"]=="transfer" and n["status"]=="pending" and n["action_id"] is None for n in nodes):
                require(len(nodes)<10,"PLAN_LIMIT","计划已满，请新建对话")
                prior=next((n for n in nodes if n["action_id"]==source["id"]),None)
                deps=([prior["id"]] if prior and prior["status"]=="succeeded" else prior["depends_on"] if prior else [])
                nodes.append({"id":"repeat_"+rid[:16],"title":f"重新发起转账{params['amount_fen']/100:.2f}元，保留{params['reserve_fen']/100:.2f}元","kind":"transfer","depends_on":deps,"status":"pending","action_id":None,"evidence":None})
                conn.execute("UPDATE tasks SET nodes=? WHERE id=?",(encode(nodes),row["task_id"]))
            self._node_for_action(conn,uid,row["task_id"],"transfer",aid)
            conn.execute("INSERT INTO actions(id,user_id,task_id,kind,params,status,risk,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (aid,uid,row["task_id"],"transfer",encode(params),"pending",risk,self.settings.stamp()))
            conn.execute("UPDATE transfer_repeats SET status='approved',result_action_id=? WHERE id=?",(aid,rid))
            conn.execute("UPDATE tasks SET status='waiting_confirmation' WHERE id=?",(row["task_id"],))
            self.audit(conn,uid,"transfer_repeat_approved",{"repeat_request_id":rid,"source_action_id":source["id"]},row["task_id"],aid)
            self.audit(conn,uid,"action_prepared",{"kind":"transfer","params":params,"risk":risk,"version":1},row["task_id"],aid)
            return self._public_action(conn,self._action(conn,uid,aid))

    def _action(self, conn, uid, aid):
        row=conn.execute("SELECT * FROM actions WHERE id=? AND user_id=?",(aid,uid)).fetchone()
        require(row is not None,"NOT_FOUND","操作不存在",404)
        return row

    def _public_action(self, conn, row):
        params=json.loads(row["params"])
        result={k:row[k] for k in ("id","task_id","kind","version","status","risk","created_at")}
        result["params"]={k:v for k,v in params.items() if k not in ("repeat_of","repeat_request_id")}
        for key in ("repeat_of","repeat_request_id"):
            if key in params: result[key]=params[key]
        result["result"]=json.loads(row["result"]) if row["result"] else None
        result["verified"]=row["verified_version"]==row["version"] and bool(row["otp_expires"]) and row["otp_expires"]>time.time()
        if row["kind"]=="transfer":
            if row["status"]=="pending":
                result["risk"]="red" if self._daily_total(conn,row["user_id"])+params["amount_fen"]>100000 else "yellow"
            recipient=conn.execute("SELECT r.name,r.phone_tail,d.bank_name,substr(d.account_number,-4) account_tail FROM recipients r LEFT JOIN recipient_details d ON d.recipient_id=r.id AND d.user_id=r.user_id WHERE r.id=? AND r.user_id=?",(params["recipient_id"],row["user_id"])).fetchone()
            result["recipient"]=dict(recipient)
        elif row["kind"] in EXTENDED_KINDS:
            result.update(self._public_extended(conn,row,params))
        else:
            sub=conn.execute("SELECT merchant,amount_fen,next_at,status FROM subscriptions WHERE id=? AND user_id=?",(params["subscription_id"],row["user_id"])).fetchone()
            result["subscription"]=dict(sub)
        return result

    def action(self, uid, aid):
        with self.db.connect() as conn:
            return self._public_action(conn,self._action(conn,uid,aid))

    def revise(self, uid, aid, version, params, allow_reserve_change=False):
        with self.db.connect(write=True) as conn:
            row=self._action(conn,uid,aid)
            require(row["kind"]=="transfer","INVALID_KIND","目前仅支持修改转账草稿，其他操作请拒绝后新建")
            require(row["status"]=="pending","ACTION_CLOSED","只能修改待确认操作")
            require(row["version"]==version,"STALE_CONFIRMATION","操作已改变，请刷新",409)
            require(self._task(conn,uid,row["task_id"])["status"] not in ("cancelled","paused"),"TASK_STOPPED","任务已停止")
            old=json.loads(row["params"])
            require(allow_reserve_change or params.get("reserve_fen",0)>=old.get("reserve_fen",0),"RESERVE_CHANGE_REQUIRES_USER","模型不能降低已记录的保留余额，请由用户明确修改")
            params={**params,**{k:old[k] for k in ("repeat_of","repeat_request_id") if k in old}}
            risk=self._validate_params(conn,uid,row["kind"],params)
            try:
                conn.execute("UPDATE actions SET params=?,version=version+1,risk=?,otp_hash=NULL,verified_version=NULL,otp_attempts=0 WHERE id=?",(encode(params),risk,aid))
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    raise BankError("DUPLICATE_ACTION","同任务已存在该操作") from exc
                raise
            self.audit(conn,uid,"action_revised",{"params":params,"version":version+1},row["task_id"],aid)
        return self.action(uid,aid)

    def challenge(self, uid, aid, version):
        with self.db.connect(write=True) as conn:
            row=self._action(conn,uid,aid)
            require(row["status"]=="pending" and row["version"]==version,"STALE_CONFIRMATION","操作已改变或关闭",409)
            require(row["otp_attempts"]<3,"VERIFICATION_LOCKED","验证码错误已达3次，请拒绝操作并新建任务")
            code=f"{secrets.randbelow(1000000):06d}"
            digest=hashlib.sha256(f"{aid}:{version}:{code}".encode()).hexdigest()
            conn.execute("UPDATE actions SET otp_hash=?,otp_expires=?,verified_version=NULL WHERE id=?",(digest,time.time()+120,aid))
            self.audit(conn,uid,"simulation_challenge_created",{"version":version},row["task_id"],aid)
            return {"simulation":True,"demo_code":code,"expires_in":120,"warning":"此验证码在同一模拟界面展示，仅用于流程演示，不是真实多因子认证"}

    def verify(self, uid, aid, version, code):
        error=None
        with self.db.connect(write=True) as conn:
            row=self._action(conn,uid,aid)
            require(row["status"]=="pending" and row["version"]==version,"STALE_CONFIRMATION","操作已改变或关闭",409)
            require(row["otp_attempts"]<3,"VERIFICATION_LOCKED","验证码错误已达3次")
            require(row["otp_hash"] and row["otp_expires"]>time.time(),"CODE_EXPIRED","请重新获取模拟验证码")
            digest=hashlib.sha256(f"{aid}:{version}:{code}".encode()).hexdigest()
            if not hmac.compare_digest(digest,row["otp_hash"]):
                conn.execute("UPDATE actions SET otp_attempts=otp_attempts+1 WHERE id=?",(aid,))
                self.audit(conn,uid,"verification_failed",{"version":version},row["task_id"],aid)
                error=BankError("INVALID_CODE","验证码错误")
            else:
                conn.execute("UPDATE actions SET verified_version=? WHERE id=?",(version,aid))
                self.audit(conn,uid,"simulation_verified",{"version":version},row["task_id"],aid)
        if error:
            raise error
        return {"verified":True,"version":version}

    def confirm(self, uid, aid, version, risk_acknowledged=False):
        with self.db.connect(write=True) as conn:
            row=self._action(conn,uid,aid)
            require(row["version"]==version,"STALE_CONFIRMATION","旧确认已失效，请核对最新操作",409)
            if row["status"]=="executed":
                return self._public_action(conn,row)
            require(row["status"]=="pending","ACTION_CLOSED","操作已关闭")
            task=self._task(conn,uid,row["task_id"])
            require(task["status"] not in ("cancelled","paused"),"TASK_STOPPED","任务已停止")
            params=json.loads(row["params"])
            risk=self._validate_params(conn,uid,row["kind"],params)
            if row["kind"] in WEALTH_KINDS:
                require(risk_acknowledged is True,"RISK_ACK_REQUIRED","请本人勾选理解模拟产品与本金记账说明后确认")
            if risk=="red":
                require(row["verified_version"]==version and row["otp_expires"] and row["otp_expires"]>time.time(),"REQUIRES_VERIFICATION","此操作需要有效的模拟强验证，请先核对操作卡",409)
            self._node_for_action(conn,uid,row["task_id"],row["kind"],aid)
            self.audit(conn,uid,"user_confirmed",{"version":version,"risk":risk},row["task_id"],aid)
            if row["kind"]=="transfer":
                conn.execute("UPDATE accounts SET balance_fen=balance_fen-? WHERE user_id=?",(params["amount_fen"],uid))
                recipient=conn.execute("SELECT name FROM recipients WHERE id=?",(params["recipient_id"],)).fetchone()
                txid="tx_"+uuid.uuid4().hex
                conn.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?)",(txid,uid,self.settings.stamp(),"out",params["amount_fen"],"转账",recipient[0],"用户明确确认的模拟转账",params["recipient_id"],aid))
                result={"transaction_id":txid,"amount_fen":params["amount_fen"],"balance_fen":conn.execute("SELECT balance_fen FROM accounts WHERE user_id=?",(uid,)).fetchone()[0]}
            elif row["kind"] in EXTENDED_KINDS:
                result=self._execute_extended(conn,uid,row,params)
            else:
                conn.execute("UPDATE subscriptions SET status='cancelled' WHERE id=? AND user_id=?",(params["subscription_id"],uid))
                result={"subscription_id":params["subscription_id"],"status":"cancelled","balance_changed":False,"future_savings_only":True}
            conn.execute("UPDATE actions SET status='executed',risk=?,result=?,otp_hash=NULL WHERE id=?",(risk,encode(result),aid))
            self._complete_node(conn,row["task_id"],aid)
            self.audit(conn,uid,"action_executed",result,row["task_id"],aid)
            return self._public_action(conn,conn.execute("SELECT * FROM actions WHERE id=?",(aid,)).fetchone())

    def reject(self, uid, aid, version):
        with self.db.connect(write=True) as conn:
            row=self._action(conn,uid,aid)
            require(row["version"]==version,"STALE_CONFIRMATION","操作已改变",409)
            require(row["status"]=="pending","ACTION_CLOSED","操作已关闭")
            conn.execute("UPDATE actions SET status='rejected',otp_hash=NULL,verified_version=NULL WHERE id=?",(aid,))
            task=self._task(conn,uid,row["task_id"])
            nodes=json.loads(task["nodes"])
            for node in nodes:
                if node["action_id"]==aid:
                    node["status"]="cancelled"
            conn.execute("UPDATE tasks SET nodes=?,status='awaiting_input' WHERE id=?",(encode(nodes),row["task_id"]))
            self.audit(conn,uid,"action_rejected",{"version":version},row["task_id"],aid)
        return self.action(uid,aid)

    def cancel_task(self, uid, tid):
        with self.db.connect(write=True) as conn:
            task=self._task(conn,uid,tid)
            nodes=json.loads(task["nodes"])
            for node in nodes:
                if node["status"]!="succeeded":
                    node["status"]="cancelled"
            conn.execute("UPDATE actions SET status='cancelled',otp_hash=NULL,verified_version=NULL WHERE task_id=? AND user_id=? AND status='pending'",(tid,uid))
            conn.execute("UPDATE transfer_repeats SET status='cancelled' WHERE task_id=? AND user_id=? AND status='pending'",(tid,uid))
            conn.execute("UPDATE tasks SET status='cancelled',nodes=? WHERE id=?",(encode(nodes),tid))
            self.audit(conn,uid,"task_cancelled",{"completed_steps_retained":True},tid)
        return self.task(uid,tid)

    def resume_task(self, uid, tid):
        with self.db.connect(write=True) as conn:
            task=self._task(conn,uid,tid)
            require(task["status"]!="cancelled","TASK_CANCELLED","已取消任务不能恢复，请新建任务")
            conn.execute("UPDATE tasks SET status='active',failures=0 WHERE id=?",(tid,))
            self.audit(conn,uid,"user_resumed",{},tid)
        return self.task(uid,tid)

    def tool_event(self, uid, tid, tool, args, result, failed=False):
        with self.db.connect(write=True) as conn:
            task=self._task(conn,uid,tid)
            self.audit(conn,uid,"tool_rejected" if failed else "tool_completed",{"tool":tool,"arguments":args,"result":result},tid)
            failures=task["failures"]+1 if failed else 0
            status="paused" if failures>=3 and task["status"] not in ("cancelled","paused") else task["status"]
            conn.execute("UPDATE tasks SET failures=?,status=?,updated_at=? WHERE id=?",(failures,status,self.settings.stamp(),tid))

    def set_status(self, uid, tid, status):
        with self.db.connect(write=True) as conn:
            task=self._task(conn,uid,tid)
            if task["status"] not in ("cancelled","paused"):
                conn.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?",(status,self.settings.stamp(),tid))

    def audit_log(self, uid, tid=None):
        with self.db.connect() as conn:
            if tid:
                self._task(conn,uid,tid)
            rows=conn.execute("SELECT * FROM audit WHERE user_id=? AND (? IS NULL OR task_id=?) ORDER BY id",(uid,tid,tid)).fetchall()
            return [{**dict(row),"detail":json.loads(row["detail"])} for row in rows]
