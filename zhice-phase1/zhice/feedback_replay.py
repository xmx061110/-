"""按接收时间回放物流反馈并滚动重排预算；不连接真实物流接口。"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from .budget_optimizer import solve

ACTIVE = {'pending', 'picked_up', 'in_transit', 'exception'}
TERMINAL = {'delivered', 'canceled'}


def instant(value):
    if not isinstance(value, str):
        raise ValueError('事件时间须为包含时区的ISO时间')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('事件时间格式不合法') from exc
    if result.tzinfo is None:
        raise ValueError('事件时间必须包含时区')
    return result.astimezone(timezone.utc)


def replay(items, total_budget_cents, events, as_of):
    """items是回放起点快照；events必须是该快照之后的事件。"""
    solve(items, total_budget_cents)  # 统一校验金额、效用和唯一订单ID。
    cutoff = instant(as_of)
    state = {x['order_id']: dict(deepcopy(x), status='pending') for x in items}
    committed, outcomes, last_observed, seen = {}, {}, {}, {}
    accepted, ignored, trace = [], [], []

    def plan():
        spent = sum(committed.values())
        candidates = [x for key, x in state.items()
                      if x['status'] in ACTIVE and key not in committed]
        result = solve(candidates, total_budget_cents - spent)
        return {'spent_cents': spent, 'remaining_cents': total_budget_cents - spent,
                'planned_cents': result['cost_cents'],
                'selected_ids': result['selected_ids'],
                'priority_units': result['priority_units']}

    initial = plan()
    ordered = []
    for e in events:
        if not isinstance(e, dict) or not isinstance(e.get('event_id'), str) or not e['event_id']:
            raise ValueError('事件ID不合法')
        received = instant(e.get('received_at'))
        if received > cutoff:
            ignored.append({'event_id': e['event_id'], 'reason': '尚未接收'})
            continue
        occurred = instant(e.get('occurred_at'))
        if occurred > received:
            raise ValueError('发生时间不能晚于接收时间')
        ordered.append((received, e['event_id'], occurred, e))
    for received, event_id, occurred, e in sorted(ordered, key=lambda row: row[:2]):
        encoded = json.dumps(e, sort_keys=True, ensure_ascii=False)
        if event_id in seen:
            if seen[event_id] != encoded:
                raise ValueError('相同事件ID包含冲突内容')
            ignored.append({'event_id': event_id, 'reason': '重复事件'})
            continue
        seen[event_id] = encoded
        key, kind = e.get('order_id'), e.get('type')
        if not isinstance(key, str) or key not in state:
            raise ValueError('事件订单不在起点快照中')
        common = {'event_id', 'order_id', 'type', 'occurred_at', 'received_at'}
        allowed = {'observation': {'status', 'priority_units'}, 'commit': set(),
                   'action_result': {'outcome'}}
        if not isinstance(kind, str) or kind not in allowed or set(e) - common - allowed[kind]:
            raise ValueError('事件类型或字段不合法')
        if kind == 'observation':
            if occurred <= last_observed.get(key, datetime.min.replace(tzinfo=timezone.utc)):
                ignored.append({'event_id': event_id, 'reason': '过期或同时间观察，不覆盖较新状态'})
                continue
            status = e.get('status')
            priority = e.get('priority_units', state[key]['priority_units'])
            if not isinstance(status, str) or status not in ACTIVE | TERMINAL or type(priority) is not int or priority < 0:
                raise ValueError('观察状态或优先级效用不合法')
            if state[key]['status'] in TERMINAL and status != state[key]['status']:
                raise ValueError('终态订单不可直接重新激活')
            state[key].update(status=status, priority_units=priority)
            last_observed[key] = occurred
        elif kind == 'commit':
            if key in committed or key not in plan()['selected_ids']:
                raise ValueError('只能执行当前方案中尚未执行的订单')
            committed[key] = state[key]['cost_cents']
        else:
            if key not in committed or e.get('outcome') not in {'success', 'failure'} or key in outcomes:
                raise ValueError('执行反馈须对应已执行且尚无结果的行动')
            outcomes[key] = e['outcome']  # 失败不自动退款，不自动重复执行。
        accepted.append(event_id)
        trace.append({'event_id': event_id, 'type': kind, 'order_id': key,
                      'received_at': received.isoformat(), **plan()})
    final = plan()
    assert final['spent_cents'] + final['planned_cents'] <= total_budget_cents
    return {'mode': '事件回放原型，非真实物流接入', 'as_of': cutoff.isoformat(),
            'budget_cents': total_budget_cents, 'initial_plan': initial,
            'final_plan': final, 'accepted_event_ids': accepted, 'ignored_events': ignored,
            'committed_costs_cents': committed, 'action_outcomes': outcomes,
            'order_states': state, 'trace': trace,
            'limitations': ['观察的优先级由上游提供，本模块不学习风险更新规则。',
                            '当前每单只支持一种固定费用行动；失败不退费且禁止自动重试。',
                            '未实现持久化、并发事务或外部物流接口。']}


def demo():
    items = [{'order_id': key, 'cost_cents': cost, 'priority_units': value}
             for key, cost, value in [('A', 1000, 60), ('B', 2000, 130), ('C', 3000, 120)]]
    def event(n, kind, key, **extra):
        timestamp = f'2026-11-01T10:0{n}:00+08:00'
        return {'event_id': f'e{n}', 'type': kind, 'order_id': key,
                'occurred_at': timestamp, 'received_at': timestamp, **extra}
    events = [event(1, 'observation', 'A', status='exception', priority_units=200),
              event(2, 'commit', 'A'), event(3, 'observation', 'B', status='delivered'),
              event(4, 'action_result', 'A', outcome='failure')]
    result = replay(items, 5000, events, '2026-11-01T10:05:00+08:00')
    result.update({'source': '全部订单、费用、效用和状态均为人工构造，非Olist实时轨迹',
                   'input': {'items': items, 'events': events},
                   'expected_checks': {'initial_selects_B_C': result['initial_plan']['selected_ids'] == ['B', 'C'],
                                       'feedback_selects_A_B': result['trace'][0]['selected_ids'] == ['A', 'B'],
                                       'final_selects_C': result['final_plan']['selected_ids'] == ['C'],
                                       'failed_action_keeps_cost': result['final_plan']['spent_cents'] == 1000}})
    assert all(result['expected_checks'].values())
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, default=Path(__file__).resolve().parents[1] / 'reports/反馈回放示例.json')
    args = p.parse_args()
    result = demo()
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'checks': result['expected_checks'], 'final_plan': result['final_plan']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
