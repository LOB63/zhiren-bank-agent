"""独立情境环境中的合成用户；普通演示数据库不自动注入这些用户。"""
from calendar import monthrange
from datetime import date, timedelta
import random

from .db import encode,password_hash

SCENARIOS={
    'salary':{'name':'陈晨','label':'固定工资 · 陈晨','balance_fen':4200000,
              'income_pattern':'每月有固定工资记录，奖金单列；未来到账仍需核实。',
              'reserve_target_fen':800000,'reserve_reason':'本人计划保留8000元日常周转资金。',
              'outlay_title':'房租','outlay_fen':260000,'outlay_days':5},
    'freelance':{'name':'许宁','label':'收入不固定 · 许宁','balance_fen':2300000,
                 'income_pattern':'项目款按阶段结算，历史上存在零收入月份；没有保证的下月收入。',
                 'reserve_target_fen':900000,'reserve_reason':'本人计划保留9000元覆盖项目回款间隔。',
                 'outlay_title':'工作室租金','outlay_fen':180000,'outlay_days':8},
    'upcoming':{'name':'郑悦','label':'近期大额支出 · 郑悦','balance_fen':1600000,
                'income_pattern':'历史工资基本固定，近期有已登记的大额用款需求。',
                'reserve_target_fen':1300000,'reserve_reason':'本人计划保留13000元，用于12000元课程费用及1000元缓冲。',
                'outlay_title':'课程费用','outlay_fen':1200000,'outlay_days':7},
}


def scenario_accounts():
    return [{'username':key,'name':value['label']} for key,value in SCENARIOS.items()]


def seed_scenario_users(database):
    with database.connect(write=True) as conn:
        for key,profile in SCENARIOS.items():
            if conn.execute('SELECT 1 FROM users WHERE id=?',(key,)).fetchone():
                continue
            conn.execute('INSERT INTO users VALUES(?,?,?,?)',(key,profile['name'],key,password_hash('demo2026',key)))
            seed_scenario_ledger(database,conn,key)


def seed_scenario_ledger(database,conn,uid):
    profile=SCENARIOS[uid]
    today=date.fromisoformat(database.settings.business_date)
    rng=random.Random({'salary':101,'freelance':202,'upcoming':303}[uid])
    entries=[]
    current=today.year*12+today.month-1
    for offset,month_index in enumerate(range(current-12,current)):
        year,month0=divmod(month_index,12);month=month0+1
        def add(day,direction,amount,category,merchant,note='',recipient=None):
            day=min(day,monthrange(year,month)[1])
            entries.append((f'scenario_{uid}_{month_index}_{len(entries)}',uid,
                            f'{year:04d}-{month:02d}-{day:02d}T12:00:00+08:00',direction,amount,
                            category,merchant,note,recipient,None))
        income=([350000,0,1100000,450000,1600000,0,600000,280000,1450000,500000,0,980000][offset]
                if uid=='freelance' else 1100000 if uid=='salary' else 800000)
        if income:add(8 if uid=='freelance' else 3,'in',income,'收入','项目结算' if uid=='freelance' else '模拟雇主','已入账收入')
        if uid=='salary' and offset in (5,11):add(15,'in',200000,'收入','模拟雇主奖金','奖金与固定工资分开')
        add(5,'out',180000 if uid=='freelance' else 260000,'住房','工作室租金' if uid=='freelance' else '房租收款','周期付款，无商户取消接口')
        for sid,merchant,amount,day in [('video','星光视频',2500,6),('music','悦耳音乐',1500,9),('cloud','云盘空间',1200,18)]:
            add(day,'out',amount,'订阅',merchant,'已登记月度订阅')
        add(20,'out',5900,'通讯','手机话费','周期付款，不等于订阅目录中的可取消项目')
        for i in range(24):
            category=['餐饮','购物','交通','餐饮','教育','日用'][i%6]
            add(2+i,'out',rng.randint(800,8500),category,f'{category}商户{i%7+1}',f'合成日常记录{i+1}')
        add(11,'out',20000,'转账','李明','日常分摊',uid+'_li1')
        if offset==11:
            add(26,'out',19900,'购物','数码商店','同日同额，是否重复需核对')
            add(26,'out',19900,'购物','数码商店','第二条独立账本记录')
    # 期初余额是历史区间前的存量资金，不冒充本期工资收入。
    net=sum(row[4] if row[3]=='in' else -row[4] for row in entries)
    opening=profile['balance_fen']-net
    # 固定工资用户历史结余较高，期初存量可能不足，明确记入期初存量调整而非伪造工资。
    # 现金按每个历史时点核对：期初为非负且整个历史区间不透支。
    minimum=0;running=0
    for row in sorted(entries,key=lambda r:(r[2],r[0])):
        running+=row[4] if row[3]=='in' else -row[4]
        minimum=min(minimum,running)
    if opening<max(0,-minimum):
        # 长期高收入的存量资金已用于区间外支出；增加一笔明确的历史资金划出保持对账。
        extra=max(0,-minimum)-opening
        opening+=extra
        entries.append((f'scenario_{uid}_historical_outflow',uid,
                        (today-timedelta(days=1)).isoformat()+'T12:00:00+08:00','out',extra,
                        '转账','历史资金划出','合成账本期末资金调配，非本次Agent执行',None,None))
    conn.execute('INSERT INTO accounts VALUES(?,?)',(uid,profile['balance_fen']))
    conn.executemany('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?)',entries)
    recipients=[('li1','李明','1234','模拟一行','6210000000001101'),
                ('li2','李明','5678','模拟二行','6210000000002202'),
                ('wang','王芳','1234','模拟三行','6210000000003303'),
                ('chen1','陈晨','9988','模拟一行','6210000000007788'),
                ('chen2','陈晨','9988','模拟二行','6210000000006677')]
    for key,name,tail,bank,account in recipients:
        rid=uid+'_'+key
        conn.execute('INSERT INTO recipients VALUES(?,?,?,?)',(rid,uid,name,tail))
        conn.execute('INSERT INTO recipient_details VALUES(?,?,?,?)',(rid,uid,bank,account))
    for sid,merchant,amount,days in [('video','星光视频',2500,2),('music','悦耳音乐',1500,5),('cloud','云盘空间',1200,15)]:
        conn.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?,?,?)',
                     (uid+'_'+sid,uid,merchant,amount,'monthly',(today+timedelta(days=days)).isoformat(),'active',1))
    cards=[(uid+'_card',uid,'6688','active',500000,180000 if uid=='salary' else 60000,100000,200000,1,1)]
    if uid=='freelance':cards.append((uid+'_card2',uid,'5522','frozen',300000,120000,50000,100000,0,1))
    if uid=='upcoming':cards=[(uid+'_card',uid,'6688','lost',500000,0,100000,200000,0,1)]
    conn.executemany('INSERT INTO cards VALUES(?,?,?,?,?,?,?,?,?,?)',cards)
    lots=[('demo_cash',200000,-1),('demo_balanced',100000,7)] if uid=='salary' else [('demo_cash',60000,-1),('demo_balanced',80000,4)] if uid=='freelance' else [('demo_growth',200000,20)]
    opening_lots=[]
    for index,(pid,amount,days) in enumerate(lots):
        available=(today+timedelta(days=days)).isoformat();aid=f'opening_{uid}_{index}'
        conn.execute('INSERT INTO investment_lots VALUES(?,?,?,?,?,?)',('lot_'+aid,uid,pid,amount,available,aid))
        opening_lots.append({'product_id':pid,'principal_fen':amount,'available_at':available,'source':'期初持仓快照'})
    context={'scenario':uid,'title':profile['label'],'recorded_at':today.isoformat(),
             'income_pattern':profile['income_pattern'],'reserve_target_fen':profile['reserve_target_fen'],
             'reserve_reason':profile['reserve_reason'],'opening_cash_fen':opening,
             'opening_holdings':opening_lots,
             'opening_card_debts':[{'card_tail':c[2],'used_fen':c[5],'source':'期初负债快照'} for c in cards],
             'upcoming_outlays':[{'title':profile['outlay_title'],'amount_fen':profile['outlay_fen'],
                                  'due_date':(today+timedelta(days=profile['outlay_days'])).isoformat(),
                                  'source':'模拟用户登记','payment_status':'unverified'}],
             'source':'synthetic_user_statement','simulation':True}
    conn.execute('INSERT INTO customer_contexts VALUES(?,?)',(uid,encode(context)))
