"""Self-authored simulation questionnaire, not Alipay's scoring formula."""
from .errors import require

QUESTIONS = [
 ('age_band','年龄区间',[('minor','未满18岁'),('adult','18至64岁'),('senior','65岁及以上')]),
 ('income_source','收入稳定程度',[('none','暂无固定收入'),('variable','收入不固定'),('stable','收入较稳定')]),
 ('emergency','现有应急金能覆盖多久必要支出',[('under1','不足1个月'),('1to3','1至3个月'),('3to6','3至6个月'),('over6','6个月以上')]),
 ('debt','每月偿债支出占收入的比例',[('high','超过50%或已有逾期'),('medium','30%至50%'),('low','不足30%'),('none','没有债务支出')]),
 ('goal','这笔资金的首要目标',[('preserve','保持流动性与本金稳定'),('income','接受小幅波动争取稳健回报'),('balanced','平衡波动与长期增长'),('growth','承受较大波动追求长期增长')]),
 ('knowledge','对金融产品风险的了解',[('none','尚不了解净值与本金损失'),('basic','了解收益不保证和本金可能损失'),('familiar','能比较风险等级与赎回限制'),('advanced','能分析波动、分散与流动性风险')]),
 ('reaction','若投资短期下跌10%，你更可能怎样处理',[('sell','难以承受，会立即全部卖出'),('reduce','降低仓位并重新评估'),('hold','核对原因后维持原计划'),('review','核对资金安排和风险后再决定是否调整')]),
 ('loss_capacity','实际财务上最多能承担多少本金损失',[('none','不能承担本金损失'),('small','不超过5%'),('medium','不超过15%'),('large','可以承担15%以上')]),
]

def questionnaire():
    return [{'id':k,'title':title,'options':[{'value':v,'label':label} for v,label in opts]} for k,title,opts in QUESTIONS]

def assess(answers, loss, experience, days):
    require(isinstance(answers,dict),'INVALID_ASSESSMENT','请完成新版风险评估')
    require(set(answers)=={k for k,_,_ in QUESTIONS}|{'monthly_essential_fen'},'INVALID_ASSESSMENT','请完整填写财务状况、投资目标及风险问题')
    score=0
    for key,_,opts in QUESTIONS:
        values=[v for v,_ in opts]
        require(answers[key] in values,'INVALID_ASSESSMENT','评估选项不合法')
        score+=values.index(answers[key])
    expense=answers['monthly_essential_fen']
    require(type(expense) is int and 0<expense<=100000000,'INVALID_ASSESSMENT','每月必要支出须为正数，且不超过100万元')
    score+= {'none':0,'limited':2,'substantial':4}[loss]+{'none':0,'some':2,'experienced':4}[experience]+(0 if days<7 else 1 if days<30 else 2 if days<365 else 3)
    category=1 if score<12 else 2 if score<18 else 3 if score<24 else 4 if score<30 else 5
    limits=[]; ceiling={'none':1,'limited':2,'substantial':3}[loss]
    if experience=='none':ceiling=1;limits.append('缺少投资经验，匹配范围限制在R1')
    elif experience=='some':ceiling=min(ceiling,2);limits.append('投资经验有限，匹配范围不超过R2')
    if days<7:ceiling=1;limits.append('近期需要用钱，优先保持流动性')
    if answers['goal']=='preserve' or answers['reaction']=='sell':ceiling=1;limits.append('投资目标或波动反应偏保守，优先低风险和流动性')
    elif answers['goal']=='income':ceiling=min(ceiling,2);limits.append('以稳健回报为目标，不匹配最高风险产品')
    if answers['emergency']=='under1' or answers['debt']=='high':ceiling=1;limits.append('应急储备不足或偿债压力较高，优先补足现金缓冲')
    if answers['knowledge']=='none' or answers['age_band']=='senior':ceiling=min(ceiling,2);limits.append('结合风险认知和年龄因素，降低匹配上限')
    if answers['loss_capacity']=='small':ceiling=min(ceiling,2);limits.append('实际损失承担能力较低，不能只依据投资意愿提高风险')
    category=min(category,{1:2,2:3,3:5}[ceiling])
    level=min(ceiling,1 if category<=2 else 2 if category==3 else 3)
    blocked=loss=='none' or answers['loss_capacity']=='none' or answers['age_band']=='minor'
    if blocked:limits.append('未成年或不能承担本金损失，当前非保本模拟产品不开放申购')
    return {'schema_version':2,'answers':answers,'score':score,'category':category,'category_name':['谨慎型','保守型','稳健型','积极型','进取型'][category-1],
            'risk_level':level,'purchase_blocked':blocked,'reasons':limits,'method':'本项目自编综合评分与约束规则，非支付宝评分公式'}
