"""构建最新版团队可运行分享包，并从实际ZIP解压后验证；不打印模型密钥。"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

from work.four_agent_acceptance import model_config

ROOT=Path(__file__).resolve().parents[1]
WORKSPACE=ROOT.parent
NAME='智策_四Agent工作台_组员分享包_20261102'
WORK_NOTE='''给大家同步一下接入新数据后的进展：已经完成LaDe五城市真实物流数据的下载、完整性检查和独立建库，整理了约1065万条揽收与派送任务记录，与原来的Olist电商数据分别保存，并把真实接单、完成和时间窗信息接入历史事件回放。算法采用“GCD状态压缩＋Pareto剪枝的0-1背包预算优化”：用费用的最大公约数压缩预算刻度，删除花费更多却没有更高优先级收益的状态，在当前候选集和预算内精确选出关注任务；动态部分加入增益门槛、切换代价和冷却间隔，形成稳定重规划策略，控制收到反馈后频繁改方案，并与不重排、周期重排、即时重排进行同事件流对照，比较效用、计算开销和方案稳定性的取舍。工程上已接通总控、经营分析、优化决策、方案评审四个角色，重做了协作可视化工作台，支持查看实际执行状态、任务名单、策略图，以及原始SQL复核和证据下载。目前已有200个LaDe回放场景、800次策略运行的验证记录，最新版111项自动测试通过。当前优先级按截止时间规则计算，资源费用和关注行动仍为模拟，验证的是历史回放下的算法可行性与计算一致性，还不能表述为真实物流延误已经下降。'''

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

README='''# 智策四Agent工作台：先读这里

这是2026-11-02最新版团队可运行包，含完整Olist和LaDe数据库、源码、可视化页面、模型共享配置、报告及测试。不是之前的单智能体旧包。

## 启动

1. 安装Python 3.11或更新版本（推荐3.12），安装时勾选Add Python to PATH。
2. 将整个ZIP完整解压到本地文件夹。解压后约需6GB空间，建议至少预留12GB；不要在压缩包窗口内直接运行。
3. 双击“启动工作台.cmd”。浏览器打开 http://127.0.0.1:8793/ ，启动窗口保持打开，关闭窗口服务停止。
4. 已沿用队长授权的学校团队模型配置，网络需能访问BIT MaaS。此配置仅供已授权组员使用，请勿把含配置的包公开上传。
5. 没有模型网络时可双击“启动工具演练.cmd”，或在页面明确选择“仅工具演练”。工具演练不会调用模型。

仅打开工作台使用Python标准库，不需安装Pandas。只有重新处理Olist数据、训练或运行完整测试时，才需要在zhice-phase1目录执行 `python -m pip install -r requirements.txt`。

端口冲突时，先关闭旧启动窗口，或在本文件夹执行 `python 启动.py --port 8794`。命令行纯工具模式：`python 启动.py --tools --port 8794`。

## 建议先看这组演示

进入“协作工作台”，城市选杭州、月日07-15、快递员6060、预算50点；展开“回放设置”，开始时刻设09:00，时长120分钟。点击“开始四Agent协作”。四角色的执行状态来自后台事件；最终可看31笔待处理任务中的15笔初始关注建议、50点资源分配、四策略图及独立复核。模型可能拒绝或失败，以本次实际结果为准。

不想重新调用模型，可进入“证据与说明”，输入已有真实运行编号 `0b247e0076944176bbd479f0d5356f7f` 后点击“载入记录”。它会明确标注历史记录，不会假装正在调用模型。

## 四个页面与算法

- 协作工作台：总控、经营分析、优化决策、方案评审四角色；点击节点查看输入、处理、输出，查看建议、任务表和策略回放。
- 经营分析：Olist月份、类别、商家筛选，经营指标与原始SQL独立复核。
- 算法实验：历史风险实验、预算背包、反馈与重规划策略对照。
- 证据与说明：完整角色交接、原始SQL、回放记录、数据边界及历史运行载入。

预算工具为GCD刻度压缩与Pareto状态剪枝的0-1背包动态规划；稳定重规划通过新增任务切换代价、相对增益门槛与冷却间隔控制方案变动。LaDe用时间窗规则优先级，不把Olist风险模型直接迁移过去。详见“接入新数据后的工作说明.md”和项目docs目录。

## 数据包含范围

- 完整Olist数据库约328MB，九个原始CSV一并提供。
- 完整LaDe数据库约5.30GB，保留21,073,821条结构化原始记录、约1065万条任务记录及原始SQL复核所需表。所有已有城市、月日和快递员范围均可选择，并非抽样演示库。
- 为避免重复，未再附带约2.88GB的LaDe原始下载文件树；原始发布方README、来源版本、清单和下载代码已保留。如需从头重建LaDe，进入zhice-phase1后执行 `python -m zhice.lade_acquire --workers 3` 获取固定版本，再按docs中的建库步骤操作。
- 不包含Python安装程序或第三方模型服务本身。资料出处与两数据源各自的许可要求见 `zhice-phase1/DATA-ATTRIBUTION.md` 和 `data/lade/raw/README.md`。

## 验证与边界

本包包含当前源码的111项自动测试和本轮页面验收报告。ZIP另有SHA-256校验文件；包内可运行 `python 校验文件.py` 核对每个文件，完整校验会读取两个数据库，需要等待一段时间。`python 启动.py --check` 可快速检查环境和数据库。

四角色使用同一模型的4次独立调用；后续回放轮次由确定性算法处理。LaDe为真实历史记录，年份未知；资源费用和关注行动为模拟，不代表真实加急费用或实际降低延误。Olist与LaDe没有共同订单映射，未硬拼记录，未接入生产物流或华为云。

原开发环境的历史报告保留为证据，其中的本机路径和旧版代码哈希不代表当前组员电脑的路径或本轮版本。当前分享包内容以“文件校验清单.json”为准，压缩包验证见同目录的“分享包验收报告.json”。
'''

VERIFY=r'''import hashlib,json
from pathlib import Path
base=Path(__file__).resolve().parent
manifest=json.loads((base/'文件校验清单.json').read_text(encoding='utf8'))
for index,row in enumerate(manifest['files'],1):
    path=base/row['path']
    if not path.is_file() or path.stat().st_size!=row['bytes']:raise SystemExit('缺少或长度不同：'+row['path'])
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
    if h.hexdigest()!=row['sha256']:raise SystemExit('校验失败：'+row['path'])
    if index%50==0 or row['bytes']>100000000:print('已校验',index,'/',len(manifest['files']),flush=True)
print('全部文件SHA-256一致。运行工作台后，运行记录数据库会正常变化。')
'''


def hash_file(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,encoding='utf8')


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


def main():
    name=NAME;counter=1
    while (WORKSPACE/name).exists() or (WORKSPACE/(name+'.zip')).exists() or (WORKSPACE/(name+'.zip.part')).exists():
        counter+=1;name=NAME+'_'+str(counter)
    stage=WORKSPACE/name;app=stage/'zhice-phase1';app.mkdir(parents=True)
    assert stage.resolve().is_relative_to(WORKSPACE.resolve())
    ignore=shutil.ignore_patterns('__pycache__','*.pyc','.venv','.env','*.part','*.building.sqlite')
    for directory in ('zhice','agent_demo','demo','models','reports','tests','docs','examples'):
        shutil.copytree(ROOT/directory,app/directory,ignore=ignore)
    # 原始Olist与完整两数据库；LaDe不重复携带已导入库的大体积原始下载树。
    for directory in ('raw','provenance'):shutil.copytree(ROOT/'data'/directory,app/'data'/directory,ignore=ignore)
    for relative in ('data/warehouse.sqlite','data/lade/warehouse.sqlite'):
        target=app/relative;target.parent.mkdir(parents=True,exist_ok=True)
        print('复制完整数据库：'+relative,flush=True);shutil.copyfile(ROOT/relative,target)
    workflow=app/'data/workflow-runs.sqlite'
    with closing(sqlite3.connect('file:'+(ROOT/'data/workflow-runs.sqlite').as_posix()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(workflow)) as destination:source.backup(destination)
    shutil.copytree(ROOT/'data/lade/provenance',app/'data/lade/provenance',ignore=ignore)
    write(app/'data/lade/raw/README.md',(ROOT/'data/lade/raw/README.md').read_text(encoding='utf8'))
    for path in (ROOT/'data/lade').glob('*.txt'):shutil.copy2(path,app/'data/lade'/path.name)
    write(app/'data/lade/README.md','> 分享包包含完整LaDe数据库；原始下载树仅保留发布方README，其余文件可按固定版本重新下载。下文记录开发环境的完整下载过程。\n\n'+(ROOT/'data/lade/README.md').read_text(encoding='utf8'))
    for path in (ROOT/'work').glob('*.py'):
        (app/'work').mkdir(exist_ok=True);shutil.copy2(path,app/'work'/path.name)
    for name_in in ('README.md','requirements.txt','DATA-ATTRIBUTION.md'):shutil.copy2(ROOT/name_in,app/name_in)
    with (app/'DATA-ATTRIBUTION.md').open('a',encoding='utf8') as handle:handle.write('\n\n分享包范围：Olist九张原始CSV及完整两数据库已附带；LaDe大体积原始下载文件未重复附带，保留原始README、来源清单与固定版本下载脚本。\n')
    url,key,model=model_config(WORKSPACE/'智策物流助手_团队共享版_含密钥.zip')
    write(stage/'团队模型配置.json',json.dumps({'url':url,'model':model,'key':key},ensure_ascii=False,indent=2))
    write(stage/'先读这里.md',README);write(stage/'启动.py',BOOT);write(stage/'校验文件.py',VERIFY)
    write(stage/'接入新数据后的工作说明.md',WORK_NOTE+'\n')
    for filename,flag in [('启动工作台.cmd',''),('启动工具演练.cmd',' --tools')]:
        command='''@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
py -3 --version >nul 2>&1
if not errorlevel 1 goto use_py
python --version >nul 2>&1
if not errorlevel 1 goto use_python
echo Please install Python 3.11 or newer and add it to PATH.
pause
exit /b 1
:use_py
py -3 -X utf8 "启动.py"FLAG
goto finish
:use_python
python -X utf8 "启动.py"FLAG
:finish
if errorlevel 1 pause
'''.replace('FLAG',flag)
        (stage/filename).write_bytes(command.replace('\n','\r\n').encode('utf8'))
    # 密钥只能出现在用户已授权的团队配置文件，不散落在报告或源码中。
    token=re.compile(rb'gpustack_[0-9a-zA-Z_]{20,}|[a-f0-9]{32}\.[A-Za-z0-9]{16}')
    for path in stage.rglob('*'):
        if not path.is_file() or path.name=='团队模型配置.json' or path.suffix.lower() not in {'.py','.json','.jsonl','.md','.txt','.js','.html','.cmd','.css'}:continue
        content=path.read_bytes()
        if key.encode() in content or token.search(content):raise AssertionError('配置外发现密钥格式：'+str(path.relative_to(stage)))
    archive=WORKSPACE/(name+'.zip');partial=archive.with_suffix('.zip.part');manifest=[]
    files=sorted(path for path in stage.rglob('*') if path.is_file())
    print(f'开始压缩{len(files)}个文件（含完整LaDe库，使用ZIP64）',flush=True)
    with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as out:
        for path in files:
            relative=path.relative_to(stage).as_posix();info=zipfile.ZipInfo.from_file(path,name+'/'+relative)
            info.compress_type=zipfile.ZIP_DEFLATED;info._compresslevel=1;h=hashlib.sha256();copied=0;last=0
            with path.open('rb') as source,out.open(info,'w',force_zip64=True) as destination:
                for block in iter(lambda:source.read(8*1024*1024),b''):
                    h.update(block);destination.write(block);copied+=len(block)
                    if copied-last>=512*1024*1024:print(f'已压缩 {relative}：{copied/1024**3:.1f} GiB',flush=True);last=copied
            manifest.append({'path':relative,'bytes':path.stat().st_size,'sha256':h.hexdigest()})
        manifest_value={'version':'2026-11-02','contains_authorized_team_model_key':True,
            'data_scope':'完整Olist和LaDe数据库；Olist原始CSV；LaDe原始下载树不重复附带',
            'uncompressed_bytes':sum(row['bytes'] for row in manifest),'files':manifest}
        content=json.dumps(manifest_value,ensure_ascii=False,indent=2).encode('utf8')
        out.writestr(name+'/文件校验清单.json',content);(stage/'文件校验清单.json').write_bytes(content)
    print('压缩完成，逐文件检查CRC和SHA-256，并实际解压验证',flush=True)
    with zipfile.ZipFile(partial) as packed:
        assert len(packed.namelist())==len(set(packed.namelist()))
        expected={name+'/'+row['path']:row for row in manifest}
        for info in packed.infolist():
            assert not Path(info.filename).is_absolute() and '..' not in Path(info.filename).parts
            h=hashlib.sha256()
            with packed.open(info) as source:
                for block in iter(lambda:source.read(8*1024*1024),b''):h.update(block)
            if info.filename in expected:assert h.hexdigest()==expected[info.filename]['sha256']
            if info.file_size>100000000:print('校验通过：'+info.filename,flush=True)
        with tempfile.TemporaryDirectory(prefix='team-check-',dir=ROOT/'work') as temporary:
            temporary_path=Path(temporary).resolve();assert temporary_path.is_relative_to((ROOT/'work').resolve())
            # 临时目录严格位于workspace；退出时仅清理这个已验证的解压检查目录。
            packed.extractall(temporary_path);unpacked=temporary_path/name
            print('开始解压副本启动与数据库查询测试',flush=True);smoke=http_smoke(unpacked)
    os.replace(partial,archive)
    checksum=hash_file(archive);archive.with_suffix('.zip.sha256').write_text(checksum+'  '+archive.name+'\n',encoding='utf8')
    receipt={'status':'passed','checked_at_utc':datetime.now(timezone.utc).isoformat(),'archive':str(archive),
        'bytes':archive.stat().st_size,'sha256':checksum,'files':len(manifest)+1,
        'uncompressed_bytes':manifest_value['uncompressed_bytes'],'zip64':True,'zip_crc_and_all_file_sha256':'passed',
        'full_lade_database_bytes':(ROOT/'data/lade/warehouse.sqlite').stat().st_size,
        'full_olist_database_bytes':(ROOT/'data/warehouse.sqlite').stat().st_size,'includes_team_model_configuration':True,
        'extracted_smoke_test':smoke,'bulk_lade_raw_files_included':False,
        'notes':'原始表完整保留在LaDe数据库；原始下载文件树未重复附带。所有包内文件在实际启动前已按清单核验。'}
    archive.with_suffix('.验收报告.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
    (stage/'分享包验收报告.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
    (ROOT/'reports/team-share-package-acceptance.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({name:receipt[name] for name in ('status','archive','bytes','files','sha256')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
