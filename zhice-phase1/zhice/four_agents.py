"""原定四角色的受控协作：模型作结构化选择，数据与预算由独立工具复核。"""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .budget_optimizer import solve
from .lade_db import CITIES, ELIGIBLE, md_day, time_seconds
from .lade_query import catalog, connect, reference_sql, scope, sql_time, standalone_sql
from .lade_replay import resource_cost, urgency, scenario, verify_raw_replay
from .multi_agent import model_json
from .rolling_optimizer import fingerprint, POLICIES

ROLES = ('总控Agent', '经营分析Agent', '优化决策Agent', '方案评审Agent')
GOALS = {'priority': '优先关注临近截止的任务', 'stability': '减少方案频繁变动'}
INPUT_KEYS = {'city', 'day', 'courier_id', 'start_time', 'duration_minutes', 'budget_points', 'goal', 'mode'}
LIMITS = ['真实历史事件回放，不是实时物流接口；费用和关注行动为模拟。',
          '优先级是截止时间规则，未训练新的延误概率模型；不声称实际减少延误。',
          '模型策略在回放前选择，不能依据回放后的成绩改选策略。',
          '使用同一模型的4次独立角色调用及受控JSON输出，不是4个独立训练的模型。',
          '历史有效完整任务队列，不含前日遗留；年份未知，未拼接Olist订单。']


class RunStore:
    """每次运行独立事务保存；不保存模型密钥或认证标头。"""
    def __init__(self, path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connection()) as con, con:
            con.executescript('''CREATE TABLE IF NOT EXISTS runs(
                run_id TEXT PRIMARY KEY,status TEXT NOT NULL,created_at TEXT NOT NULL,
                request_json TEXT NOT NULL,result_json TEXT);
                CREATE TABLE IF NOT EXISTS events(run_id TEXT NOT NULL,seq INTEGER NOT NULL,
                payload_json TEXT NOT NULL,previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL,
                PRIMARY KEY(run_id,seq),FOREIGN KEY(run_id) REFERENCES runs(run_id));''')

    def connection(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.execute('PRAGMA foreign_keys=ON'); return con

    def begin(self, request):
        key = uuid.uuid4().hex
        with closing(self.connection()) as con, con:
            con.execute('INSERT INTO runs VALUES(?,?,?,?,NULL)',
                (key, 'running', datetime.now(timezone.utc).isoformat(), json.dumps(request, ensure_ascii=False)))
        return key

    def append(self, key, payload):
        with closing(self.connection()) as con, con:
            con.execute('BEGIN IMMEDIATE')
            last = con.execute('SELECT seq,event_hash FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 1', (key,)).fetchone()
            seq, previous = (last[0]+1, last[1]) if last else (1, '')
            digest = fingerprint({'run_id': key, 'seq': seq, 'previous_hash': previous, 'payload': payload})
            con.execute('INSERT INTO events VALUES(?,?,?,?,?)', (key, seq, json.dumps(payload, ensure_ascii=False), previous, digest))

    def finish(self, key, result):
        with closing(self.connection()) as con, con:
            con.execute('UPDATE runs SET status=?,result_json=? WHERE run_id=?',
                (result['status'], json.dumps(result, ensure_ascii=False), key))

    def read(self, key):
        if not isinstance(key, str) or len(key) != 32 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('运行编号不合法')
        with closing(self.connection()) as con:
            row = con.execute('SELECT status,request_json,result_json FROM runs WHERE run_id=?', (key,)).fetchone()
            events = con.execute('SELECT seq,payload_json,previous_hash,event_hash FROM events WHERE run_id=? ORDER BY seq', (key,)).fetchall()
        if row is None: raise ValueError('没有这个运行记录')
        previous = ''; history = []
        for expected_seq, (seq, payload, parent, digest) in enumerate(events, 1):
            value = json.loads(payload)
            if seq != expected_seq or parent != previous or digest != fingerprint(
                    {'run_id': key, 'seq': seq, 'previous_hash': parent, 'payload': value}):
                raise ValueError('运行记录指纹复核失败')
            history.append({'seq': seq, 'payload': value, 'previous_hash': parent, 'event_hash': digest}); previous = digest
        result = json.loads(row[2]) if row[2] else None
        if result is not None:
            if not history or history[-1]['payload'] != {'kind': 'final_result', 'result_sha256': fingerprint(result)}:
                raise ValueError('运行结果与记录指纹不一致')
        return {'run_id': key, 'status': row[0], 'request': json.loads(row[1]), 'result': result,
                'events': history, 'record_verification': 'passed',
                'hash_boundary': '本地一致性检查，无外部签名；不抵御同时重写整个库'}


def validated_request(database, request):
    if not isinstance(request, dict) or set(request) - INPUT_KEYS: raise ValueError('四Agent请求包含不支持的字段')
    r = {'budget_points': 50, 'duration_minutes': 120, 'goal': 'priority', 'mode': 'model', **request}
    scope('pickup', r.get('city'), r.get('day'), r.get('courier_id'))
    if not isinstance(r['goal'], str) or r['goal'] not in GOALS: raise ValueError('请选择支持的决策目标')
    if not isinstance(r['mode'], str) or r['mode'] not in ('model', 'tools'): raise ValueError('运行模式不合法')
    if type(r['budget_points']) is not int or not 0 <= r['budget_points'] <= 5000: raise ValueError('资源预算须为0到5000的整数')
    if type(r['duration_minutes']) is not int or not 30 <= r['duration_minutes'] <= 180 or r['duration_minutes'] % 5:
        raise ValueError('回放时长须为30到180分钟的5分钟整数倍')
    info = catalog(database, r['city'], r['day'])
    couriers = info['couriers']
    if r.get('courier_id') is None:
        if not couriers: raise ValueError('没有有效快递员')
        r['courier_id'] = couriers[0]['courier_id']
    with closing(connect(database)) as con:
        courier = con.execute('SELECT first_accept_s FROM scope_catalog WHERE kind=? AND city_code=? AND accept_day=? AND courier_id=?',
            ('pickup', r['city'], r['day'], r['courier_id'])).fetchone()
    if courier is None: raise ValueError('快递员不在所选城市和月日')
    if r.get('start_time') is None:
        minute = min(1435, (courier[0] % 86400 + 299)//300*5)
        r['start_time'] = f'{minute//60:02d}:{minute%60:02d}'
    if not isinstance(r['start_time'], str) or len(r['start_time']) != 5: raise ValueError('开始时刻须为HH:MM')
    now = time_seconds(r['day'] + ' ' + r['start_time'] + ':00')
    if now is None or now + r['duration_minutes']*60 >= md_day(r['day'])+86400: raise ValueError('开始时刻或回放时长不合法')
    return r, now


def snapshot(database, r, now, limit=100):
    """只把当时已接单且未完成任务及截止时间交给决策角色。"""
    params = {'kind': 'pickup', 'city': r['city'], 'day': r['day'], 'courier': r['courier_id'], 'now': now}
    columns = 'task_id,external_id,accept_s,finish_s,window_start_s,window_end_s'
    sql = f'''SELECT {columns} FROM tasks WHERE kind=:kind AND city_code=:city AND accept_day=:day
        AND courier_id=:courier AND accept_s<=:now AND {ELIGIBLE} ORDER BY accept_s,task_id'''
    raw = reference_sql('pickup').rsplit('     SELECT ', 1)[0]
    raw = raw.replace('SELECT '+sql_time('i.accept_time'), 'SELECT i._task_id task_id,i.order_id external_id,'+sql_time('i.accept_time'), 1)
    raw += f'''SELECT {columns} FROM scoped WHERE eligible AND accept_s<=:now ORDER BY accept_s,task_id'''
    with closing(connect(database)) as con:
        params['file_id'] = con.execute('SELECT id FROM source_files WHERE kind=? AND city_code=?', ('pickup', r['city'])).fetchone()[0]
        facts = [dict(x) for x in con.execute(sql, params)]
        independent = [dict(x) for x in con.execute(raw, params)]
        revision = con.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0]
    if facts != independent: raise ValueError('当前快照与原始SQL不一致')
    active = [x for x in facts if x['finish_s'] > now]
    selected = active[:limit]
    items = []
    for x in selected:
        key = f'lade:pickup:{r["city"]}:{x["task_id"]}'
        items.append({'order_id': key, 'source_task_id': x['task_id'], 'accept_s': x['accept_s'],
                      'window_end_s': x['window_end_s'], 'priority_units': urgency(x['window_end_s'], now),
                      'cost_cents': resource_cost(key)*100})
    public = {'source': 'lade', 'revision': revision, 'as_of_s': now,
        'city': r['city'], 'day': r['day'], 'courier_id': r['courier_id'],
        'accepted_count': len(facts), 'completed_by_now': len(facts)-len(active),
        'active_total': len(active), 'candidate_limit': limit, 'candidate_count': len(items),
        'overdue_count': sum(x['window_end_s'] < now for x in active),
        'deadline_30min_count': sum(0 <= x['window_end_s']-now <= 1800 for x in active), 'items': items,
        'knowledge_boundary': '截至当前时刻的历史有效完整任务；不向模型提供未来完成时刻、未来接单或全日延迟率。'}
    evidence = {'status': 'passed', 'normalized_sql': sql, 'raw_reference_sql': raw,
        'standalone_raw_reference': standalone_sql(raw.replace(':now', str(now)), params),
        'parameters': params, 'records_compared': len(facts), 'snapshot_sha256': fingerprint(public)}
    return public, evidence


def verify_plan(database, r, public, proposal, selected):
    fresh, _ = snapshot(database, r, public['as_of_s'])
    if fresh != public: raise ValueError('当前任务快照已变化，必须重新决策')
    if selected not in POLICIES: raise ValueError('策略不合法')
    ids = proposal.get('selected_ids')
    if not isinstance(ids, list) or len(ids) != len(set(ids)): raise ValueError('方案任务重复或格式错误')
    by_id = {x['order_id']: x for x in public['items']}
    if any(x not in by_id for x in ids): raise ValueError('方案包含不可见任务')
    cost = sum(by_id[x]['cost_cents'] for x in ids)
    value = sum(by_id[x]['priority_units'] for x in ids)
    if cost > r['budget_points']*100 or cost != proposal.get('cost_cents') or value != proposal.get('priority_units'):
        raise ValueError('方案预算或效用记账不一致')
    return {'status': 'passed', 'visible_tasks_only': True, 'unique_tasks': True,
            'cost_cents': cost, 'priority_units': value, 'budget_cents': r['budget_points']*100,
            'snapshot_sha256': fingerprint(public), 'policy_chosen_before_replay': selected,
            'scope': '原始SQL来源与当前可见性、无重复、预算及效用记账；不证明动态全局最优或真实干预效果'}


def run(database, store_path, request, config=None, transport=None, progress=None):
    r, now = validated_request(database, request)
    if r['mode'] == 'model' and not transport and not config: raise ValueError('尚未配置模型；可明确选择仅工具演练')
    store = RunStore(store_path); key = store.begin(r)
    stages = []; calls = 0
    result = {'run_id': key, 'status': 'running', 'mode': r['mode'], 'request': r,
              'stages': stages, 'proposal': None, 'limitations': LIMITS}
    def stage(index, facts, instruction, fallback, validate):
        nonlocal calls
        role = ROLES[index]
        store.append(key, {'kind': 'role_input', 'role': role, 'input': facts})
        if progress: progress({'type': 'stage_started', 'run_id': key, 'index': index, 'role': role})
        if r['mode'] == 'model':
            calls += 1
            endpoint, secret, name = config or ('', '', 'test')
            value = model_json(role, instruction, facts, endpoint, secret, name, transport)
        else: value = fallback
        validate(value)
        node = {'role': role, 'status': 'passed', 'method': '真实模型调用' if r['mode'] == 'model' else '确定性工具演练',
                'input_sha256': fingerprint(facts), 'output': value}
        stages.append(node); store.append(key, {'kind': 'role_output', **node})
        if progress: progress({'type': 'stage_completed', 'run_id': key, 'index': index, 'stage': node})
        return value

    def exact(value, expected):
        if value != expected: raise ValueError('模型输出与已确认目标或原始事实不一致')

    try:
        mission = {'source': 'lade', 'goal': r['goal'], 'steps': ['analysis', 'optimization', 'review']}
        stage(0, {'confirmed_request': r, 'supported_source': 'lade', 'goal_meaning': GOALS[r['goal']]},
            '你是总控Agent。用户已确认筛选与目标。仅输出JSON '+json.dumps(mission)+'。不得改变目标或数据源，不输出其他字段。',
            mission, lambda v: exact(v, mission))
        public, evidence = snapshot(database, r, now)
        result['snapshot'] = public; result['snapshot_evidence'] = evidence
        attention = ('empty' if not public['active_total'] else 'overdue' if public['overdue_count'] else
                     'approaching_deadline' if public['deadline_30min_count'] else 'normal')
        analysis_expected = {'attention': attention, 'evidence_id': fingerprint(public)}
        analysis = stage(1, {'snapshot': public, 'evidence_id': fingerprint(public)},
            '你是经营分析Agent。仅输出JSON {"attention":"empty|overdue|approaching_deadline|normal","evidence_id":"输入的evidence_id"}。'
            'active_total为0选empty，否则overdue_count大于0选overdue，否则deadline_30min_count大于0选approaching_deadline，其他选normal。不能推断原因。',
            analysis_expected, lambda v: exact(v, analysis_expected))
        initial = solve(public['items'], r['budget_points']*100)
        initial.pop('elapsed_ms', None)  # 机器计时噪声不作为模型决策输入。
        store.append(key, {'kind': 'tool_output', 'tool': 'exact_budget_solver', 'output': initial})
        default_policy = 'immediate' if r['goal'] == 'priority' else 'stable'
        def validate_choice(v):
            if set(v) != {'policy', 'candidate_id'} or not isinstance(v['policy'], str) or v['policy'] not in POLICIES or v['candidate_id'] != 'exact_budget':
                raise ValueError('优化决策输出不合法')
        choice = stage(2, {'snapshot': public, 'analysis': analysis, 'goal': r['goal'], 'candidate_id': 'exact_budget', 'initial_plan': initial,
                           'policies': {'static': '初始后不重排', 'periodic': '每3轮重排', 'immediate': '状态变化即重排', 'stable': '门槛及冷却限制变动'}},
            '你是优化决策Agent。根据用户目标选择后续反馈重排策略，不改预算或任务。仅输出JSON '
            '{"policy":"static|periodic|immediate|stable","candidate_id":"exact_budget"}。priority通常选择immediate，stability通常选择stable。'
            '尚未发生回放，不得声称所选策略未来最佳。初始方案由精确整数预算工具给定。',
            {'policy': default_policy, 'candidate_id': 'exact_budget'}, validate_choice)
        hard_review = verify_plan(database, r, public, initial, choice['policy'])
        result['hard_review'] = hard_review
        def validate_review(v):
            if set(v) != {'approved', 'reason_code'} or type(v['approved']) is not bool or v['reason_code'] not in ('constraints_checked', 'manual_review_required'):
                raise ValueError('方案评审输出不合法')
            if v['approved'] != (v['reason_code'] == 'constraints_checked'): raise ValueError('评审结论与理由矛盾')
        review = stage(3, {'confirmed_request': r, 'snapshot': public, 'analysis': analysis, 'policy_choice': choice,
                          'initial_plan': initial, 'independent_checks': hard_review, 'limitations': LIMITS},
            '你是方案评审Agent。检查输入约束和工具复核，全部通过可输出JSON {"approved":true,"reason_code":"constraints_checked"}。'
            '如需拒绝输出 {"approved":false,"reason_code":"manual_review_required"}。只输出这些字段，不声称实际降低延误。',
            {'approved': True, 'reason_code': 'constraints_checked'}, validate_review)
        result['model_calls'] = calls
        if not review['approved']:
            result['status'] = 'review_rejected'
        else:
            # 模型策略已经冻结；以下历史回放只用于事后验证，绝不再调用模型改选。
            if progress: progress({'type': 'verification_started', 'run_id': key})
            replay = scenario(database, r['city'], r['day'], r['courier_id'], r['budget_points'], r['start_time'], r['duration_minutes'])
            verify_raw_replay(database, replay['input'], replay)
            # 当前初始计划必须与回放的同一时间截面完全一致。
            first = next(x for x in replay['results'] if x['policy'] == choice['policy'])['trace'][0]
            if set(first['planned_ids_before_execution']) != set(initial['selected_ids']): raise ValueError('回放初始方案与评审快照不一致')
            result.update(status='passed', proposal={'policy': choice['policy'], 'initial_plan': initial},
                          replay=replay, retrospective_metrics=replay['historical_metrics'],
                          replay_choice_boundary='四角色输出先冻结，再生成历史回放；全日指标仅供事后核对，未传入决策模型')
            store.append(key, {'kind': 'tool_output', 'tool': 'historical_replay', 'input_sha256': replay['stream_sha256'], 'verification': replay['verification']})
    except Exception as exc:
        # 不保存外部服务异常正文，避免认证信息或第三方文本进入记录。
        result.update(status='failed', proposal=None, model_calls=calls,
                      error={'type': type(exc).__name__, 'message': '模型输出、数据或方案复核失败，未发布方案。'})
        store.append(key, {'kind': 'failure', 'error_type': type(exc).__name__})
    store.append(key, {'kind': 'final_result', 'result_sha256': fingerprint(result)})
    store.finish(key, result)
    return store.read(key)
