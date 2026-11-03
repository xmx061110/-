"""按白名单构建分享包，不复制环境配置、缓存或凭据。"""
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT.parent/'智策物流助手_分享包'

README='''# 智策物流助手：先读这里

这是可在 Windows 本机运行的单智能体演示，使用 Olist 历史电商订单。压缩包请先完整解压，不要在压缩包窗口内直接双击启动。

## 启动

1. 安装 Python 3.11 或更新版本（python.org 官方安装器，安装时勾选 Add Python to PATH）。本地演示只用 Python 标准库，不需要安装 requirements.txt 中的数据处理依赖。
2. 双击「启动演示.cmd」，浏览器自动打开 http://127.0.0.1:8788/ 。启动窗口保持打开；关闭窗口会停止服务。
3. 如需自然语言提问，双击「启动学校模型.cmd」，按提示输入自己有权使用的北理工 BIT MaaS 密钥，输入时不显示。网络须能访问学校模型接口。不含任何预置密钥。
4. 其他模型可在终端设置 ZHICE_MODEL_URL（完整 chat/completions HTTPS 地址）、ZHICE_MODEL_NAME、ZHICE_MODEL_KEY，再运行 python 启动.py --school。密钥不需要写进文件。
5. 如果8788端口已占用，在此文件夹打开终端运行 python 启动.py --port 8791，或先关闭旧服务。

没有模型密钥时，也可以直接使用页面筛选、SQL复核和排查清单。

## 能做什么

- 按购买月份、商品类别、商家及其组合，查询订单量、销售额、配送延迟率、低评分占比、商家履约代理指标。
- 自然语言模式由模型提取条件，需本人确认筛选项，再点击查询；最终数字由数据库计算。
- 每次指标查询都用原始表独立SQL复核，页面可查看SQL并下载证据。
- 第⑤部分可选月份和类别生成历史延迟商家排查清单，显示样本量、延迟订单数、比例和核查建议。
- 第④部分展示一个已完成的历史延迟预测实验，实验模型效果有限，不能作为实时业务能力宣传。

## 两分钟展示顺序

先选2018-01，点「查询并独立复核」，展示数字和SQL；然后到第⑤部分点「生成排查清单」。若配置了模型，可以先问「2018年1月订单配送延迟率是多少？」，确认月份后查询。可再问「利润是多少？」观察其提示能力限制。

## 目前边界

Olist是2016—2018年的历史数据，没有实时车辆轨迹和真实企业成本；不能计算真实利润、确定延迟原因或证明某商家应当负责。当前自然语言接口不支持排行、趋势比较、按天查询等额外条件；页面的历史排查按钮是独立确定性工具。多智能体协作与华为云接入尚未完成。

200道固定开发回归题的最终轮为198/200，其中两道类别题被保守拒绝；这不是整体准确率或独立盲测成绩。详细题目与三轮结果见 zhice-phase1/reports/200题真实模型验收报告.md。密钥、Python本体和第三方在线模型服务不包含在包内。

## 文件与复现

zhice-phase1/zhice：完整Python源码；agent_demo：助手页面；data：数据库及原始CSV；reports：报告与证据；tests：自动测试；work：验收脚本。

在 zhice-phase1 文件夹执行 python -m pip install -r requirements.txt 后，执行 python -m unittest discover -s tests -v 可运行完整测试。完整测试和重新处理CSV需要pandas；仅启动演示不需要。数据来源和CC BY-NC-SA 4.0条款见 DATA-ATTRIBUTION.md，需保留署名、非商业和相同方式共享要求。
'''

BOOT='''import argparse, os, sys
from pathlib import Path
if sys.version_info < (3,11):
    raise SystemExit('需要 Python 3.11 或更新版本')
p=argparse.ArgumentParser(); p.add_argument('--school',action='store_true'); p.add_argument('--port',type=int,default=8788); a=p.parse_args()
root=Path(__file__).resolve().parent/'zhice-phase1'; os.chdir(root); sys.path.insert(0,str(root))
sys.argv=['zhice', '--port', str(a.port), '--open']
if a.school:
    from zhice.agent_school import run
    run()
else:
    from zhice.agent_server import main
    main()
'''

def main():
    if TARGET.exists():raise SystemExit('分享目录已存在，避免覆盖；请另选名称')
    app=TARGET/'zhice-phase1';app.mkdir(parents=True)
    for name in ('zhice','agent_demo','demo','data','models','reports','tests','docs','examples'):
        shutil.copytree(ROOT/name,app/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (app/'work').mkdir()
    for name in ('phase2_acceptance.py','evaluate_200.py'):
        shutil.copy2(ROOT/'work'/name,app/'work'/name)
    for name in ('README.md','requirements.txt','DATA-ATTRIBUTION.md'):
        shutil.copy2(ROOT/name,app/name)
    (TARGET/'先读这里.md').write_text(README,encoding='utf8')
    (TARGET/'启动.py').write_text(BOOT,encoding='utf8')
    for title,flag in [('启动演示.cmd',''),('启动学校模型.cmd',' --school')]:
        command='''@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "启动.py"FLAG
  goto end
)
where python >nul 2>nul
if %errorlevel%==0 (
  python "启动.py"FLAG
  goto end
)
echo 请先安装 Python 3.11 或更新版本，并勾选 Add Python to PATH。
:end
echo 服务已停止；若有错误请查看上面的提示。
pause
'''.replace('FLAG',flag)
        (TARGET/title).write_bytes(command.replace('\n','\r\n').encode('utf8'))
    manifest=[]
    for path in sorted(TARGET.rglob('*')):
        if not path.is_file():continue
        if path.suffix.lower() in {'.py','.md','.json','.jsonl','.js','.html','.cmd','.txt'}:
            content=path.read_text(encoding='utf-8-sig')
            if 'gpustack_' in content or 'cDZU7nd' in content:raise RuntimeError('发现敏感内容，停止打包')
        with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        manifest.append({'path':path.relative_to(TARGET).as_posix(),'size':path.stat().st_size,'sha256':digest})
    (TARGET/'文件校验清单.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    archive=TARGET.with_suffix('.zip')
    if archive.exists():raise SystemExit('压缩包已存在，避免覆盖')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in TARGET.rglob('*'):
            if path.is_file():z.write(path,path.relative_to(TARGET.parent))
    with zipfile.ZipFile(archive) as z:
        if z.testzip():raise RuntimeError('压缩包校验失败')
    with archive.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    archive.with_suffix('.zip.sha256').write_text(digest+'  '+archive.name,encoding='utf8')
    print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'files':len(manifest)+1,'sha256':digest},ensure_ascii=False))

if __name__=='__main__':main()
