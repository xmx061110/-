"""交互查询的独立记账复核；不导入规划器、求解器或模拟生成器。"""
import hashlib
import json


def verify_comparison(stream, result):
    def require(condition, message):
        if not condition:
            raise ValueError('重排结果复核失败：' + message)

    digest = hashlib.sha256(json.dumps(stream, sort_keys=True, ensure_ascii=False,
                                       separators=(',', ':')).encode('utf8')).hexdigest()
    require(result['stream_sha256'] == digest, '输入指纹不一致')
    require({row['policy'] for row in result['results']} == {'static', 'periodic', 'immediate', 'stable'}
            and len(result['results']) == 4, '策略清单不完整')
    items = {row['order_id']: row for row in stream['items']}
    executions = rounds = 0
    for row in result['results']:
        require(row['stream_sha256'] == digest, '策略输入不同')
        require(len(row['trace']) == stream['steps'], '轮数不一致')
        spent = utility = 0
        actions = {}
        for step, trace in enumerate(row['trace']):
            require(trace['step'] == step, '轮次顺序错误')
            require(trace['received_event_ids'] == sorted(e['event_id'] for e in stream['events'] if e['step'] == step),
                    '观察事件与接收轮次不一致')
            current = {key: dict(value, status='pending') for key, value in items.items()}
            for event in sorted((e for e in stream['events'] if e['step'] <= step),
                                key=lambda e: (e['occurred_step'], e['event_id'])):
                current[event['order_id']].update(status=event['status'], priority_units=event['priority_units'])
            planned = trace['planned_ids_before_execution']
            require(len(planned) == len(set(planned)), '计划中订单重复')
            require(all(key in current and key not in actions and current[key].get('available_step', 0) <= step
                        and current[key]['status'] not in {'delivered', 'canceled', 'completed'}
                        for key in planned), '已处理或终态订单仍被选中')
            cost = sum(items[key]['cost_cents'] for key in planned)
            require(cost == trace['planned_cost_cents'] and cost <= stream['budget_cents'] - spent, '计划金额错误或超支')
            action = trace['executed']
            if action is not None:
                key = action['order_id']
                require(key in planned and key not in actions and action['step'] == step, '执行重复或未列入计划')
                require(action['cost_cents'] == items[key]['cost_cents'], '执行费用不一致')
                require(action['priority_units'] == current[key]['priority_units'], '执行效用不符合当时可见观察')
                actions[key] = action
                spent += action['cost_cents']
                utility += action['priority_units']
                executions += 1
            require(spent == trace['spent_cents'] and spent <= stream['budget_cents'], '累计支出错误')
            require(utility == trace['utility_units'], '累计效用错误')
            require(stream['budget_cents'] - spent == trace['remaining_cents'], '剩余预算错误')
            rounds += 1
        require(list(actions.values()) == row['actions'] and len(actions) == row['executed_count'], '执行清单不一致')
        require(spent == row['spent_cents'] and utility == row['utility_units'], '汇总不一致')
    return {'status': 'passed', 'policy_runs_checked': 4, 'rounds_checked': rounds,
            'executions_checked': executions, 'independent_of_planner': True,
            'scope': '预算、唯一执行、终态排除、截至当轮已接收的状态/效用及汇总记账；不证明动态全局最优'}
