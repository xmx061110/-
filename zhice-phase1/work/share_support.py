"""当前分包构建所需的启动模板、摘要计算与实际解压验证。"""
from contextlib import closing
from pathlib import Path
import hashlib,json,socket,sqlite3,subprocess,sys,time,urllib.error,urllib.request
ROOT=Path(__file__).resolve().parents[1]
WORKSPACE=ROOT.parent
BOOT=r'''"""智策团队工作台启动器；配置仅供已授权项目组使用。"""
import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys

def main():
    if sys.version_info<(3,11):raise SystemExit('需要Python 3.11或更新版本，推荐3.12。')
    parser=argparse.ArgumentParser();parser.add_argument('--tools',action='store_true')
    parser.add_argument('--port',type=int,default=8793);parser.add_argument('--no-open',action='store_true')
    parser.add_argument('--check',action='store_true');args=parser.parse_args()
    base=Path(__file__).resolve().parent;project=base/'zhice-phase1'
    if not project.exists():raise SystemExit('请完整解压分享包后运行，不要在压缩包窗口中直接启动。')
    for name in ('data/warehouse.sqlite','data/lade/warehouse.sqlite'):
        path=project/name
        if not path.is_file():raise SystemExit('缺少数据库：'+name+'，请重新完整解压。')
        with sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True) as con:con.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone()
    if args.tools:
        for name in ('ZHICE_MODEL_URL','ZHICE_MODEL_KEY','ZHICE_MODEL_NAME'):os.environ.pop(name,None)
    else:
        config=json.loads((base/'团队模型配置.json').read_text(encoding='utf8'))
        for name,field in (('ZHICE_MODEL_URL','url'),('ZHICE_MODEL_KEY','key'),('ZHICE_MODEL_NAME','model')):
            if not isinstance(config.get(field),str) or not config[field]:raise SystemExit('团队模型配置不完整。')
            os.environ.setdefault(name,config[field])
    if args.check:
        print(json.dumps({'status':'passed','databases':2,'model_configured':bool(os.getenv('ZHICE_MODEL_KEY'))},ensure_ascii=False));return
    with socket.socket() as check:
        try:check.bind(('127.0.0.1',args.port))
        except OSError:raise SystemExit('端口已被使用。请先关闭旧服务，或运行 python 启动.py --port 8794')
    os.chdir(project);sys.path.insert(0,str(project));sys.dont_write_bytecode=True
    sys.argv=['智策','--port',str(args.port)]+([] if args.no_open else ['--open'])
    from zhice.agent_server import main as serve
    serve()

if __name__=='__main__':main()
'''

def hash_file(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()

def http_smoke(extracted):
    """从真正解压的副本启动，并禁用site-packages证明演示不依赖Pandas。"""
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def start(tools):
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        process=subprocess.Popen([sys.executable,'-S','-X','utf8','启动.py','--no-open','--port',str(port)]+(['--tools'] if tools else []),
            cwd=extracted,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        base=f'http://127.0.0.1:{port}'
        for _ in range(100):
            if process.poll() is not None:raise AssertionError('解压副本启动失败')
            try:
                with opener.open(base+'/api/four-agents/config',timeout=1) as response:config=json.load(response)
                return process,base,config
            except (OSError,urllib.error.URLError):time.sleep(.1)
        process.terminate();process.wait(timeout=10);raise AssertionError('解压副本启动超时')
    def stop(process):
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    def post(base,path,data,stream=False):
        request=urllib.request.Request(base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=90) as response:
            return [json.loads(line) for line in response if line.strip()] if stream else json.load(response)
    process,base,config=start(True)
    try:
        assert not config['model_configured']
        for path,marker in [('/', '协作决策工作台'),('/workspace.js','stage_started'),('/workspace.css','.agent-flow')]:
            with opener.open(base+path,timeout=10) as response:assert marker in response.read().decode('utf8')
        result=post(base,'/api/verify',{'month':'2018-01'})
        assert result['evidence']['status']=='passed' and result['evidence']['program']['order_count']==7269
        events=post(base,'/api/four-agents/stream',{'city':'hz','day':'07-15','courier_id':'6060','start_time':'09:00',
            'duration_minutes':120,'budget_points':50,'goal':'stability','mode':'tools'},True)
        assert [x['type'] for x in events]==['stage_started','stage_completed']*4+['verification_started','result']
        record=events[-1]['record'];r=record['result']
        assert r['status']=='passed' and r['model_calls']==0 and r['snapshot']['active_total']==31
        assert len(r['proposal']['initial_plan']['selected_ids'])==15 and r['proposal']['initial_plan']['cost_cents']==5000
        database=extracted/'zhice-phase1/data/lade/warehouse.sqlite'
        with closing(sqlite3.connect('file:'+database.as_posix()+'?mode=ro',uri=True)) as con:
            rows=con.execute(r['snapshot_evidence']['standalone_raw_reference']).fetchall();assert len(rows)==38
        with opener.open(base+'/api/four-agents/runs/0b247e0076944176bbd479f0d5356f7f',timeout=10) as response:
            saved=json.load(response);assert saved['record_verification']=='passed' and saved['result']['model_calls']==4
    finally:stop(process)
    process,base,config=start(False)
    try:assert config['model_configured'] and config['model']=='deepseek-v4-flash'
    finally:stop(process)
    return {'status':'passed','stdlib_only_startup':True,'olist_2018_01_orders':7269,
        'four_roles_stream_events':len(events),'lade_snapshot_accepted':38,'lade_snapshot_active':31,
        'plan_tasks':15,'plan_resource_points':50,'model_calls_in_smoke_test':0,
        'team_model_configuration_loaded':True,'saved_real_model_record_readable':True,'standalone_sql':'passed'}
