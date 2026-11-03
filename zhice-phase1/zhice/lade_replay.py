"""真实接单/完成历史接入预算算法；资源费用和模拟行动不会改写历史完成时刻。"""
from contextlib import closing
import hashlib
import json

from .lade_db import CITIES, ELIGIBLE, md_day, time_seconds
from .lade_query import catalog, connect, raw_time, scope, verified_metrics
from .rolling_optimizer import compare
from .rolling_verification import verify_comparison

PARAMETERS = {'min_gain_bps': 200, 'switch_penalty_units': 500, 'cooldown_steps': 2}
GRID_SECONDS = 300


def urgency(deadline, now):
    slack = deadline - now
    return 1000000 * 1800 // (1800 + max(slack, 0)) + min(max(-slack, 0), 7200) * 100


def resource_cost(task_key):
    return 1 + int.from_bytes(hashlib.sha256(('lade-resource-v1:' + task_key).encode()).digest()[:2], 'big') % 10


def make_real_stream(rows, start_s, steps, budget_points, revision):
    items = []; events = []; timeline = []
    for row in rows:
        key = f'lade:pickup:{row["city_code"]}:{row["task_id"]}'
        release = max(0, (row['accept_s'] - start_s + GRID_SECONDS - 1) // GRID_SECONDS)
        completed = (row['finish_s'] - start_s + GRID_SECONDS - 1) // GRID_SECONDS
        if not 0 <= release < steps or row['finish_s'] <= start_s:
            raise ValueError('任务不在回放队列范围内')
        items.append({'order_id': key, 'cost_cents': resource_cost(key) * 100,
                      'priority_units': urgency(row['window_end_s'], start_s + release * GRID_SECONDS),
                      'available_step': release, 'source_task_id': row['task_id'],
                      'window_end_s': row['window_end_s']})
        timeline.append({'task_key': key, 'source_task_id': row['task_id'], 'external_id': row['external_id'],
                         'accept_s': row['accept_s'], 'finish_s': row['finish_s'],
                         'window_start_s': row['window_start_s'], 'window_end_s': row['window_end_s'],
                         'available_step': release, 'completion_step': completed})
        for step in range(release, steps):
            done = step >= completed
            events.append({'event_id': f'{key}:{step:02d}', 'order_id': key, 'step': step,
                           'occurred_step': step, 'status': 'completed' if done else 'pending',
                           'priority_units': 0 if done else urgency(row['window_end_s'], start_s + step * GRID_SECONDS)})
            if done: break
    return {'items': items, 'events': events, 'steps': steps, 'budget_cents': budget_points * 100,
            'source': 'LaDe真实历史接单/揽收完成记录；时间窗规则效用；模拟资源费用与行动',
            'source_revision': revision, 'start_s': start_s, 'grid_seconds': GRID_SECONDS,
            'source_timeline': timeline,
            'accounting_unit': '1模拟资源点=100内部整数刻度，无BRL或人民币含义'}


def verify_raw_replay(database, stream, result):
    ledger = verify_comparison(stream, result)
    by_key = {item['order_id']: item for item in stream['items']}
    ids = [item['source_task_id'] for item in stream['items']]
    sources = {}
    with closing(connect(database)) as con:
        if ids:
            raw = con.execute(f'''SELECT _task_id,order_id,accept_time,pickup_time,time_window_start,time_window_end
                FROM raw_pickup WHERE _task_id IN ({','.join('?' for _ in ids)})''', ids)
            sources = {row['_task_id']: dict(row) for row in raw}
    if len(sources) != len(ids): raise ValueError('原始揽收任务不完整')
    checked_plans = checked_actions = 0
    for policy in result['results']:
        for trace in policy['trace']:
            now = stream['start_s'] + trace['step'] * stream['grid_seconds']
            for key in trace['planned_ids_before_execution']:
                source = sources[by_key[key]['source_task_id']]
                accept, finish = raw_time(source['accept_time']), raw_time(source['pickup_time'])
                if accept is None or finish is None or not accept <= now < finish:
                    raise ValueError('原始事实复核失败：选中了尚未接单或已经完成的任务')
                checked_plans += 1
            action = trace['executed']
            if action:
                key = action['order_id']; source = sources[by_key[key]['source_task_id']]
                deadline = raw_time(source['time_window_end'])
                # 独立从原始字符串与当前时刻算规则效用，不读取未来完成时刻作为评分特征。
                slack = deadline - now
                expected = 1000000 * 1800 // (1800 + max(slack, 0)) + 100 * min(max(-slack, 0), 7200)
                if action['priority_units'] != expected: raise ValueError('原始时间窗与动作效用不一致')
                if action['cost_cents'] != by_key[key]['cost_cents']: raise ValueError('模拟资源记账不一致')
                checked_actions += 1
    return {**ledger, 'raw_source_check': 'passed', 'planned_task_occurrences_checked': checked_plans,
            'actions_checked_against_raw': checked_actions,
            'scope': '独立原始时间解析+任务可见性+完成退出+规则效用+预算记账；不证明干预有效'}


def scenario(database, city, day, courier_id=None, budget_points=50, start_time=None, duration_minutes=120, limit=100):
    scope('pickup', city, day, courier_id)
    if type(budget_points) is not int or not 0 <= budget_points <= 5000:
        raise ValueError('模拟资源预算须为0到5000的整数')
    if type(duration_minutes) is not int or not 30 <= duration_minutes <= 180 or duration_minutes % 5:
        raise ValueError('回放长度须为30到180分钟的5分钟整数倍')
    if type(limit) is not int or not 1 <= limit <= 200: raise ValueError('候选上限须为1到200')
    info = catalog(database, city, day)
    if courier_id is None:
        if not info['couriers']: raise ValueError('所选范围没有有效揽收任务')
        courier_id = info['couriers'][0]['courier_id']
    with closing(connect(database)) as con:
        courier = con.execute('SELECT * FROM scope_catalog WHERE kind=? AND city_code=? AND accept_day=? AND courier_id=?',
                              ('pickup', city, day, courier_id)).fetchone()
        if courier is None: raise ValueError('快递员在所选城市和日期没有有效揽收任务')
        if start_time is None:
            minutes = min(1435, (courier['first_accept_s'] % 86400 + GRID_SECONDS - 1) // GRID_SECONDS * 5)
            start_time = f'{minutes//60:02d}:{minutes%60:02d}'
        if not isinstance(start_time, str) or len(start_time) != 5:
            raise ValueError('开始时刻须为HH:MM')
        start = time_seconds(day + ' ' + start_time + ':00')
        end = start + duration_minutes * 60 if start is not None else None
        if start is None or end >= md_day(day) + 86400:
            raise ValueError('回放时刻不合法或超出所选月日')
        params = ('pickup', city, day, courier_id, end, start)
        where = f'''kind=? AND city_code=? AND accept_day=? AND courier_id=?
            AND accept_s<=? AND finish_s>? AND {ELIGIBLE}'''
        total = con.execute('SELECT COUNT(*) FROM tasks WHERE ' + where, params).fetchone()[0]
        rows = [dict(row) for row in con.execute('SELECT * FROM tasks WHERE ' + where + ' ORDER BY accept_s,task_id LIMIT ?', (*params, limit))]
        revision = con.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0]
    evidence = verified_metrics(database, 'pickup', city, day, courier_id)
    stream = make_real_stream(rows, start, duration_minutes // 5 + 1, budget_points, revision)
    result = compare(stream, PARAMETERS)
    result.update({'status': 'passed' if rows else 'no_candidates', 'city': city, 'city_label': CITIES[city],
                   'day': day, 'courier_id': courier_id, 'start_time': start_time,
                   'duration_minutes': duration_minutes, 'budget_points': budget_points,
                   'candidate_rule': '当日该快递员：开始时尚未完成或期间新接单；按接单时刻、任务行号取前N笔',
                   'eligible_scope_tasks': total, 'candidate_limit': limit, 'input': stream,
                   'historical_metrics': evidence, 'stable_parameters': PARAMETERS})
    result['verification'] = verify_raw_replay(database, stream, result)
    result['limitations'] = ['接单和完成时刻为真实历史记录，按5分钟网格延后至下一可见快照；没有实际接收延迟记录。',
                             '优先级是距时间窗截止的确定性规则，不是训练出的延迟概率；假设接单时可获知时间窗。',
                             '费用1到10资源点、每轮最多一次行动均为模拟；行动不改变真实历史完成时刻。',
                             '累计规则效用不是挽回订单数或实际节省费用，不能证明干预降低延误。',
                             '仅回放所选月日有效完整任务，不含昨日遗留；自动默认快递员按历史有效任务数选择，属于回顾性样本。',
                             '年份未知，未拼接Olist订单；轨迹postman_id与城市courier_id未建立对应关系。']
    return result
