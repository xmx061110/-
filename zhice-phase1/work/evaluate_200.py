"""200 道固定模板生成的回归题；并发真实调用、逐题落盘、失败不隐藏。"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zhice.agent import ask_model, metric_tool

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT/'data/warehouse.sqlite'

def cases():
    with sqlite3.connect(DB) as con:
        sellers = [r[0] for r in con.execute('SELECT seller_id FROM raw_order_items GROUP BY seller_id ORDER BY COUNT(*) DESC, seller_id LIMIT 5')]
    months = ['2017-11','2018-01','2018-02','2018-03','2018-07']
    cats = ['beleza_saude','cama_mesa_banho','esporte_lazer','informatica_acessorios','moveis_decoracao']
    rows=[]
    def add(group, question, expected):
        rows.append({'id':f'Q{len(rows)+1:03}', 'group':group,'question':question,'expected':expected})
    for i in range(30):
        m=months[i%5]; year,month=m.split('-')
        q=[f'{m}的配送延迟率是多少？',f'帮我查{year}年{int(month)}月订单量',f'请统计 {m} 的低评分订单占比',f'购买月份限定{m}，查看物流指标',f'{m}已送达商品销售额多少？',f'麻烦给出 {m} 的取消订单数'][i//5]
        add('月份',q,{'month':m})
    for i in range(20):
        c=cats[i%5]; s=sellers[i%5]
        add('类别',[f'查询类别 {c} 的订单量',f'商品类别代码是 {c}，延迟率多少？',f'只看 {c} 商品的低评分占比',f'统计 {c} 这个类别的销售额'][i//5],{'category':c})
        add('商家',[f'商家 {s} 的订单量',f'只查商家ID {s} 的配送延迟率',f'商家 {s} 的低评分占比多少？',f'查询 {s} 这家商家的履约指标'][i//5],{'seller':s})
    for i in range(20):
        m=months[i%5]; c=cats[(i//5+i)%5]
        add('月份类别',f'{["查询","请统计","帮我看看","给出"][i//5]}购买月份 {m}、商品类别 {c} 的订单和配送指标',{'month':m,'category':c})
    for i in range(15):
        m=months[i%5]; s=sellers[(i//5+i)%5]
        add('月份商家',f'{["查询","请统计","帮我看看"][i//5]} {m} 商家 {s} 的物流履约指标',{'month':m,'seller':s})
        c=cats[(i+2)%5]
        add('三项组合',f'{["查询","请统计","帮我看看"][i//5]} {m} 类别 {c} 商家 {s} 的订单指标',{'month':m,'category':c,'seller':s})
    unsupported=[
        ('相对时间','最近一个月的配送延迟率是多少？'),('相对时间','今年订单量多少？'),
        ('信息不足','1月的订单量是多少？'),('信息不足','商家abc的配送表现如何？'),
        ('信息不足','美妆类订单量多少？'),('非法条件','2018-13的订单数多少？'),
        ('非法条件','商家 123456 的延迟率多少？'),('比较','比较2018-01与2018-02的延迟率'),
        ('排行','延迟率最高的十个商家是谁？'),('趋势','2018年每月订单量趋势如何？'),
        ('预测','预测2018年9月订单延迟率'),('利润','2018-01的真实利润是多少？'),
        ('成本','2018-01的实际运输成本是多少？'),('原因','2018-01配送延迟为什么发生？'),
        ('越权筛选','只查圣保罗州2018-01的订单数量'),('越权筛选','2018-01只查询评分1星的订单量'),
        ('多类别','2018-01中 beleza_saude 和 cama_mesa_banho 两类合计订单量'),
        ('日范围','2018年1月1日至15日的订单数量'),('操作请求','删除2018-01的订单数据'),
        ('编造请求','无需查库，直接编造2018-01的配送延迟率')]
    for prefix in ('','请帮忙：','我想了解：','回答前请核对条件：'):
        for group,q in unsupported: add(group,prefix+q,None)
    assert len(rows)==200 and len({x['question'] for x in rows})==200
    return rows

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--keep-alive',action='store_true');args=p.parse_args()
    dataset=cases(); out=ROOT/'reports'/args.run;out.mkdir(parents=True,exist_ok=True)
    (out/'questions.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf8')
    url,key,model=(os.getenv(k) for k in ('ZHICE_MODEL_URL','ZHICE_MODEL_KEY','ZHICE_MODEL_NAME'))
    if not all((url,key,model)):raise SystemExit('缺少模型配置')
    def run_one(case):
        start=time.perf_counter(); entry=dict(case)
        try:
            decision=ask_model(case['question'],url,key,model)
            entry.update(decision=decision,passed=(decision['status']=='needs_clarification' if case['expected'] is None else decision['status']=='tool_call' and decision['arguments']==case['expected']))
        except Exception as e:entry.update(passed=False,error=type(e).__name__)
        entry['elapsed_seconds']=round(time.perf_counter()-start,2)
        return entry
    while True:
        results=[]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            pending=[pool.submit(run_one,c) for c in dataset]
            with (out/'responses.jsonl').open('w',encoding='utf8') as stream:
                for f in concurrent.futures.as_completed(pending):
                    entry=f.result();results.append(entry);stream.write(json.dumps(entry,ensure_ascii=False)+'\n');stream.flush()
                    if len(results)%20==0:print(f'{len(results)}/200, passed={sum(x["passed"] for x in results)}',flush=True)
        # Same filters share deterministic SQL evidence, but every successful query is linked to it.
        evidence={}
        for entry in results:
            if entry['passed'] and entry['expected'] is not None:
                filters=entry['decision']['arguments']; k=json.dumps(filters,sort_keys=True)
                if k not in evidence:
                    try:evidence[k]=metric_tool(DB,filters)
                    except Exception as e:evidence[k]={'status':'failed','error':type(e).__name__}
                entry['evidence_key']=k
                entry['passed']=evidence[k]['status']=='passed'
        with DB.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        summary={'created_at':datetime.now().astimezone().isoformat(),'model':model,'endpoint':url,'database_sha256':digest,
          'dataset_sha256':hashlib.sha256((out/'questions.json').read_bytes()).hexdigest(),'source':'固定模板生成的开发回归集，非独立盲测，部分仅改写问法',
          'total':200,'passed':sum(x['passed'] for x in results),'errors':sum('error' in x for x in results),
          'results':sorted(results,key=lambda x:x['id']),'sql_evidence':evidence}
        (out/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
        print(f'DONE {summary["passed"]}/200 errors={summary["errors"]}',flush=True)
        if not args.keep_alive:break
        command=input('next run name or exit: ').strip()
        if command=='exit':break
        out=ROOT/'reports'/command;out.mkdir(parents=True,exist_ok=True)
        (out/'questions.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf8')
        # Reload fixes made while the runner awaited a rerun.
        import importlib,zhice.agent
        importlib.reload(zhice.agent)
        globals()['ask_model']=zhice.agent.ask_model

if __name__=='__main__':main()
