"""5城市×10个均匀选取月日×4种资源预算；不按结果选择日期或调参。"""
import hashlib
import json
from pathlib import Path
from statistics import mean

from zhice.lade_query import catalog
from zhice.lade_replay import PARAMETERS, scenario

ROOT = Path(__file__).resolve().parents[1]


def main():
    db = ROOT / 'data/lade/warehouse.sqlite'
    protocol = {'cities': ['jl', 'hz', 'sh', 'cq', 'yt'], 'days_per_city': 10,
                'budgets_points': [0, 20, 50, 100], 'duration_minutes': 120, 'candidate_limit': 40,
                'day_selection': '有效日期列表按等间隔取10个，不按延迟或算法成绩选择',
                'courier_selection': '该日期有效历史任务数最多的快递员；同分按编号。回顾性样本，不是线上派单策略',
                'policy_parameters': PARAMETERS, 'parameter_fitting': '未拟合，规则与参数预先指定',
                'meaning': '真实历史任务事件+规则优先级+模拟资源与行动，不是实际干预效果评估'}
    (ROOT / 'models/lade-rule-policy.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding='utf8')
    cases = []; scopes = {}; ids = set(); audits = {'rounds': 0, 'actions': 0, 'planned_occurrences': 0}
    for city in protocol['cities']:
        days = catalog(db, city)['days']
        chosen = days[::max(1, len(days)//10)][:10]
        for day in chosen:
            info = catalog(db, city, day); courier = info['couriers'][0]['courier_id']
            for budget in protocol['budgets_points']:
                r = scenario(db, city, day, courier, budget, duration_minutes=120, limit=40)
                if r['verification']['raw_source_check'] != 'passed' or r['historical_metrics']['status'] != 'passed':
                    raise ValueError('真实源校验失败')
                for row in r['input']['items']: ids.add(row['source_task_id'])
                a = r['verification']; audits['rounds'] += a['rounds_checked']; audits['actions'] += a['actions_checked_against_raw']
                audits['planned_occurrences'] += a['planned_task_occurrences_checked']
                scopes[f'{city}:{day}:{courier}'] = r['historical_metrics']['metrics']
                case = {'city': city, 'day': day, 'courier_id': courier, 'start_time': r['start_time'],
                        'budget_points': budget, 'candidate_count': r['candidate_count'],
                        'stream_sha256': r['stream_sha256'], 'verification': a,
                        'source_task_ids': [x['source_task_id'] for x in r['input']['items']],
                        'results': [{k: v for k, v in row.items() if k != 'trace'} for row in r['results']]}
                cases.append(case)
                if len(cases) == 3:
                    (ROOT / 'reports/lade-replay-example.json').write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding='utf8')
                    (ROOT / 'reports/lade-reference-example.sql').write_text(r['historical_metrics']['sql']['standalone_raw_reference'], encoding='utf8')
        print(f'{city}真实事件回放已完成：累计{len(cases)}场景', flush=True)
    aggregate = {}
    for policy in ('static', 'periodic', 'immediate', 'stable'):
        rows = [next(row for row in case['results'] if row['policy'] == policy) for case in cases]
        aggregate[policy] = {f'mean_{key}': mean(row[key] for row in rows)
                             for key in ('utility_units', 'spent_cents', 'executed_count', 'solver_calls', 'plan_edit_count')}
    summary = {'status': 'passed', 'scenario_count': len(cases), 'policy_runs': len(cases)*4,
               'verified_scopes': len(scopes), 'unique_source_tasks': len(ids), 'audit_counts': audits,
               'aggregate': aggregate, 'protocol': protocol,
               'import_report_sha256': hashlib.sha256((ROOT / 'reports/lade-import-validation.json').read_bytes()).hexdigest(),
               'code_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in [
                   'zhice/lade_replay.py','zhice/lade_query.py','zhice/rolling_optimizer.py','zhice/rolling_verification.py']},
               'limitations': ['历史事实真实，但费用、行动与执行容量为模拟，不能称实际延误减少或费用节省。',
                               '规则效用不是概率，也不是在LaDe上训练的新预测模型。',
                               '每个范围最多40笔，未验证全部1065万条任务的所有调度组合；数据库行数与文件对账覆盖全量。']}
    (ROOT / 'reports/lade-replay-evaluation.json').write_text(json.dumps({'summary': summary, 'scopes': scopes, 'cases': cases}, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
