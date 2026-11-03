"""整数分0-1背包：压缩金额单位并剪去被支配状态，输出可复算证据。"""
import csv
import argparse
import hashlib
import math
import time
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from datetime import date
from pathlib import Path
import json


def validate(items,budget):
    if type(budget) is not int or budget<0:raise ValueError('预算必须为非负整数分')
    if len(items)>500:raise ValueError('本原型每次最多处理500笔候选订单')
    seen=set()
    for x in items:
        if not isinstance(x.get('order_id'),str) or not x['order_id'] or x['order_id'] in seen:raise ValueError('订单ID为空或重复')
        seen.add(x['order_id'])
        if type(x.get('cost_cents')) is not int or x['cost_cents']<=0:raise ValueError('成本须为正整数分')
        if type(x.get('priority_units')) is not int or x['priority_units']<0:raise ValueError('目标值须为非负整数')


def solve(items,budget,prune=True):
    validate(items,budget)
    start=time.perf_counter()
    usable=sorted((x for x in items if x['cost_cents']<=budget and x['priority_units']>0),key=lambda x:x['order_id'])
    unit=math.gcd(*(x['cost_cents'] for x in usable)) if usable else 1
    capacity=budget//unit
    if capacity>100000:raise ValueError('压缩后的预算过大，请缩小候选范围或预算')
    frontier={0:(0,())};visited=0;peak=1
    for x in usable:
        cost=x['cost_cents']//unit;value=x['priority_units'];new=dict(frontier)
        for spent,(score,ids) in frontier.items():
            visited+=1;next_spent=spent+cost
            if next_spent>capacity:continue
            candidate=(score+value,ids+(x['order_id'],))
            old=new.get(next_spent)
            if old is None or candidate[0]>old[0] or (candidate[0]==old[0] and candidate[1]<old[1]):new[next_spent]=candidate
        if prune:
            best=-1;frontier={}
            for spent,pair in sorted(new.items()):
                if pair[0]>best:frontier[spent]=pair;best=pair[0]
        else:frontier=new
        peak=max(peak,len(frontier))
    spent,(score,ids)=min(frontier.items(),key=lambda p:(-p[1][0],p[0],p[1][1]))
    return {'algorithm':'gcd_pareto_dp' if prune else 'gcd_unpruned_dp','selected_ids':list(ids),
       'cost_cents':spent*unit,'budget_cents':budget,'unused_cents':budget-spent*unit,'priority_units':score,
       'candidate_count':len(items),'eligible_candidate_count':len(usable),'unit_cents':unit,
       'states_visited':visited,'peak_states':peak,'elapsed_ms':round(1000*(time.perf_counter()-start),3),
       'optimality':'对传入的整数目标、候选集和预算约束精确最优；不代表实际经营最优'}


def greedy(items,budget,method):
    validate(items,budget)
    if method=='risk':key=lambda x:(-x['risk_score'],x['order_id'])
    elif method=='density':key=lambda x:(-Fraction(x['priority_units'],x['cost_cents']),x['order_id'])
    else:raise ValueError('未知基线')
    selected=[];spent=score=0
    for x in sorted(items,key=key):
        if spent+x['cost_cents']<=budget:
            selected.append(x['order_id']);spent+=x['cost_cents'];score+=x['priority_units']
    return {'algorithm':method,'selected_ids':selected,'cost_cents':spent,'budget_cents':budget,'priority_units':score}


def simulated_candidates(path,day,seed=0,limit=100):
    if type(limit) is not int or not 1<=limit<=500:raise ValueError('候选数量不合法')
    with open(path,encoding='utf-8-sig',newline='') as f:
        rows=[r for r in csv.DictReader(f) if r['order_purchase_timestamp'].startswith(day)]
    # 只按下单时间和ID截取队列，不读取delivery_late或实际送达时间。
    rows.sort(key=lambda r:(r['order_purchase_timestamp'],r['order_id']))
    items=[]
    for r in rows[:limit]:
        h=hashlib.sha256(f'{seed}:{r["order_id"]}'.encode()).digest()
        cost=(1+int.from_bytes(h[:2],'big')%30)*100
        effect=Decimal([20,50,80][h[2]%3])/100
        risk=Decimal(r['predicted_late_probability'])
        if not risk.is_finite() or not 0<=risk<=1:raise ValueError('风险评分非法')
        priority=int((risk*effect*1000000).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        items.append({'order_id':r['order_id'],'risk_score':float(risk),'cost_cents':cost,
                     'assumed_effect':str(effect),'priority_units':priority})
    return items


def scenario(path,day,budget_cents,seed=0,limit=100):
    if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:raise ValueError('日期须为YYYY-MM-DD')
    if type(seed) is not int or not 0<=seed<=9999:raise ValueError('模拟种子须为0到9999的整数')
    items=simulated_candidates(path,day,seed,limit)
    result=solve(items,budget_cents)
    by_id={x['order_id']:x for x in items}
    assert sum(by_id[k]['cost_cents'] for k in result['selected_ids'])==result['cost_cents']<=budget_cents
    assert sum(by_id[k]['priority_units'] for k in result['selected_ids'])==result['priority_units']
    return {'mode':'历史评分+模拟成本和干预系数的离线情景','day':day,'seed':seed,'candidate_rule':'当日历史测试订单按下单时间与ID排序取前N笔；非实时队列',
       'cost_rule':'SHA256(seed:order_id)前2字节映射1到30 BRL；模拟处理费用，不是运费',
       'effect_rule':'第3字节映射0.2、0.5、0.8；未从真实干预估计',
       'objective':'最大化sum(未校准风险评分×假设干预系数×1000000，逐单四舍五入)；仅为优先级效用，不是预计减少延迟单量',
       'candidates':items,'optimized':result,'baselines':[greedy(items,budget_cents,m) for m in ('risk','density')],
       'limitations':['模型评分未经概率校准且测试AUC约0.571。','真实加急成本、处理能力与干预效果缺失。','仅在已送达且时间有效的历史测试样本中验证；不代表真实业务可行性。']}


def main():
    root=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser();p.add_argument('--day',default='2018-07-01');p.add_argument('--budget-cents',type=int,default=30000)
    p.add_argument('--seed',type=int,default=0);p.add_argument('--limit',type=int,default=100)
    p.add_argument('--out',type=Path,default=root/'reports/预算分配交互结果.json');a=p.parse_args()
    result=scenario(root/'models/historical-risk-test.csv',a.day,a.budget_cents,a.seed,a.limit)
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result['optimized'],ensure_ascii=False,indent=2));print('完整证据:',a.out)

if __name__=='__main__':main()
