"""相同外生反馈下比较预算重规划策略；费用、状态变化和效用均非真实干预结果。"""
from collections import defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import random

from .budget_optimizer import solve, validate

POLICIES = ('static', 'periodic', 'immediate', 'stable')
ACTIVE = {'pending', 'picked_up', 'in_transit', 'exception'}
TERMINAL = {'delivered', 'canceled', 'completed'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf8')).hexdigest()


def validate_stream(stream):
    items, budget = stream['items'], stream['budget_cents']
    validate(items, budget)
    steps = stream['steps']
    if type(steps) is not int or not 1 <= steps <= 48:
        raise ValueError('回放轮数须为1到48')
    for item in items:
        release = item.get('available_step', 0)
        if type(release) is not int or not 0 <= release < steps:
            raise ValueError('任务可见轮次不合法')
    ids = {x['order_id'] for x in items}
    by_step, seen = defaultdict(list), {}
    for event in stream['events']:
        if not isinstance(event, dict) or set(event) != {
                'event_id', 'order_id', 'step', 'occurred_step', 'status', 'priority_units'}:
            raise ValueError('观察事件字段不合法')
        eid, key = event['event_id'], event['order_id']
        if not isinstance(eid, str) or not eid or not isinstance(key, str) or key not in ids:
            raise ValueError('观察事件ID或订单不合法')
        received, occurred = event['step'], event['occurred_step']
        if type(received) is not int or type(occurred) is not int or not 0 <= occurred <= received:
            raise ValueError('事件发生/接收轮次不合法')
        if received < next(x.get('available_step', 0) for x in items if x['order_id'] == key):
            raise ValueError('观察不能早于任务首次可见轮次')
        if not isinstance(event['status'], str) or event['status'] not in ACTIVE | TERMINAL:
            raise ValueError('观察状态不合法')
        if type(event['priority_units']) is not int or event['priority_units'] < 0:
            raise ValueError('观察效用须为非负整数')
        digest = fingerprint(event)
        if eid in seen:
            if digest != seen[eid]:
                raise ValueError('同一事件ID内容冲突')
            continue
        seen[eid] = digest
        by_step[received].append(event)
    return {step: sorted(rows, key=lambda e: e['event_id']) for step, rows in by_step.items()}


def run_policy(stream, policy, *, interval=3, min_gain_bps=500,
               switch_penalty_units=1000, cooldown_steps=2):
    """只使用当前轮及之前收到的信息。每轮最多执行1单，执行后费用不可退。"""
    if policy not in POLICIES:
        raise ValueError('未知重规划策略')
    for value, minimum in ((interval, 1), (min_gain_bps, 0),
                           (switch_penalty_units, 0), (cooldown_steps, 1)):
        if type(value) is not int or value < minimum:
            raise ValueError('策略参数不合法')
    by_step = validate_stream(stream)
    state = {x['order_id']: dict(deepcopy(x), status='pending') for x in stream['items']}
    executed, last_observed = {}, {}
    queue, trace = [], []
    spent = utility = solver_calls = accepted_replans = rejected_replans = edits = mandatory_removals = 0
    last_replan = -cooldown_steps
    stale_events = 0
    for step in range(stream['steps']):
        observations, changed = [], False
        changed = step > 0 and any(row.get('available_step', 0) == step for row in state.values())
        for event in by_step.get(step, []):
            key = event['order_id']
            observations.append(event['event_id'])
            if event['occurred_step'] <= last_observed.get(key, -1):
                stale_events += 1
                continue
            row = state[key]
            if row['status'] in TERMINAL and event['status'] != row['status']:
                raise ValueError('终态订单不可重新激活')
            different = (row['status'], row['priority_units']) != (event['status'], event['priority_units'])
            row.update(status=event['status'], priority_units=event['priority_units'])
            last_observed[key] = event['occurred_step']
            changed |= different and key not in executed
        active = {key: row for key, row in state.items()
                  if row['status'] in ACTIVE and key not in executed and row['priority_units'] > 0
                  and row.get('available_step', 0) <= step}
        remaining = stream['budget_cents'] - spent
        valid_queue = [key for key in queue if key in active]
        mandatory_removals += len(queue) - len(valid_queue)
        queue = valid_queue
        queue_value = sum(active[key]['priority_units'] for key in queue)
        affordable = any(row['cost_cents'] <= remaining for row in active.values())
        initial = step == 0
        forced = policy in {'immediate', 'stable'} and not queue and affordable
        due = initial or (policy == 'periodic' and step % interval == 0)
        due |= policy == 'immediate' and changed
        due |= policy == 'stable' and changed and step - last_replan >= cooldown_steps
        due |= forced
        if not initial and not affordable:
            due = False  # 无可负担行动时，不为空队列反复调用求解器。
        reason = '保留现有方案'
        if due:
            candidates = deepcopy(list(active.values()))
            if policy == 'stable' and not initial and not forced:
                # 对新加入订单收取效用单位的切换代价；它不是BRL费用。
                for row in candidates:
                    if row['order_id'] not in queue:
                        row['priority_units'] = max(0, row['priority_units'] - switch_penalty_units)
            proposal = solve(candidates, remaining)
            solver_calls += 1
            proposed = proposal['selected_ids']
            raw_value = sum(active[key]['priority_units'] for key in proposed)
            additions = len(set(proposed) - set(queue))
            threshold = (queue_value * min_gain_bps + 9999) // 10000
            net_gain = raw_value - queue_value - additions * switch_penalty_units
            accept = policy != 'stable' or initial or forced or net_gain > threshold
            if accept:
                if not initial:
                    edits += len(set(queue) ^ set(proposed))
                    accepted_replans += 1
                queue = proposed
                last_replan = step
                reason = '初始规划' if initial else '队列为空补充' if forced else '接受重规划'
            else:
                rejected_replans += 1
                reason = '净增益未过门槛，保留方案'
        planned_ids = list(queue)
        planned_cost = sum(active[key]['cost_cents'] for key in queue)
        if planned_cost > remaining:
            raise AssertionError('计划超出剩余预算')
        action = None
        if queue:
            # 所有策略使用相同执行规则：当前效用/费用最高的计划订单先执行。
            key = min(queue, key=lambda k: (-Fraction(active[k]['priority_units'], active[k]['cost_cents']), k))
            row = active[key]
            action = {'order_id': key, 'cost_cents': row['cost_cents'],
                      'priority_units': row['priority_units'], 'step': step}
            executed[key] = action
            spent += row['cost_cents']
            utility += row['priority_units']
            queue.remove(key)
        trace.append({'step': step, 'received_event_ids': observations, 'decision': reason,
                      'planned_ids_before_execution': planned_ids, 'planned_cost_cents': planned_cost,
                      'executed': action, 'spent_cents': spent, 'utility_units': utility,
                      'remaining_cents': stream['budget_cents'] - spent})
    assert spent <= stream['budget_cents']
    return {'policy': policy, 'stream_sha256': fingerprint(stream), 'budget_cents': stream['budget_cents'],
            'spent_cents': spent, 'utility_units': utility, 'executed_count': len(executed),
            'solver_calls': solver_calls, 'accepted_replans': accepted_replans,
            'rejected_replans': rejected_replans, 'plan_edit_count': edits,
            'mandatory_removals': mandatory_removals, 'stale_events_ignored': stale_events,
            'actions': list(executed.values()), 'trace': trace,
            'parameters': {'interval': interval, 'min_gain_bps': min_gain_bps,
                           'switch_penalty_units': switch_penalty_units, 'cooldown_steps': cooldown_steps}}


def make_stream(items, budget_cents, seed, *, steps=12):
    """固定种子生成与策略无关的外生模拟观察；不使用真实延迟标签。"""
    rng = random.Random(seed)
    known = {x['order_id']: x['priority_units'] for x in items}
    alive = sorted(known)
    events = []
    for step in range(1, steps):
        for key in rng.sample(alive, min(8, len(alive))):
            status = 'exception' if rng.random() < .35 else 'in_transit'
            if step >= 3 and rng.random() < .08:
                status = 'delivered' if rng.random() < .8 else 'canceled'
                alive.remove(key)
            known[key] = max(1, round(known[key] * rng.choice((.6, .85, 1.15, 1.6))))
            delay = rng.choice((0, 0, 0, 1, 2))
            events.append({'event_id': f'{step:02d}:{key}', 'order_id': key,
                           'step': step + delay, 'occurred_step': step,
                           'status': status, 'priority_units': known[key]})
    return {'items': deepcopy(items), 'budget_cents': budget_cents, 'steps': steps,
            'events': events, 'seed': seed,
            'source': '历史风险初值+人工费用、效用变化和状态；非真实物流或干预实验'}


def compare(stream, stable_parameters=None):
    params = stable_parameters or {'min_gain_bps': 500, 'switch_penalty_units': 1000, 'cooldown_steps': 2}
    results = [run_policy(stream, policy, **(params if policy == 'stable' else {}))
               for policy in POLICIES]
    return {'source': stream.get('source', '输入事件回放'), 'stream_sha256': fingerprint(stream),
            'candidate_count': len(stream['items']), 'steps': stream['steps'],
            'budget_cents': stream['budget_cents'], 'results': results,
            'limitations': ['累计效用是执行当时的输入评分之和，不是减少延迟的订单数。',
                            '反馈变化和费用均为模拟；不同策略使用相同外生事件，但没有行动改变物流的因果模型。',
                            '稳定策略可能牺牲效用换取较少方案切换，不保证始终更好。']}
