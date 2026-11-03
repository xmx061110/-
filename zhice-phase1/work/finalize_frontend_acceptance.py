"""复核新版界面实际下载记录，不改写旧版本验收。"""
from contextlib import closing
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from zhice.four_agents import RunStore, snapshot, verify_plan, ROLES
from zhice.lade_replay import verify_raw_replay

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(4*1024*1024),b''):value.update(block)
    return value.hexdigest()


class IdParser(HTMLParser):
    def __init__(self):super().__init__();self.ids=[]
    def handle_starttag(self,tag,attrs):
        self.ids.extend(value for key,value in attrs if key=='id')


def main():
    reports=ROOT/'reports';read=lambda name:json.loads((reports/name).read_text(encoding='utf8'))
    browser=read('frontend-redesign-browser.json');record=read('四Agent新版浏览器下载证据.json')
    assert browser['status']=='passed' and browser['run_id']==record['run_id']
    assert record==RunStore(ROOT/'data/workflow-runs.sqlite').read(record['run_id'])
    result=record['result'];assert result['status']=='passed' and result['model_calls']==4
    assert [x['role'] for x in result['stages']]==list(ROLES)
    db=ROOT/'data/lade/warehouse.sqlite';public,evidence=snapshot(db,result['request'],result['snapshot']['as_of_s'])
    assert public==result['snapshot'] and evidence==result['snapshot_evidence']
    assert verify_plan(db,result['request'],public,result['proposal']['initial_plan'],result['proposal']['policy'])==result['hard_review']
    audit=verify_raw_replay(db,result['replay']['input'],result['replay']);assert audit['status']=='passed'
    with closing(sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True)) as con:
        con.row_factory=sqlite3.Row
        rows=[dict(row) for row in con.execute(evidence['standalone_raw_reference'])]
        assert len(rows)==public['accepted_count']
        assert sum(row['finish_s']>public['as_of_s'] for row in rows)==public['active_total']
        day=dict(con.execute(result['retrospective_metrics']['sql']['standalone_raw_reference']).fetchone())
        assert day==result['retrospective_metrics']['reference_counts']
    baseline=read('四Agent浏览器下载证据.json')['result']
    assert result['snapshot']==baseline['snapshot'] and result['proposal']==baseline['proposal']
    plan=result['proposal']['initial_plan']
    assert browser['active']==f'{public["active_total"]}笔' and browser['selected']==f'{len(plan["selected_ids"])}笔'
    assert browser['budget']==f'{plan["cost_cents"]//100}点' and browser['review']=='已通过'
    assert browser['flow_count']=='4 / 4 已完成' and browser['policy_rows']==4 and browser['task_rows']==5
    assert not browser['horizontal_overflow']
    log=(reports/'frontend-redesign-tests.log').read_text(encoding='utf-8-sig')
    test_count=int(re.search(r'Ran (\d+) tests',log)[1]);assert test_count==111 and log.rstrip().endswith('OK')
    html=IdParser();html.feed((ROOT/'agent_demo/index.html').read_text(encoding='utf8'));assert len(html.ids)==len(set(html.ids))
    for name in ('四Agent工作台改版.jpg','四Agent工作台改版-完整页面.jpg','四Agent工作台-真实执行中.jpg'):
        path=reports/name;assert path.stat().st_size>10000 and path.read_bytes().startswith(b'\xff\xd8\xff')
    original=digest(ROOT/'data/warehouse.sqlite');assert original==read('four-agent-acceptance.json')['olist_database_sha256']
    names=['agent_demo/index.html','agent_demo/app.js','agent_demo/workspace.js','agent_demo/workspace.css',
           'zhice/agent_server.py','zhice/four_agents.py','tests/test_agent_server.py','tests/test_four_agents.py']
    proof=['frontend-redesign-browser.json','frontend-redesign-tests.log','四Agent新版浏览器下载证据.json',
           '四Agent工作台改版.jpg','四Agent工作台改版-完整页面.jpg','四Agent工作台-真实执行中.jpg']
    report={'status':'passed','checked_at_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'前端工作台改版与真实进度可视化','test_count':test_count,'browser_cases':browser['cases'],
        'browser_viewport':browser['viewport'],'browser_run_id':record['run_id'],'actual_model_calls':4,
        'downloaded_record_matches_saved_record':True,'snapshot_and_plan_match_previous_same_scope_run':True,
        'standalone_raw_sql':'passed','independent_replay_audit':audit,
        'ui_matches_verified_record':True,'unique_html_ids':True,'olist_database_unchanged':True,
        'code_sha256':{name:digest(ROOT/name) for name in names},
        'evidence_sha256':{name:digest(reports/name) for name in proof},
        'boundaries':['后台真实事件驱动节点；非定时进度演示。',
            '当前截图为同一模型四角色的真实运行及明确标记的历史记录载入。',
            '真实历史任务、模拟资源行动；本次未增加实际物流干预能力。',
            '浏览器验证使用当前桌面视口；窄屏媒体样式已实现，未做手机设备实测。',
            '旧版验收报告保留为历史证据，不代表其代码哈希仍对应本轮版本。']}
    (reports/'frontend-redesign-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({key:report[key] for key in ('status','test_count','actual_model_calls','ui_matches_verified_record','standalone_raw_sql')},ensure_ascii=False))


if __name__=='__main__':main()
