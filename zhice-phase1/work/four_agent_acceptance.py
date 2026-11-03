"""四Agent真实数据库验收及本地启动；优先使用本机已授权配置。"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

from zhice.four_agents import run, RunStore
from zhice.lade_query import catalog
from zhice.rolling_optimizer import fingerprint
from zhice.model_settings import resolve_model_config

ROOT=Path(__file__).resolve().parents[1]


def model_config(shared_archive=None):
    url,key,name=resolve_model_config()
    if not key and shared_archive:
        # 只解析已授权团队共享包中的字符串常量，不执行包内脚本。
        with zipfile.ZipFile(shared_archive) as archive:
            names=[x for x in archive.namelist() if x.endswith('/启动.py')]
            if len(names)!=1: raise ValueError('共享包启动脚本不唯一')
            tree=ast.parse(archive.read(names[0]).decode('utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='setdefault' and len(node.args)==2:
                if isinstance(node.args[0],ast.Constant) and node.args[0].value=='ZHICE_MODEL_KEY': key=ast.literal_eval(node.args[1])
    if not isinstance(key,str) or not key: raise ValueError('没有可用模型配置')
    return url or 'https://maas.bit.edu.cn/v1/chat/completions',key,name or 'deepseek-v4-flash'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true')
    parser.add_argument('--shared-archive',type=Path);parser.add_argument('--serve',action='store_true')
    parser.add_argument('--port',type=int,default=8793);args=parser.parse_args()
    if args.serve:
        config=model_config(args.shared_archive)
        for k,v in zip(('ZHICE_MODEL_URL','ZHICE_MODEL_KEY','ZHICE_MODEL_NAME'),config):os.environ[k]=v
        from zhice.agent_server import main as server
        sys.argv=[sys.argv[0],'--port',str(args.port)];server();return
    database=ROOT/'data/lade/warehouse.sqlite';store=ROOT/'data/workflow-runs.sqlite'
    results=[]
    if not args.live:
        for city in ('jl','hz','sh','cq','yt'):
            first=catalog(database,city);days=[first['days'][0],first['days'][len(first['days'])//2]]
            for day in days:
                courier=catalog(database,city,day)['couriers'][0]
                for goal in ('priority','stability'):
                    for budget in (0,50):
                        request={'city':city,'day':day,'courier_id':courier['courier_id'],'start_time':courier['start_time'],
                            'duration_minutes':120,'budget_points':budget,'goal':goal,'mode':'tools'}
                        record=run(database,store,request);r=record['result']
                        assert r['status']=='passed' and r['model_calls']==0
                        assert len(r['stages'])==4 and r['hard_review']['status']=='passed'
                        assert record['record_verification']=='passed'
                        results.append({'run_id':record['run_id'],'request':request,'status':r['status'],
                            'snapshot_sha256':fingerprint(r['snapshot']),'policy':r['proposal']['policy'],
                            'replay_verification':r['replay']['verification']})
            print(f'{city}完成，累计{len(results)}组真实数据工具演练',flush=True)
        name='four-agent-tools-evaluation.json';model_calls=0
    else:
        config=model_config(args.shared_archive);model_calls=0
        for city,day,courier,start,goal,budget in [('hz','07-15','6060','09:00','priority',50),
                ('hz','07-15','6060','09:00','stability',50),('jl','05-01','14171','08:25','priority',0)]:
            request={'city':city,'day':day,'courier_id':courier,'start_time':start,'goal':goal,'budget_points':budget,'mode':'model'}
            record=run(database,store,request,config);r=record['result'];model_calls+=r['model_calls']
            path=ROOT/'reports'/f'four-agent-live-{len(results)+1}.json'
            path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
            results.append({'run_id':record['run_id'],'request':request,'status':r['status'],'model_calls':r['model_calls'],
                            'evidence':path.name,'roles':[x['role'] for x in r['stages']]})
            print(f'真实模型样例{len(results)}：{r["status"]}，调用{r["model_calls"]}次',flush=True)
            if r['status']!='passed': break
        name='four-agent-live-evaluation.json'
    code=['zhice/four_agents.py','zhice/agent_server.py','work/four_agent_acceptance.py']
    report={'status':'passed' if results and all(x['status']=='passed' for x in results) else 'failed',
        'date_utc':datetime.now(timezone.utc).isoformat(),'mode':'model' if args.live else 'tools',
        'scenario_count':len(results),'model_calls':model_calls,'cases':results,
        'code_sha256':{x:hashlib.sha256((ROOT/x).read_bytes()).hexdigest() for x in code},
        'boundary':'固定验收场景，不代表模型整体准确率或真实物流干预收益；工具演练不计作模型协作。'}
    (ROOT/'reports'/name).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:report[k] for k in ('status','mode','scenario_count','model_calls')},ensure_ascii=False))
    if report['status']!='passed':raise SystemExit(1)


if __name__=='__main__':main()
