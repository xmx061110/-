"""可复现的单智能体验收；真实模型测试必须显式启用并自行配置凭据。"""
import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from zhice.agent import ask_model, metric_tool

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'warehouse.sqlite'
CASES = [
    ('全部订单', {}),
    ('2018年1月', {'month': '2018-01'}),
    ('2017年11月', {'month': '2017-11'}),
    ('美妆健康类', {'category': 'beleza_saude'}),
    ('床浴桌布类', {'category': 'cama_mesa_banho'}),
    ('指定商家', {'seller': '4a3ca9315b744ce9f8e9374361493884'}),
    ('月份和类别', {'month': '2018-01', 'category': 'beleza_saude'}),
    ('月份和商家', {'month': '2018-01', 'seller': '4a3ca9315b744ce9f8e9374361493884'}),
    ('三项组合', {'month': '2018-01', 'category': 'beleza_saude', 'seller': '4a3ca9315b744ce9f8e9374361493884'}),
    ('无匹配月份', {'month': '2099-01'}),
]
QUESTIONS = [
    ('2018年1月订单配送延迟率是多少？', {'month': '2018-01'}),
    ('2017年11月有多少订单？', {'month': '2017-11'}),
    ('商品类别 beleza_saude 的低评分占比是多少？', {'category': 'beleza_saude'}),
    ('商家 4a3ca9315b744ce9f8e9374361493884 的履约表现如何？', {'seller': '4a3ca9315b744ce9f8e9374361493884'}),
    ('查询2018年1月类别 beleza_saude 的配送情况', {'month': '2018-01', 'category': 'beleza_saude'}),
    ('最近一个月的延迟率是多少？', None),
    ('比较2017年和2018年的配送延迟率', None),
    ('哪个商家的延迟率最高？', None),
    ('利润是多少？', None),
    ('为什么会配送延迟？', None),
]

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--real-model', action='store_true', help='调用配置好的真实模型，可能产生模型费用')
    args = p.parse_args()
    if not DB.exists(): raise SystemExit('未找到数据仓库')
    with DB.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
              'database_sha256': digest,
              'database_cases': [], 'real_model': {'status': 'not_run', 'cases': []}}
    for name, filters in CASES:
        try:
            result = metric_tool(DB, filters)
            report['database_cases'].append({'name': name, 'filters': filters, 'passed': result['status'] == 'passed',
                'checks_passed': result['checks_passed'], 'checks_total': result['check_count'],
                'empty': result['empty'], 'metrics': result['program'], 'sql': result['sql']})
        except Exception as exc:
            report['database_cases'].append({'name': name, 'filters': filters, 'passed': False, 'error': type(exc).__name__})
    if args.real_model:
        url, key, model = (os.getenv(k) for k in ('ZHICE_MODEL_URL', 'ZHICE_MODEL_KEY', 'ZHICE_MODEL_NAME'))
        if not all((url, key, model)): raise SystemExit('缺少 ZHICE_MODEL_URL、ZHICE_MODEL_KEY 或 ZHICE_MODEL_NAME，未调用模型')
        report['real_model']['status'] = 'completed'
        report['real_model']['model'] = model
        for question, expected in QUESTIONS:
            try:
                decision = ask_model(question, url, key, model)
                passed = (decision['status'] == 'needs_clarification' if expected is None else
                          decision['status'] == 'tool_call' and decision['arguments'] == expected)
                entry = {'question': question, 'expected': expected, 'decision': decision, 'passed': passed}
                if passed and expected is not None:
                    evidence = metric_tool(DB, decision['arguments'])
                    entry['database_validation'] = {'status': evidence['status'], 'filters': evidence['filters'],
                        'checks_passed': evidence['checks_passed'], 'checks_total': evidence['check_count'],
                        'order_count': evidence['program']['order_count'], 'sql': evidence['sql']}
                    entry['passed'] = evidence['status'] == 'passed'
                report['real_model']['cases'].append(entry)
            except Exception as exc:
                report['real_model']['cases'].append({'question': question, 'expected': expected, 'passed': False, 'error': type(exc).__name__})
    out = ROOT / 'reports' / '单智能体验收结果.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    good = sum(case['passed'] for case in report['database_cases'])
    print(f'数据库验收：{good}/{len(CASES)}；真实模型：{report["real_model"]["status"]}；结果：{out}')
    if good != len(CASES) or any(not x['passed'] for x in report['real_model']['cases']): raise SystemExit(1)

if __name__ == '__main__': main()
