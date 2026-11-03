"""独立读取已保存输入和逐轮结果复核，不调用规划器或生成器。"""
import hashlib
import json
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / 'reports/rolling-optimization-evaluation.json'
    payload = json.loads(path.read_text(encoding='utf8'))
    summary = payload['summary']
    protocol = summary['protocol']
    assert not set(protocol['development_days']) & set(protocol['evaluation_days'])
    assert not set(protocol['development_seeds']) & set(protocol['evaluation_seeds'])
    checked_runs = checked_steps = checked_actions = 0
    aggregates = {key: [] for key in ('static', 'periodic', 'immediate', 'stable')}
    for record in payload['scenarios']:
        stream = record['input']
        items = {row['order_id']: row for row in stream['items']}
        digest = hashlib.sha256(json.dumps(stream, sort_keys=True, ensure_ascii=False,
                                           separators=(',', ':')).encode('utf8')).hexdigest()
        assert digest == record['stream_sha256']
        assert len(record['results']) == 4
        for result in record['results']:
            assert result['stream_sha256'] == digest
            assert len(result['trace']) == stream['steps']
            actions, previous_queue = {}, []
            spent = utility = edits = mandatory = accepted = rejected = 0
            for step, trace in enumerate(result['trace']):
                assert trace['step'] == step
                assert trace['received_event_ids'] == sorted(e['event_id'] for e in stream['events'] if e['step'] == step)
                # 按发生轮次找截至本轮已收到的最新观察，独立于规划器的增量状态表。
                current = {key: dict(row, status='pending') for key, row in items.items()}
                visible = sorted((e for e in stream['events'] if e['step'] <= step),
                                 key=lambda e: (e['occurred_step'], e['event_id']))
                for event in visible:
                    current[event['order_id']].update(status=event['status'], priority_units=event['priority_units'])
                previous_valid = [key for key in previous_queue if current[key]['status'] not in {'delivered', 'canceled'}
                                  and current[key]['priority_units'] > 0]
                mandatory += len(previous_queue) - len(previous_valid)
                planned = trace['planned_ids_before_execution']
                assert len(planned) == len(set(planned))
                for key in planned:
                    assert key in items and key not in actions
                    assert current[key]['status'] not in {'delivered', 'canceled'}
                planned_cost = sum(items[key]['cost_cents'] for key in planned)
                assert planned_cost == trace['planned_cost_cents'] <= stream['budget_cents'] - spent
                if step:
                    if trace['decision'] in {'接受重规划', '队列为空补充'}:
                        accepted += 1
                        edits += len(set(previous_valid) ^ set(planned))
                    else:
                        assert planned == previous_valid
                    if trace['decision'].startswith('净增益未过门槛'):
                        rejected += 1
                action = trace['executed']
                if action:
                    key = action['order_id']
                    assert key in planned and key not in actions
                    assert action['step'] == step
                    assert action['cost_cents'] == items[key]['cost_cents']
                    assert action['priority_units'] == current[key]['priority_units']
                    actions[key] = action
                    spent += action['cost_cents']
                    utility += action['priority_units']
                    checked_actions += 1
                assert spent == trace['spent_cents'] <= stream['budget_cents']
                assert utility == trace['utility_units']
                assert trace['remaining_cents'] == stream['budget_cents'] - spent
                previous_queue = [key for key in planned if key not in actions]
                checked_steps += 1
            assert list(actions.values()) == result['actions']
            assert len(actions) == result['executed_count']
            assert spent == result['spent_cents'] and utility == result['utility_units']
            assert edits == result['plan_edit_count'] and mandatory == result['mandatory_removals']
            assert accepted == result['accepted_replans'] and rejected == result['rejected_replans']
            assert result['solver_calls'] == accepted + rejected + 1
            assert result['net_utility_units'] == utility - protocol['review_penalty_units_per_edit'] * edits
            aggregates[result['policy']].append(result)
            checked_runs += 1
    for policy, rows in aggregates.items():
        for key, value in summary['aggregate'][policy].items():
            assert abs(mean(row[key.removeprefix('mean_')] for row in rows) - value) < 1e-8
    result = {'status': 'passed', 'independent_of_planner': True,
              'scenario_count': len(payload['scenarios']), 'policy_runs_checked': checked_runs,
              'steps_checked': checked_steps, 'executions_checked': checked_actions,
              'checks': ['方案预算和累计支出', '执行当时状态和效用', '不重复执行', '不提前读取未来接收事件',
                         '方案变动计数和行政代价', '四策略相同输入指纹', '汇总指标复算', '开发和评估日期/种子分离'],
              'evaluation_file_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'limitations': ['仅验证保存场景的一致性，不证明动态全局最优或真实干预效果。']}
    (ROOT / 'reports/rolling-independent-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
