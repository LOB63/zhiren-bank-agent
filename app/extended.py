"""理财与信用卡模拟业务；所有写动作复用操作版本、确认和事务。

产品为虚构教学产品，按本金记账，无真实市场价格、收益或银行授信。
"""
from datetime import date, timedelta
import json
import uuid
from .db import encode
from .errors import require

MERCHANTS = (
    {"id":"demo_supermarket","name":"模拟超市","category":"日用零售","channels":["online","offline"]},
    {"id":"demo_shop","name":"模拟商城","category":"普通零售","channels":["online","offline"]},
    {"id":"demo_transit","name":"模拟出行","category":"交通服务","channels":["online","offline"]},
)

PRODUCTS = (
    {"id":"demo_cash","name":"模拟灵活现金","risk_level":1,"min_fen":10000,"lock_days":0,
     "description":"支持当日赎回，无锁定期。"},
    {"id":"demo_balanced","name":"模拟稳健组合","risk_level":2,"min_fen":100000,"lock_days":7,
     "description":"每笔申购后锁定7天，到期可申请赎回。"},
    {"id":"demo_growth","name":"模拟成长组合","risk_level":3,"min_fen":100000,"lock_days":30,
     "description":"每笔申购后锁定30天，到期可申请赎回。"},
)
WEALTH_KINDS={"wealth_buy","wealth_redeem"}
CARD_KINDS={"card_apply","card_limit","card_freeze","card_unlock","card_lost","card_online","card_payment","card_repay"}
EXTENDED_KINDS=WEALTH_KINDS|CARD_KINDS


class ExtendedBankMixin:
    def risk_profile(self, uid, conn=None):
        if conn is None:
            with self.db.connect() as handle:
                return self.risk_profile(uid,handle)
        row=conn.execute("SELECT * FROM risk_profiles WHERE user_id=?",(uid,)).fetchone()
        if not row:
            return {"completed":False,"requires_user_form":True,"message":"请本人在理财页面填写模拟风险问卷，模型不能代填。"}
        result=dict(row);result["completed"]=True
        result["expired"]=result["expires_at"]<self.settings.business_date
        result["simulation"]=True
        result['assessment']=json.loads(result.pop('assessment_json',None) or 'null')
        result['assessment_current']=bool(result['assessment'] and result['assessment'].get('schema_version')==2)
        if result['assessment_current']:
            result['risk_label']=f"C{result['assessment']['category']} {result['assessment']['category_name']}"
            result['product_ceiling_label']=f"R{result['risk_level']}"
        return result

    def save_risk_profile(self, uid, answers):
        require(answers.get("confirm") is True,"PROFILE_CONSENT_REQUIRED","请确认问卷答案由本人填写")
        loss=answers.get("loss_tolerance");experience=answers.get("experience");days=answers.get("horizon_days")
        require(loss in ("none","limited","substantial") and experience in ("none","some","experienced")
                and type(days) is int and 1<=days<=3650,"INVALID_PROFILE","风险问卷答案不合法")
        from .risk import assess
        assessment=assess(answers.get('assessment'),loss,experience,days)
        level=assessment['risk_level']
        expires=(date.fromisoformat(self.settings.business_date)+timedelta(days=365)).isoformat()
        with self.db.connect(write=True) as conn:
            prior=conn.execute("SELECT version FROM risk_profiles WHERE user_id=?",(uid,)).fetchone()
            version=prior[0]+1 if prior else 1
            conn.execute("INSERT INTO risk_profiles VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET loss_tolerance=excluded.loss_tolerance,experience=excluded.experience,horizon_days=excluded.horizon_days,risk_level=excluded.risk_level,version=excluded.version,expires_at=excluded.expires_at,updated_at=excluded.updated_at,assessment_json=excluded.assessment_json",
                         (uid,loss,experience,days,level,version,expires,self.settings.stamp(),encode(assessment)))
            self.audit(conn,uid,"user_risk_profile_saved",{"version":version,"risk_level":level,"source":"authenticated_user_form","simulation":True})
        return self.risk_profile(uid)

    def _product(self, pid):
        product=next((dict(p) for p in PRODUCTS if p["id"]==pid),None)
        require(product is not None,"NOT_FOUND","模拟产品不存在",404)
        return product

    def wealth(self, uid):
        profile=self.risk_profile(uid)
        products=[];holdings=[]
        with self.db.connect() as conn:
            for original in PRODUCTS:
                p=dict(original)
                reasons=[]
                if not profile.get("completed"):reasons.append("尚未完成本人风险问卷")
                elif not profile.get('assessment_current'):reasons.append('请补充新版完整评估，旧三项问卷不再用于申购')
                elif profile.get("expired"):reasons.append("问卷已过期，需重新评估")
                else:
                    if profile['assessment']['purchase_blocked']:reasons.append('评估显示当前不适合非保本投资')
                    if profile["loss_tolerance"]=="none":reasons.append("不能接受本金损失，当前产品均非保本")
                    if profile["risk_level"]<p["risk_level"]:reasons.append(f"问卷R{profile['risk_level']}低于产品R{p['risk_level']}")
                    if profile["horizon_days"]<p["lock_days"]:reasons.append(f"用钱时间{profile['horizon_days']}天短于锁定期{p['lock_days']}天")
                p["eligible"]=not reasons
                p["eligibility_reasons"]=reasons
                p.update(simulation=True,valuation_method="principal_only_demo",promised_return=None,fees_fen=0)
                products.append(p)
                lots=[dict(r) for r in conn.execute("SELECT id,principal_fen,available_at FROM investment_lots WHERE user_id=? AND product_id=? AND principal_fen>0 ORDER BY available_at,id",(uid,p["id"]))]
                total=sum(r["principal_fen"] for r in lots)
                available=sum(r["principal_fen"] for r in lots if r["available_at"]<=self.settings.business_date)
                holdings.append({"product_id":p["id"],"product_name":p["name"],"principal_fen":total,"redeemable_fen":available,"lots":lots})
        recommendation=self.investment_guidance(uid,profile,products,holdings)
        if not self.settings.legacy_wealth_enabled:
            products=[]
        return {"products":products,"profile":profile,"holdings":holdings,"simulation":True,
                "trading_enabled":self.settings.legacy_wealth_enabled,
                'recommendation':recommendation,
                "valuation_method":"principal_only_demo","estimated_profit_fen":None,
                "limitations":["虚构产品，风险等级仅为问卷演示规则，不是真实银行适当性评估",
                               "按申购本金记账，不模拟净值、收益和实际市场损失；本金赎回数值不是保本承诺",
                               "锁定期按北京时间业务日期计算；固定日期评测模式仅用于复现实验"]}

    def _card(self, conn, uid, cid):
        row=conn.execute("SELECT * FROM cards WHERE id=? AND user_id=?",(cid,uid)).fetchone()
        require(row is not None,"NOT_FOUND","卡片不存在",404)
        return row

    def investment_guidance(self,uid,profile,products,holdings):
        if not profile.get('assessment_current') or profile.get('expired'):
            return {'available':False,'summary':'完成新版评估后，生成与你的财务安排相对应的建议。'}
        a=profile['assessment'];answers=a['answers'];context=self.customer_context(uid)
        cash=self.balance(uid)['balance_fen']
        months=3 if answers['income_source']=='stable' else 6
        emergency=answers['monthly_essential_fen']*months
        due=sum(x['amount_fen'] for x in context.get('upcoming_outlays',[]))
        debt=sum(c['used_fen'] for c in self.cards(uid)['cards'])
        reserve=max(context.get('reserve_target_fen',0),emergency+due+debt)
        ceiling=max(0,cash-reserve)
        legacy=self.settings.legacy_wealth_enabled
        eligible=[p for p in products if p['eligible']] if legacy else []
        blocked=a['purchase_blocked'] or answers['debt']=='high' or answers['emergency']=='under1'
        trial=(ceiling*{1:10,2:15,3:20,4:25,5:30}[a['category']]//100)//100*100 if (eligible or not legacy) and not blocked else 0
        preferred=eligible[0] if eligible else None
        if preferred and trial<preferred['min_fen']:trial=0
        summary=('先补足应急金、处理偿债压力，暂不新增投资。' if blocked else
                 '当前资金应优先保障近期用款，暂不建议新增投资。' if not trial else
                 f"先保留{reserve/100:.2f}元作为资金缓冲，可考虑以{trial/100:.2f}元小额尝试{preferred['name']}；其余现金继续保留。" if legacy else
                 f"先保留{reserve/100:.2f}元作为资金缓冲，可将{trial/100:.2f}元作为初步了解投资方案的参考金额；具体产品须另行核对风险、期限和流动性。")
        return {'available':True,'summary':summary,'reserve_fen':reserve,'cash_fen':cash,'cash_ceiling_fen':ceiling,'suggested_trial_fen':trial,
                'preferred_product_id':preferred['id'] if preferred and trial else None,
                'reasons':[f'按本人填写的每月必要支出保留{months}个月应急金，共{emergency/100:.2f}元',
                           f'另计登记待核对用款{due/100:.2f}元与已记录信用卡待还{debt/100:.2f}元；与登记预留取较大值',
                           '优先核对流动性、风险和用款期限，不因承受能力较高就选择最高风险产品',
                           f"已有持仓本金{sum(h['principal_fen'] for h in holdings)/100:.2f}元，锁定持仓不作为可立即使用现金"],
                'amounts_yuan':{'cash':f'{cash/100:.2f}','reserve':f'{reserve/100:.2f}','cash_ceiling':f'{ceiling/100:.2f}','suggested_trial':f'{trial/100:.2f}','known_outlays':f'{due/100:.2f}','known_card_debt':f'{debt/100:.2f}'},
                'basis':'基于本人问卷与本账户已记录资料的模拟建议；未核实外部负债，预留按保守口径计算，可能包含重叠支出。试投使用本项目10%至30%的示例比例，并非经收益回测优化的配置。',
                'requires_user_decision':True}

    def _card_spent(self, conn, uid, cid):
        rows=conn.execute("SELECT a.params FROM actions a JOIN entries e ON e.action_id=a.id WHERE a.user_id=? AND e.user_id=? AND a.kind='card_payment' AND a.status='executed' AND substr(e.at,1,10)=?",(uid,uid,self.settings.business_date))
        return sum(p["amount_fen"] for r in rows if (p:=json.loads(r[0]))["card_id"]==cid)

    def cards(self, uid):
        with self.db.connect() as conn:
            result=[]
            for row in conn.execute("SELECT * FROM cards WHERE user_id=? ORDER BY id",(uid,)):
                item=dict(row);item.update(available_credit_fen=row["credit_limit_fen"]-row["used_fen"],today_spent_fen=self._card_spent(conn,uid,row["id"]),simulation=True)
                result.append(item)
        return {"cards":result,"merchants":[dict(m) for m in MERCHANTS],"simulation":True,"max_cards":2,"max_credit_limit_fen":1000000,
                "limitations":["模拟信用卡，申请审批与额度是演示规则，不构成真实授信",
                               "刷卡增加待还款，不减少现金余额；还款减少现金和待还款，限额不影响普通账户转账",
                               "无真实卡号、利息、账单日或外部商户清算；挂失后不可在此解挂"]}

    def _bind_extended(self, conn, uid, kind, params):
        params=dict(params)
        if kind=="wealth_buy":
            profile=self.risk_profile(uid,conn)
            params["profile_version"]=profile.get("version",0)
        if kind=="card_payment":
            merchant=next((m for m in MERCHANTS if m["id"]==params.get("merchant_id")),None)
            require(merchant is not None,"MERCHANT_NOT_ALLOWED","只允许登记商户刷卡；个人收款、理财与未登记商户不支持")
            params["merchant"]=merchant["name"]
        if kind in CARD_KINDS and kind!="card_apply":
            card=self._card(conn,uid,params["card_id"])
            params["card_version"]=card["version"]
        return params

    def _validate_extended(self, conn, uid, kind, params):
        require(kind in EXTENDED_KINDS,"INVALID_KIND","操作类型不允许")
        if kind in WEALTH_KINDS:
            require(self.settings.legacy_wealth_enabled,"FEATURE_REMOVED","模拟理财交易已移除，当前仅支持风险评估和银行产品查询",410)
            p=self._product(params["product_id"]);amount=params["amount_fen"]
            require(type(amount) is int and 0<amount<=100000000,"INVALID_AMOUNT","理财金额必须为正整数分，最多100万元")
            if kind=="wealth_buy":
                profile=self.risk_profile(uid,conn)
                require(profile["completed"] and not profile["expired"],"RISK_PROFILE_REQUIRED","请本人先在理财页面完成有效的模拟风险问卷")
                require(profile.get('assessment_current'),"RISK_PROFILE_REQUIRED","请本人补充新版完整风险评估")
                require(params.get("profile_version")==profile["version"],"PROFILE_CHANGED","风险问卷已改变，请重新创建申购草稿",409)
                require(not profile['assessment']['purchase_blocked'],'PRODUCT_UNSUITABLE','当前评估不适合非保本投资')
                require(profile["loss_tolerance"]!="none","PRODUCT_UNSUITABLE","当前模拟产品均非保本，不能接受本金损失时不可申购")
                require(profile["risk_level"]>=p["risk_level"] and profile["horizon_days"]>=p["lock_days"],"PRODUCT_UNSUITABLE","产品风险或锁定期不符合问卷条件")
                require(amount>=p["min_fen"],"BELOW_MINIMUM","金额低于产品起购金额")
                reserve=params.get("reserve_fen",0)
                require(type(reserve) is int and reserve>=0,"INVALID_RESERVE","保留余额必须为非负整数分")
                balance=conn.execute("SELECT balance_fen FROM accounts WHERE user_id=?",(uid,)).fetchone()[0]
                require(balance-amount>=reserve,"INSUFFICIENT_BALANCE","申购后余额不足或不满足保留余额约束")
            else:
                available=conn.execute("SELECT coalesce(sum(principal_fen),0) FROM investment_lots WHERE user_id=? AND product_id=? AND available_at<=?",(uid,p["id"],self.settings.business_date)).fetchone()[0]
                require(available>=amount,"INSUFFICIENT_HOLDINGS","可赎回本金不足，部分持仓可能仍在锁定期")
            return "red"
        if kind=="card_apply":
            require(conn.execute("SELECT count(*) FROM cards WHERE user_id=?",(uid,)).fetchone()[0]<2,"CARD_COUNT_LIMIT","模拟环境每人最多两张卡，挂失卡仍占名额")
            return "red"
        card=self._card(conn,uid,params["card_id"])
        require(params.get("card_version")==card["version"],"CARD_CHANGED","卡片状态或额度已改变，请重新创建操作草稿",409)
        amount=params.get("amount_fen")
        if kind=="card_repay":
            require(type(amount) is int and 0<amount<=card["used_fen"],"INVALID_REPAYMENT","还款金额必须为正且不超过待还款")
            balance=conn.execute("SELECT balance_fen FROM accounts WHERE user_id=?",(uid,)).fetchone()[0]
            reserve=params.get("reserve_fen",0)
            require(type(reserve) is int and reserve>=0,"INVALID_RESERVE","保留余额必须为非负整数分")
            require(balance-amount>=reserve,"INSUFFICIENT_BALANCE","现金不足或不满足还款后保留余额")
            return "red"
        require(card["status"]!="lost","CARD_LOST","卡片已挂失，不能解挂或继续使用")
        if kind=="card_payment":
            merchant=next((m for m in MERCHANTS if m["id"]==params.get("merchant_id")),None)
            require(merchant is not None and params.get("merchant")==merchant["name"],"MERCHANT_NOT_ALLOWED","此草稿缺少有效登记商户，请拒绝后重新选择商户；不得给个人转账或购买理财")
            require(params["channel"] in merchant["channels"],"INVALID_CHANNEL","商户不支持此渠道")
            require(card["status"]=="active","CARD_FROZEN","卡片已冻结，不能支付")
            require(type(amount) is int and amount>0,"INVALID_AMOUNT","消费金额必须为正整数分")
            require(params["channel"] in ("online","offline"),"INVALID_CHANNEL","支付渠道不合法")
            require(bool(card["online_enabled"]) or params["channel"]!="online","ONLINE_DISABLED","线上交易已限制")
            require(amount<=card["single_limit_fen"] and self._card_spent(conn,uid,card["id"])+amount<=card["daily_limit_fen"],"CARD_PAYMENT_LIMIT","超过卡片单笔或当日累计支付限额")
            require(card["used_fen"]+amount<=card["credit_limit_fen"],"INSUFFICIENT_CREDIT","卡片可用额度不足")
            return "red" if amount>100000 else "yellow"
        if kind=="card_limit":
            credit,single,daily=(params[k] for k in ("credit_limit_fen","single_limit_fen","daily_limit_fen"))
            require(all(type(n) is int for n in (credit,single,daily)) and card["used_fen"]<=credit<=1000000 and 0<single<=daily<=credit,"INVALID_CARD_LIMIT","额度上限1万元，不能低于待还款；须满足单笔≤日限额≤总额度")
            return "red"
        if kind=="card_unlock":require(card["status"]=="frozen","NOT_FROZEN","只有冻结卡片可以解锁")
        if kind=="card_freeze":require(card["status"]=="active","ALREADY_FROZEN","卡片已经冻结")
        if kind=="card_online":require(type(params.get("enabled")) is bool,"INVALID_CHANNEL","开关必须为布尔值")
        return "red" if kind=="card_unlock" or (kind=="card_online" and params["enabled"]) else "yellow"

    def _public_extended(self, conn, row, params):
        if row["kind"] in WEALTH_KINDS:
            return {"product":self._product(params["product_id"])}
        if row["kind"]=="card_apply":return {"card":None}
        card=dict(self._card(conn,row["user_id"],params["card_id"]))
        return {"card":card}

    def _execute_extended(self, conn, uid, row, params):
        kind=row["kind"];aid=row["id"];result={"simulation":True,"kind":kind}
        money_direction=None;category=merchant=note=None;amount=params.get("amount_fen",0)
        if kind in WEALTH_KINDS:
            product=self._product(params["product_id"])
            result.update(product_id=product["id"],amount_fen=amount,valuation_method="principal_only_demo",profit_fen=None)
            if kind=="wealth_buy":
                available=(date.fromisoformat(self.settings.business_date)+timedelta(days=product["lock_days"])).isoformat()
                conn.execute("INSERT INTO investment_lots VALUES(?,?,?,?,?,?)",("lot_"+aid,uid,product["id"],amount,available,aid))
                money_direction="out";category="理财申购";note="模拟申购本金，不代表消费或收益";result["available_at"]=available
            else:
                remaining=amount
                lots=conn.execute("SELECT id,principal_fen FROM investment_lots WHERE user_id=? AND product_id=? AND principal_fen>0 AND available_at<=? ORDER BY available_at,id",(uid,product["id"],self.settings.business_date)).fetchall()
                for lot in lots:
                    take=min(remaining,lot["principal_fen"])
                    conn.execute("UPDATE investment_lots SET principal_fen=principal_fen-? WHERE id=?",(take,lot["id"]))
                    remaining-=take
                    if not remaining:break
                require(remaining==0,"INSUFFICIENT_HOLDINGS","可赎回持仓已变化")
                money_direction="in";category="理财赎回";note="模拟赎回本金，不是投资收益"
            merchant=product["name"]
        elif kind=="card_apply":
            cid="card_"+aid;tail=str(1000+int(uuid.uuid4().hex[:6],16)%9000)
            while conn.execute("SELECT 1 FROM cards WHERE user_id=? AND tail=?",(uid,tail)).fetchone():
                tail=str(1000+int(uuid.uuid4().hex[:6],16)%9000)
            conn.execute("INSERT INTO cards VALUES(?,?,?,?,?,?,?,?,?,?)",(cid,uid,tail,"active",500000,0,100000,200000,1,1))
            result.update(card_id=cid,status="active",credit_limit_fen=500000,message="模拟审批通过，不构成真实授信")
        else:
            cid=params["card_id"]
            if kind=="card_payment":
                conn.execute("UPDATE cards SET used_fen=used_fen+?,version=version+1 WHERE id=? AND user_id=?",(amount,cid,uid))
                # 信用卡消费记入账单，但不从现金账户重复扣减。
                txid="tx_"+uuid.uuid4().hex
                conn.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?)",(txid,uid,self.settings.stamp(),"out",amount,"信用卡消费",params["merchant"],"模拟信用卡消费，增加待还款，现金余额未变",None,aid))
                result.update(transaction_id=txid,amount_fen=amount,balance_changed=False)
            elif kind=="card_repay":
                conn.execute("UPDATE cards SET used_fen=used_fen-?,version=version+1 WHERE id=? AND user_id=?",(amount,cid,uid))
                money_direction="out";category="信用卡还款";merchant="模拟信用卡还款";note="归还信用卡本金，不重复计入消费"
            elif kind=="card_limit":
                conn.execute("UPDATE cards SET credit_limit_fen=?,single_limit_fen=?,daily_limit_fen=?,version=version+1 WHERE id=? AND user_id=?",(params["credit_limit_fen"],params["single_limit_fen"],params["daily_limit_fen"],cid,uid))
            elif kind=="card_online":conn.execute("UPDATE cards SET online_enabled=?,version=version+1 WHERE id=? AND user_id=?",(int(params["enabled"]),cid,uid))
            else:
                status={"card_freeze":"frozen","card_unlock":"active","card_lost":"lost"}[kind]
                conn.execute("UPDATE cards SET status=?,version=version+1 WHERE id=? AND user_id=?",(status,cid,uid))
            result["card"]=dict(self._card(conn,uid,cid))
        if money_direction:
            conn.execute("UPDATE accounts SET balance_fen=balance_fen+? WHERE user_id=?",(amount if money_direction=="in" else -amount,uid))
            txid="tx_"+uuid.uuid4().hex
            conn.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?)",(txid,uid,self.settings.stamp(),money_direction,amount,category,merchant,note,None,aid))
            result.update(transaction_id=txid,amount_fen=amount)
        result["balance_fen"]=conn.execute("SELECT balance_fen FROM accounts WHERE user_id=?",(uid,)).fetchone()[0]
        return result
