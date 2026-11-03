"""交互演示使用开发阶段冻结的参数，不根据用户查看的场景调参。"""
from datetime import date
import json

from .budget_optimizer import simulated_candidates
from .rolling_optimizer import compare, make_stream
from .rolling_verification import verify_comparison


def scenario(root, day, budget_cents):
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError('日期须为YYYY-MM-DD')
    if type(budget_cents) is not int or not 0 <= budget_cents <= 500000:
        raise ValueError('演示预算范围为0到500000分')
    params = json.loads((root / 'models/rolling-policy.json').read_text(encoding='utf8'))
    items = simulated_candidates(root / 'models/historical-risk-test.csv', day, 100, 40)
    stream = make_stream(items, budget_cents, 100)
    result = compare(stream, params['parameters'])
    result.update({'day': day, 'seed': 100, 'input': stream, 'parameter_selection': params})
    result['verification'] = verify_comparison(stream, result)
    historical = json.loads((root / 'reports/rolling-optimization-summary.json').read_text(encoding='utf8'))
    if historical['selected_parameters'] != params:
        raise ValueError('当前参数与预存评估不一致，请重新生成评估报告')
    result['batch_reference'] = {
        'scenario_count': historical['scenario_count'], 'policy_runs': historical['policy_runs'],
        'aggregate': historical['aggregate'], 'stable_vs_immediate_net': historical['stable_vs_immediate_net'],
        'scope': '预存200场景汇总，不是当前选择的单场景结果；当前选择不保证稳定策略更好'}
    return result
