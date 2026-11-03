"""将已验证团队版本拆成程序包与数据库包；两包解压到相同目录即可运行。"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
import os

from work.share_support import ROOT,WORKSPACE,http_smoke,hash_file,BOOT
from zhice.model_settings import resolve_model_config

SOURCE=ROOT
OUTPUT=WORKSPACE/'分享给组员'
TOP='智策四Agent工作台'
INTRO='接入LaDe真实物流数据后，我们已完成独立建库、预算选单与反馈重规划，并接通总控、经营分析、优化决策、方案评审四个角色。现在可以在可视化工作台查看协作过程、关注任务和策略对照，并用SQL复核、下载证据；当前仍是历史回放与模拟资源分配。'

README='''# 智策四Agent工作台：组员启动说明

## 两个包怎么放

请下载“智策_智能体程序包_20261102.zip”和“智策_数据库包_20261102.zip”，将两包都解压到同一个位置。两者的顶层文件夹都叫“智策四Agent工作台”，应合并为同一个文件夹，不要嵌套成两层。

最终结构应为：

```text
智策四Agent工作台/
  启动工作台.cmd
  启动工具演练.cmd
  启动.py
  团队模型配置.json
  zhice-phase1/
    zhice/                   程序源码
    agent_demo/              最新可视化页面
    data/
      warehouse.sqlite       Olist完整数据库
      workflow-runs.sqlite   已保存的运行记录
      lade/warehouse.sqlite  LaDe完整数据库
```

## 启动

安装Python 3.11或更新版本（推荐3.12，勾选Add Python to PATH），再双击“启动工作台.cmd”。浏览器会打开 http://127.0.0.1:8793/ ，窗口保持打开。仅演示不需要安装Pandas。两包解压合计约6GB，建议预留12GB空间。

已包含队长授权的团队模型配置，只供授权组员使用；模型调用需要学校接口网络可达。无需模型时可双击“启动工具演练.cmd”。端口冲突时关闭旧窗口，或执行 `python 启动.py --port 8794`。

## 怎么看

进入协作工作台，选择杭州、07-15、快递员6060、预算50点，展开回放设置后选09:00起120分钟，点击开始四Agent协作。也可以到证据与说明输入 `0b247e0076944176bbd479f0d5356f7f`，载入已保存的真实模型运行结果。

程序包包含新版工作台、四角色代码、算法、模型文件、测试、报告和团队配置；数据库包完整保留两套分析数据库、原始表、已有运行记录，以及Olist九张原始CSV。LaDe约2.88GB的原始下载文件树不重复附带，可按固定版本下载脚本补齐。组员以后只更新程序包即可继续使用这套数据。

首次运行前可执行 `python 校验文件.py` 核对两个包的所有清单文件。完整校验会读取大数据库，需等待；开始运行后，运行记录数据库会正常改变，之后再校验它会提示变化。

算法原理、实际参数和对照结果见“算法与优化说明.md”；可直接发群的短介绍见“组员简短介绍.md”。两份数据的来源及许可分别见项目DATA-ATTRIBUTION.md和LaDe原始README。

LaDe优先级为截止时间规则，费用与关注行动为模拟；不声称已经降低真实物流延误。四角色使用同一模型的独立调用，未接生产物流或华为云。
'''

VERIFY='''import hashlib,json
from pathlib import Path
base=Path(__file__).resolve().parent
for name in ('程序包文件校验.json','数据库包文件校验.json'):
    path=base/name
    if not path.is_file():raise SystemExit('缺少'+name+'，请把两包解压到同一文件夹。')
    rows=json.loads(path.read_text(encoding='utf8'))['files']
    for index,row in enumerate(rows,1):
        file=base/row['path']
        if not file.is_file() or file.stat().st_size!=row['bytes']:raise SystemExit('缺少或长度变化：'+row['path'])
        h=hashlib.sha256()
        with file.open('rb') as handle:
            for chunk in iter(lambda:handle.read(8*1024*1024),b''):h.update(chunk)
        if h.hexdigest()!=row['sha256']:raise SystemExit('内容变化：'+row['path'])
        if index%50==0 or row['bytes']>100000000:print(name,index,'/',len(rows),flush=True)
print('两个包的所有清单文件校验通过。')
'''


def algorithm_note():
    report=json.loads((ROOT/'reports/lade-replay-evaluation.json').read_text(encoding='utf8'))['summary']
    a=report['aggregate'];immediate,stable=a['immediate'],a['stable']
    lines=['# 当前算法及优化说明','',
        '## 1. 单轮预算：GCD状态压缩 + Pareto剪枝的0-1背包动态规划','',
        '每笔候选任务都有模拟资源费用c和截止时间规则优先级v。在总预算B内选择任务，最大化优先级总分：max Σ(vᵢxᵢ)，约束Σ(cᵢxᵢ)≤B，xᵢ∈{0,1}。',
        'GCD压缩：把费用和预算按费用最大公约数缩放，预算向下取整；例如内部预算5000、费用100/200/500，转成容量50和费用1/2/5。它减少状态数量，不改变原预算下的可行解。',
        'Pareto剪枝：删除花费更多但优先级总分没有更高的状态；同成本保留更高分方案。当前候选集和给定整数目标下仍可精确求解。','',
        '## 2. 反馈处理：带门槛的稳定重规划','',
        '每5分钟更新当前可见任务，新增接单进入候选，已完成任务退出，费用只在模拟执行时记账。稳定策略对新加入方案的任务扣除效用切换代价，只有净增益超过门槛且达到冷却间隔才按普通反馈调整；初始规划和空队列补充是例外。',
        '当前LaDe参数：相对增益门槛2%；每个新加入方案的任务扣500效用单位；接受重排后至少间隔2轮，即10分钟。500不是资源点，也不是货币。',
        '这些是LaDe预先指定的规则参数；旧Olist模拟实验使用的10%/5000参数属于另一个实验，不能混用。','',
        '## 3. 当前LaDe对照结果','',
        '200个固定历史回放场景、4种策略共800次运行；以下为每场景均值。','',
        '|指标|即时重排|稳定重排|变化|','|---|---:|---:|---:|']
    for key,label in [('mean_plan_edit_count','方案增删项'),('mean_solver_calls','求解次数'),('mean_utility_units','规则效用')]:
        change=100*(stable[key]/immediate[key]-1)
        lines.append(f'|{label}|{immediate[key]:,.3f}|{stable[key]:,.3f}|{change:+.2f}%|')
    lines.extend(['','当前结果是用部分规则效用换取更少的求解和方案变动，并非所有指标都提升。单轮精确最优不代表整个动态过程全局最优；没有真实干预费用和效果，不能据此声称实际延误下降。','',
        '源码：zhice/budget_optimizer.py、zhice/rolling_optimizer.py、zhice/lade_replay.py；完整证据：reports/lade-replay-evaluation.json。'])
    return '\n'.join(lines)+'\n'


def main():
    assert SOURCE.is_dir()
    OUTPUT.mkdir(exist_ok=True)
    archives={'程序':OUTPUT/'智策_智能体程序包_20261102.zip','数据库':OUTPUT/'智策_数据库包_20261102.zip'}
    for path in archives.values():assert not path.exists() and not path.with_suffix('.zip.part').exists(),'目标已存在，未覆盖'
    replacements={'先读这里.md':README.encode('utf8'),'校验文件.py':VERIFY.encode('utf8'),
        '组员简短介绍.md':(INTRO+'\n').encode('utf8'),'算法与优化说明.md':algorithm_note().encode('utf8')}
    boot=BOOT.replace("'缺少数据库：'+name+'，请重新完整解压。'","'缺少数据库包：'+name+'，请把数据库包与程序包解压到同一个文件夹。'")
    replacements['启动.py']=boot.encode('utf8')
    url,key,model=resolve_model_config()
    if not key:raise ValueError('请先配置本地团队模型。')
    replacements['团队模型配置.json']=json.dumps({'url':url,'key':key,'model':model},ensure_ascii=False,indent=2).encode('utf8')
    for title in ('启动工作台.cmd','启动工具演练.cmd'):
        replacements[title]=(ROOT/'work/templates'/title).read_bytes()
    sources={'程序':[],'数据库':[]}
    for folder,dirs,names in os.walk(SOURCE):
      dirs[:]=[name for name in dirs if name not in {'__pycache__','.git','.venv','archives','test-temp'}]
      for filename in sorted(names):
        file=Path(folder)/filename;source_relative=file.relative_to(SOURCE).as_posix()
        if file.suffix in {'.pyc','.part'} or file.name.endswith('.building.sqlite') or source_relative.startswith('config/'):continue
        if source_relative.startswith('work/') and not (file.parent==ROOT/'work' and file.suffix=='.py' or source_relative.startswith('work/templates/')):continue
        if source_relative.startswith('data/lade/raw/') and source_relative!='data/lade/raw/README.md':continue
        relative='zhice-phase1/'+source_relative
        part='数据库' if relative.startswith('zhice-phase1/data/') else '程序'
        sources[part].append((relative,file))
    manifests={}
    for part,archive in archives.items():
        records=[];partial=archive.with_suffix('.zip.part')
        print('开始构建'+part+'包',flush=True)
        with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as packed:
            for relative,file in sources[part]:
                if file.stat().st_size>100000000:print('压缩：'+relative,flush=True)
                packed.write(file,TOP+'/'+relative)
                records.append({'path':relative,'bytes':file.stat().st_size,'sha256':hash_file(file)})
            if part=='程序':
                for relative,content in replacements.items():
                    packed.writestr(TOP+'/'+relative,content)
                    records.append({'path':relative,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})
            manifest_name='程序包文件校验.json' if part=='程序' else '数据库包文件校验.json'
            packed.writestr(TOP+'/'+manifest_name,json.dumps({'part':part,'files':records},ensure_ascii=False,indent=2).encode('utf8'))
        partial.replace(archive);manifests[part]=records
        print(part+'包已写入，开始CRC与SHA-256核验',flush=True)
        with zipfile.ZipFile(archive) as packed:
            assert len(packed.namelist())==len(set(packed.namelist()))
            for row in records:
                h=hashlib.sha256()
                with packed.open(TOP+'/'+row['path']) as stream:
                    for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
                assert h.hexdigest()==row['sha256']
        checksum=hash_file(archive);archive.with_suffix('.zip.sha256').write_text(checksum+'  '+archive.name+'\n',encoding='utf8')
        print(part+'包校验通过：'+str(archive.stat().st_size)+'字节',flush=True)
    assert archives['程序'].stat().st_size<1000000000
    with tempfile.TemporaryDirectory(prefix='split-check-',dir=ROOT/'work') as temporary:
        temp=Path(temporary).resolve();assert temp.is_relative_to((ROOT/'work').resolve())
        with zipfile.ZipFile(archives['程序']) as packed:packed.extractall(temp)
        unpacked=temp/TOP
        result=subprocess.run([sys.executable,'-S','-X','utf8','启动.py','--check'],cwd=unpacked,capture_output=True,text=True,encoding='utf8',timeout=30)
        assert result.returncode!=0 and '数据库包' in result.stdout+result.stderr
        print('仅程序包的缺库提示通过；合并解压数据库包继续测试',flush=True)
        with zipfile.ZipFile(archives['数据库']) as packed:packed.extractall(temp)
        smoke=http_smoke(unpacked)
    report={'status':'passed','checked_at_utc':datetime.now(timezone.utc).isoformat(),'shared_top_directory':TOP,
        'program_package_under_1gb':True,'program_only_missing_data_message':'passed','merged_extracted_smoke_test':smoke,
        'parts':{part:{'archive':str(path),'bytes':path.stat().st_size,'sha256':hash_file(path),
            'manifest_files':len(manifests[part]),'crc_and_all_file_sha256':'passed'} for part,path in archives.items()},
        'data_scope':'保留完整两套分析数据库、原始表、运行记录和Olist原始CSV；LaDe原始下载树未重复附带。',
        'size_boundary':'1GB限制用于程序包；完整数据库单独分发，数据库包仍大于1GB。'}
    (ROOT/'reports/split-share-package-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (OUTPUT/'智策_分包验收报告_20261102.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (OUTPUT/'智策_算法与优化说明.md').write_bytes(replacements['算法与优化说明.md'])
    (OUTPUT/'智策_给组员的简短介绍.md').write_bytes(replacements['组员简短介绍.md'])
    print(json.dumps({'status':'passed','parts':report['parts'],'merged_smoke':'passed'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
