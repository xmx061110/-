"""汇总四Agent已有验收，重新执行下载证据内的SQL和独立约束复核。"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from zhice.four_agents import RunStore, ROLES, snapshot, verify_plan
from zhice.lade_replay import verify_raw_replay

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(4*1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def main():
    reports=ROOT/'reports'; read=lambda name:json.loads((reports/name).read_text(encoding='utf8'))
    tools=read('four-agent-tools-evaluation.json');live=read('four-agent-live-evaluation.json')
    for evaluated in (tools,live):
        assert evaluated['status']=='passed'
        for name,digest in evaluated['code_sha256'].items():assert sha256(ROOT/name)==digest,name
    assert tools['scenario_count']==40 and tools['model_calls']==0
    assert live['scenario_count']==3 and live['model_calls']==12
    store=RunStore(ROOT/'data/workflow-runs.sqlite')
    for case in tools['cases']+live['cases']:
        saved=store.read(case['run_id']); assert saved['result']['status']=='passed'
        if 'evidence' in case: assert saved==read(case['evidence'])
    log=(reports/'four-agent-regression-tests.log').read_text(encoding='utf-8-sig')
    tests=re.search(r'Ran (\d+) tests',log)
    assert tests and int(tests[1])==109 and log.rstrip().endswith('OK')
    record=read('四Agent浏览器下载证据.json'); assert record==store.read(record['run_id'])
    result=record['result']; assert result['status']=='passed' and result['model_calls']==4
    assert [x['role'] for x in result['stages']]==list(ROLES)
    database=ROOT/'data/lade/warehouse.sqlite';request=result['request'];public=result['snapshot']
    actual,evidence=snapshot(database,request,public['as_of_s'])
    assert actual==public and evidence==result['snapshot_evidence']
    plan=verify_plan(database,request,public,result['proposal']['initial_plan'],result['proposal']['policy'])
    assert plan==result['hard_review']
    replay=verify_raw_replay(database,result['replay']['input'],result['replay'])
    assert replay['status']=='passed'
    with closing(sqlite3.connect(f'file:{database.as_posix()}?mode=ro',uri=True)) as con:
        con.row_factory=sqlite3.Row
        raw=[dict(x) for x in con.execute(evidence['standalone_raw_reference'])]
        assert len(raw)==public['accepted_count']
        assert sum(x['finish_s']>public['as_of_s'] for x in raw)==public['active_total']
        full_day=dict(con.execute(result['retrospective_metrics']['sql']['standalone_raw_reference']).fetchone())
        assert full_day==result['retrospective_metrics']['reference_counts']
    screenshot=reports/'原定四Agent协作验证.png';assert screenshot.stat().st_size>10000
    original=sha256(ROOT/'data/warehouse.sqlite')
    assert original==read('lade-integration-acceptance.json')['olist_database_sha256']
    browser={'status':'passed','cases':[
        {'name':'小数预算拒绝','input_budget':'0.5','plan_published':False,'download_enabled':False},
        {'name':'零预算稳定工具演练','run_id':'73e8b4fc3d1942c38c1c7eea5f4140e9','model_calls':0,'selected_count':0},
        {'name':'目录更新锁定运行并清空旧结果','city':'hz','day':'07-15'},
        {'name':'杭州真实四角色运行及下载','run_id':record['run_id'],'model_calls':4,
         'accepted_count':public['accepted_count'],'active_total':public['active_total'],
         'selected_count':len(result['proposal']['initial_plan']['selected_ids']),
         'cost_points':result['proposal']['initial_plan']['cost_cents']//100}],
        'method':'实际页面选择、点击、下载与截图；非模拟DOM测试',
        'download_equals_saved_record':True,'standalone_snapshot_and_full_day_sql':'passed',
        'independent_plan_verification':plan,'replay_reverification':replay}
    zero=store.read(browser['cases'][1]['run_id'])['result']
    assert zero['model_calls']==0 and zero['proposal']['initial_plan']['selected_ids']==[]
    assert zero['proposal']['policy']=='stable'
    (reports/'four-agent-browser-validation.json').write_text(json.dumps(browser,ensure_ascii=False,indent=2),encoding='utf8')
    (reports/'four-agent-snapshot-example.sql').write_text(evidence['standalone_raw_reference'],encoding='utf8')
    names=['four-agent-tools-evaluation.json','four-agent-live-evaluation.json',
           'four-agent-live-1.json','four-agent-live-2.json','four-agent-live-3.json',
           'four-agent-regression-tests.log','four-agent-browser-validation.json',
           '四Agent浏览器下载证据.json','原定四Agent协作验证.png','four-agent-snapshot-example.sql']
    code=['zhice/four_agents.py','zhice/agent_server.py','agent_demo/index.html','agent_demo/app.js',
          'agent_demo/style.css','tests/test_four_agents.py','tests/test_agent_server.py',
          'work/four_agent_acceptance.py','work/finalize_four_agent_acceptance.py']
    summary={'status':'passed','checked_at_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'LaDe揽收场景本地受控四角色协作','roles':list(ROLES),'test_count':109,
        'database_tools_scenarios':40,'fixed_live_model_scenarios':3,'fixed_live_model_calls':12,
        'browser_live_model_scenarios':1,'browser_live_model_calls':4,
        'total_real_model_calls_in_recorded_acceptance':16,'browser_run_id':record['run_id'],
        'browser_case_count':len(browser['cases']),'evaluation_code_hashes_match':True,
        'downloaded_record_reverification':'passed','standalone_raw_sql_reverification':'passed',
        'olist_database_unchanged':True,'olist_database_sha256':original,
        'lade_database_bytes':database.stat().st_size,
        'code_sha256':{name:sha256(ROOT/name) for name in code},
        'evidence_sha256':{name:sha256(reports/name) for name in names},
        'limitations':['固定场景功能验收，不是整体准确率或真实物流干预收益。',
            '相同模型4次独立角色调用；固定受控JSON流程，不是开放式自主编排。',
            '模型仅起点调用；反馈回放后续轮次由确定性算法处理，未反复调用模型。',
            '当前候选上限100；真实历史状态，规则优先级和模拟费用、关注行动。',
            '未接生产实时物流、跨源订单映射、LaDe新预测模型或华为云。']}
    (reports/'four-agent-acceptance.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({key:summary[key] for key in ('status','test_count','database_tools_scenarios',
        'fixed_live_model_scenarios','browser_live_model_scenarios','total_real_model_calls_in_recorded_acceptance')},ensure_ascii=False))


if __name__=='__main__':main()
