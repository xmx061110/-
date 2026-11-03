"""先用开发日期选固定参数，再在不重叠日期和保留种子上比较四种策略。"""
import csv
import hashlib
import json
from pathlib import Path
import random
from statistics import mean

from zhice.budget_optimizer import simulated_candidates
from zhice.rolling_optimizer import compare, fingerprint, make_stream, run_policy

ROOT = Path(__file__).resolve().parents[1]
PARAMETERS = [
    {'min_gain_bps': 0, 'switch_penalty_units': 0, 'cooldown_steps': 1},
    {'min_gain_bps': 200, 'switch_penalty_units': 1000, 'cooldown_steps': 1},
    {'min_gain_bps': 500, 'switch_penalty_units': 1000, 'cooldown_steps': 2},
    {'min_gain_bps': 1000, 'switch_penalty_units': 5000, 'cooldown_steps': 2},
]
REVIEW_PENALTY = 1000  # 每个方案增删项的模拟行政代价，效用单位而非金额。


def summarize(rows):
    return {name: {'mean_' + key: mean(r[name][key] for r in rows)
                   for key in ('utility_units', 'spent_cents', 'executed_count', 'solver_calls',
                               'accepted_replans', 'rejected_replans', 'plan_edit_count', 'net_utility_units')}
            for name in ('static', 'periodic', 'immediate', 'stable')}


def main():
    source = ROOT / 'models/historical-risk-test.csv'
    with source.open(encoding='utf-8-sig', newline='') as f:
        days = sorted({row['order_purchase_timestamp'][:10] for row in csv.DictReader(f)})
    development_days = days[:8]
    evaluation_days = days[8:][::2][:20]
    if len(evaluation_days) != 20:
        raise ValueError('保留评估日期不足20天')
    protocol = {'development_days': development_days, 'development_seeds': [0, 1],
                'evaluation_days': evaluation_days, 'evaluation_seeds': [100, 101, 102, 103, 104],
                'budgets_cents': [10000, 20000], 'candidate_limit': 40, 'steps': 12,
                'execution_capacity_per_step': 1, 'periodic_interval': 3,
                'candidate_parameters': PARAMETERS, 'review_penalty_units_per_edit': REVIEW_PENALTY,
                'selection_rule': '仅按开发集平均(执行时累计效用-1000×方案增删项数)选参数；并列按候选序号',
                'comparison_rule': '相同日期/种子/预算产生同一外生事件流，四策略执行容量和执行排序规则相同',
                'heldout_scope': '仅日期与模拟种子保留；历史风险数据已用于此前实验，不是新的盲测或真实干预评估'}
    (ROOT / 'reports/rolling-protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding='utf8')
    development = [[] for _ in PARAMETERS]
    for day in development_days:
        for seed in protocol['development_seeds']:
            items = simulated_candidates(source, day, seed, 40)
            for budget in protocol['budgets_cents']:
                stream = make_stream(items, budget, seed)
                for index, params in enumerate(PARAMETERS):
                    result = run_policy(stream, 'stable', **params)
                    development[index].append(result['utility_units'] - REVIEW_PENALTY * result['plan_edit_count'])
    means = [mean(values) for values in development]
    best = max(range(len(PARAMETERS)), key=lambda index: (means[index], -index))
    selected = {'parameters': PARAMETERS[best], 'development_scores': means, 'selected_index': best,
                'protocol_sha256': fingerprint(protocol), 'development_scenarios': len(development[0]),
                'selection_source': '开发日期和开发模拟种子；评估阶段不再改参数'}
    # 在读取/计算保留日期评估结果之前，冻结并写入选择结果。
    (ROOT / 'models/rolling-policy.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding='utf8')
    print('开发参数已冻结：' + json.dumps(selected, ensure_ascii=False), flush=True)
    records, pairs, day_differences = [], [], []
    for day in evaluation_days:
        local_differences = []
        for seed in protocol['evaluation_seeds']:
            items = simulated_candidates(source, day, seed, 40)
            for budget in protocol['budgets_cents']:
                stream = make_stream(items, budget, seed)
                result = compare(stream, PARAMETERS[best])
                paired = {}
                for row in result['results']:
                    assert row['stream_sha256'] == result['stream_sha256']
                    assert sum(x['cost_cents'] for x in row['actions']) == row['spent_cents'] <= budget
                    assert len({x['order_id'] for x in row['actions']}) == row['executed_count']
                    row['net_utility_units'] = row['utility_units'] - REVIEW_PENALTY * row['plan_edit_count']
                    paired[row['policy']] = row
                pairs.append(paired)
                local_differences.append(paired['stable']['net_utility_units'] - paired['immediate']['net_utility_units'])
                records.append({'day': day, 'seed': seed, 'budget_cents': budget, 'input': stream, **result})
        day_differences.append(mean(local_differences))
    stats = summarize(pairs)
    rng = random.Random(20261101)
    boot = sorted(mean(rng.choices(day_differences, k=len(day_differences))) for _ in range(2000))
    summary = {'scenario_count': len(records), 'policy_runs': len(records) * 4,
               'all_budget_checks_passed': True, 'all_action_uniqueness_checks_passed': True,
               'selected_parameters': selected, 'aggregate': stats,
               'stable_vs_immediate_net': {
                   'wins': sum(x['stable']['net_utility_units'] > x['immediate']['net_utility_units'] for x in pairs),
                   'ties': sum(x['stable']['net_utility_units'] == x['immediate']['net_utility_units'] for x in pairs),
                   'losses': sum(x['stable']['net_utility_units'] < x['immediate']['net_utility_units'] for x in pairs),
                   'mean_paired_difference_units': mean(day_differences),
                   'day_cluster_bootstrap95_units': [boot[49], boot[1949]],
                   'meaning': '仅为当前模拟事件分布和假设切换代价下的比较，区间不代表真实业务收益'},
               'protocol': protocol, 'input_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
               'limitations': ['费用、状态变化、风险变化及方案切换代价是模拟参数，不是实际干预数据。',
                               '累计效用不是减少延迟单量；当前评分模型AUC约0.571且未校准。',
                               '单轮精确背包不等于整个动态时间段的全局最优；未来状态不提供给策略。',
                               '日期分组自助区间仅描述模拟实验差异；不能外推至真实物流。']}
    summary_path = ROOT / 'reports/rolling-optimization-summary.json'
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    full = {'summary': summary, 'scenarios': records}
    (ROOT / 'reports/rolling-optimization-evaluation.json').write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding='utf8')
    # 固定使用第一例供演示，不按改善程度挑选。
    (ROOT / 'reports/rolling-optimization-example.json').write_text(json.dumps(records[0], ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({k: v for k, v in summary.items() if k not in {'protocol', 'limitations'}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
