"""历史商家配送延迟排查清单：两套 SQL 核对后生成待人工调查的优先级。"""
import argparse
import json
from contextlib import closing
from pathlib import Path
from .agent import ROOT, metric_tool
from .demo_validation import open_readonly, reference_query, compare_results, TIE_AUDIT_SQL, render_sql
from .metrics import metric_query


def build_triage(database, month, category=None, min_orders=30, limit=10):
    if not isinstance(min_orders,int) or isinstance(min_orders,bool) or min_orders<1:
        raise ValueError('最小样本量须为正整数')
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=100:
        raise ValueError('清单长度须为1到100')
    filters={'month':month}
    if category is not None:filters['category']=category
    scope=metric_tool(database,filters)
    a,ap=metric_query(month=month,category=category,group_by='seller')
    b,bp=reference_query(month=month,category=category,group_by='seller')
    with closing(open_readonly(database)) as con:
        if con.execute(TIE_AUDIT_SQL).fetchone()[0]:raise RuntimeError('评分冲突，停止输出')
        primary={r['group_value']:dict(r) for r in con.execute(a,ap)}
        raw={r['group_value']:dict(r) for r in con.execute(b,bp)}
        if primary.keys()!=raw.keys():raise RuntimeError('商家集合复核失败')
        if any(not all(x['passed'] for x in compare_results(primary[k],raw[k])) for k in primary):
            raise RuntimeError('商家指标复核失败')
        sql={'program':render_sql(con,a,ap),'reference':render_sql(con,b,bp)}
    eligible=[r for r in primary.values() if r['delivery_eligible_order_count']>=min_orders and r['late_order_count']>0]
    eligible.sort(key=lambda r:(-r['late_order_count'],-r['delivery_late_rate'],r['group_value']))
    priority=[]
    for rank,r in enumerate(eligible[:limit],1):
        priority.append({'rank':rank,'seller':r['group_value'],'metrics':r,
            'action':'优先核对承诺送达日、交运记录和配送异常记录，联系商家确认后再决定是否干预。',
            'reason':f'当前筛选内观察到{r["late_order_count"]}笔配送延迟，分母{r["delivery_eligible_order_count"]}笔。',
            'causal_claim':False})
    return {'status':'passed','filters':scope['filters'],'min_eligible_orders':min_orders,
        'selection_rule':'按历史延迟订单数降序、延迟率降序、商家ID升序；门槛按有效配送分母计算',
        'scope_metrics':scope['program'],'checked_sellers':len(primary),'eligible_sellers':len(eligible),
        'checks_passed':len(primary)*21,'priority':priority,'sql':sql,
        'limitations':['商家关联的是订单级配送结果；多商家订单不能据此认定某商家负责。',
                       '各商家订单数不可直接相加作为总订单量。','这是历史排查优先级，不是实时预警、因果结论或收益测算。']}


def main():
    p=argparse.ArgumentParser();p.add_argument('--month',required=True);p.add_argument('--category')
    p.add_argument('--min-orders',type=int,default=30);p.add_argument('--limit',type=int,default=10)
    p.add_argument('--out',type=Path,default=ROOT/'reports'/'历史配送延迟排查清单.json');args=p.parse_args()
    result=build_triage(ROOT/'data/warehouse.sqlite',args.month,args.category,args.min_orders,args.limit)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(f'已核对 {result["checked_sellers"]} 家商家，生成 {len(result["priority"])} 条排查建议：{args.out}')

if __name__=='__main__':main()
