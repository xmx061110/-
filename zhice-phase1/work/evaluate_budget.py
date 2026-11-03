import csv
import hashlib
import json
from pathlib import Path
from statistics import mean
from zhice.budget_optimizer import scenario,solve

ROOT=Path(__file__).resolve().parents[1]
path=ROOT/'models/historical-risk-test.csv'
with path.open(encoding='utf-8-sig',newline='') as f:days=sorted({r['order_purchase_timestamp'][:10] for r in csv.DictReader(f)})
# 预先固定20天，跨越两个月；3个模拟种子、3个预算，共180个场景。
days=days[::3][:20]
reports=[]
for day in days:
    for seed in (0,1,2):
        for budget in (10000,30000,50000):
            r=scenario(path,day,budget,seed,100)
            plain=solve(r['candidates'],budget,prune=False)
            assert plain['priority_units']==r['optimized']['priority_units']
            assert all(x['priority_units']<=r['optimized']['priority_units'] for x in r['baselines'])
            r['unpruned']=plain;reports.append(r)
with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
summary={'scenario_count':len(reports),'input_sha256':digest,'days':days,'candidate_limit':100,
    'budget_values_cents':[10000,30000,50000],'simulation_seeds':[0,1,2],
    'feasible_count':sum(r['optimized']['cost_cents']<=r['optimized']['budget_cents'] for r in reports),
    'exact_matches_unpruned':len(reports),
    'strictly_better_than_risk':sum(r['optimized']['priority_units']>r['baselines'][0]['priority_units'] for r in reports),
    'strictly_better_than_density':sum(r['optimized']['priority_units']>r['baselines'][1]['priority_units'] for r in reports),
    'mean_priority_gain_vs_risk':mean((r['optimized']['priority_units']/r['baselines'][0]['priority_units']-1) for r in reports if r['baselines'][0]['priority_units']),
    'mean_priority_gain_vs_density':mean((r['optimized']['priority_units']/r['baselines'][1]['priority_units']-1) for r in reports if r['baselines'][1]['priority_units']),
    'pruned_states':sum(r['optimized']['states_visited'] for r in reports),'unpruned_states':sum(r['unpruned']['states_visited'] for r in reports),
    'mean_ms_pruned':mean(r['optimized']['elapsed_ms'] for r in reports),'mean_ms_unpruned':mean(r['unpruned']['elapsed_ms'] for r in reports),
    'meaning':'同一固定模拟成本和干预系数下的目标函数比较；不是实际减少延误或节省费用',
    'scenarios':reports}
(ROOT/'reports/budget-optimization-evaluation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
(ROOT/'reports/预算分配示例.json').write_text(json.dumps(reports[0],ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in summary.items() if k!='scenarios'},ensure_ascii=False,indent=2))
