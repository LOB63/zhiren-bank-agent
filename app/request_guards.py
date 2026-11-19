"""依据可信用户消息检查模型提出的资金用途和收款人；消息由Agent传入，不由工具参数决定。"""
import re
from .errors import require


def guard_request(bank,uid,name,args,user_texts):
    if user_texts is None:
        return  # 手工业务验证使用明确的对象ID，不冒充自然语言授权。
    latest=user_texts[-1] if user_texts else ""
    if name.startswith('prepare_') or name=='revise_transfer':
        # 尚未指明要取消哪几项时，不能先办理同一请求中的后续转账。
        merchants={s['merchant'] for s in bank.subscriptions(uid)}
        required=0
        for text in user_texts:
            selection=re.search(r'取消[^。；]{0,12}(?:选中|选择|选好)(?:的)?(?:(两|二|2|三|3|一|1)项)?',text)
            if selection:
                required={'两':2,'二':2,'2':2,'三':3,'3':3,'一':1,'1':1}.get(selection.group(1),1)
            if required and (re.search(r'选择|选好|我选|取消|就这|这两|这三',text)):
                if sum(m in text for m in merchants)>=required:required=0
            if re.search(r'不(?:再)?取消|先不(?:用)?取消|订阅先不管',text):required=0
        require(not required,'SELECTION_REQUIRED','请先让用户明确选择要取消的订阅；选择前不能先办后续操作')
    if name in ("prepare_transfer","prepare_wealth_buy"):
        # 补充尾号/金额不改变已指定的资金来源；只有用户明确改用现金才能解除限制。
        for text in reversed(user_texts):
            negative=r'(?:不要|不能|不准|不许|禁止|不是|并非|不再|勿|别|不)\s*$'
            cash_matches=re.finditer(r"(?:改用|使用|用|从)(?:我的|本人)?(?:账户余额|储蓄|现金账户|存款|现金|余额)",text)
            cash_positions=[m.start() for m in cash_matches if not re.search(negative,text[:m.start()])]
            credit_matches=re.finditer(r"(?:改用|使用|从|用|拿|刷)[^。；，,]{0,12}信用卡|信用卡(?:里面|里|中|的额度|资金|额度|余额)?(?:的)?(?:转账|转给|拿|取现|提现|套现|购买理财|买理财|申购|投资)",text)
            credit_positions=[m.start() for m in credit_matches if not re.search(negative,text[:m.start()])]
            if cash_positions or credit_positions:
                require(max(cash_positions,default=-1)>max(credit_positions,default=-1),"CREDIT_SOURCE_UNSUPPORTED","本系统不支持信用卡转账、取现或购买理财；此前指定的资金来源仍有效，不能自动改用现金，请用户明确资金来源")
                break
    if name=="prepare_card_payment":
        merchants=bank.cards(uid)["merchants"]
        merchant=next((m for m in merchants if m["id"]==args["merchant_id"]),None)
        require(merchant is not None,"MERCHANT_NOT_ALLOWED","商户未登记，不允许刷卡")
        require(merchant["name"] in latest or merchant["id"] in latest,"MERCHANT_NOT_AUTHORIZED","用户尚未指定该登记商户，请追问；不能把个人转账换成商户消费")
        require(not re.search(r"套现|转给|拿给|借给|给.+(?:转账|转钱)|(?:购买|买|申购).*(?:理财|基金|股票)",latest),
                "CARD_PURPOSE_DENIED","不能用模拟商户消费包装个人转账、套现或投资")
    if name not in ("prepare_transfer","revise_transfer"):
        return
    recipients=bank.recipients(uid)
    chosen=next((r for r in recipients if r["id"]==args["recipient_id"]),None)
    require(chosen is not None,"NOT_FOUND","收款人不存在",404)
    for text in reversed(user_texts):
        account_tails=re.findall(r"(?:收款账号|银行账号|账号|银行卡)(?:的)?尾号\s*[:：]?\s*(\d{4})(?!\d)",text)
        explicit=[r for r in recipients if r.get('account_tail') in account_tails] if account_tails else [r for r in recipients if r["id"] in text or re.search(r"(?:尾号|末四位|后四位)\s*[:：]?\s*"+re.escape(r["phone_tail"])+r"(?!\d)",text)
                  or re.fullmatch(r"\s*"+re.escape(r["phone_tail"])+r"(?:那个|的)?[。.!！]?\s*",text)]
        specified_tails=re.findall(r'(?:尾号|末四位|后四位)\s*[:：]?\s*(\d{4})(?!\d)',text)
        require(not specified_tails or bool(explicit),'RECIPIENT_MISMATCH','指定的尾号未匹配到已保存收款人，请核对后再办理')
        named=[r for r in recipients if r["name"] in text]
        if explicit:
            if named:
                names={r['name'] for r in named}
                explicit=[r for r in explicit if r['name'] in names]
                require(bool(explicit),'RECIPIENT_MISMATCH','姓名与指定尾号不匹配')
            ids={r["id"] for r in explicit}
            require(len(ids)==1 and chosen["id"] in ids,"RECIPIENT_AMBIGUOUS","收款人信息不唯一或与用户指定不符，请用户明确姓名和手机号尾号")
            if named:require(chosen["name"] in {r["name"] for r in named},"RECIPIENT_MISMATCH","姓名与手机号尾号不匹配")
            return
        if named:
            matches=[r for r in named if r["name"]==chosen["name"]]
            require(len(matches)==1,"RECIPIENT_AMBIGUOUS","存在同名收款人，请用户指定手机号尾号，模型不能代选")
            return
    require(False,"RECIPIENT_AMBIGUOUS","用户尚未明确收款人，请追问姓名或手机号尾号")
