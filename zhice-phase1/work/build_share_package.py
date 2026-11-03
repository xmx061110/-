"""Build and verify a self-contained project ZIP for sharing (Python required)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path

from zhice.demo_validation import compare_results

project=Path(__file__).resolve().parents[1]
workspace=project.parent
working=project/'work'
working.mkdir(exist_ok=True)
name='智策验证台_完整分享包'
destination=workspace/(name+'.zip')
counter=1
while destination.exists() or destination.with_suffix('.zip.part').exists():
    destination=workspace/f'{name}({counter}).zip'
    counter+=1
temporary=destination.with_suffix('.zip.part')
top='智策验证台'

launcher=r'''@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0zhice-phase1"
if errorlevel 1 goto fail
py -3 --version >nul 2>&1
if not errorlevel 1 goto use_py
python --version >nul 2>&1
if not errorlevel 1 goto use_python
echo Python 3 is required. Install Python 3.12 and select "Add Python to PATH".
pause
exit /b 1
:use_py
py -3 -X utf8 -m zhice.demo_server --open
goto finish
:use_python
python -X utf8 -m zhice.demo_server --open
:finish
if errorlevel 1 pause
exit /b %errorlevel%
:fail
echo Cannot find the project directory. Extract the whole ZIP before running.
pause
exit /b 1
'''.replace('\n','\r\n').encode('ascii')

instructions=(workspace/'验证台使用说明.md').read_text(encoding='utf-8')
instructions=instructions.replace('1. 打开桌面上的 `华为ict` 文件夹。','1. 将整个压缩包解压到一个文件夹，打开解压出的 `智策验证台` 文件夹。')
instructions=instructions.replace('程序运行期间请保留启动窗口；','请先安装 Python 3（推荐 Python 3.12），并在安装时将 Python 加入 PATH。\n\n程序运行期间请保留启动窗口；')
instructions+='\n## 完整项目内容\n\n本包同时包含原始 CSV、完整处理代码、SQLite 数据库、测试和报告。如需从头重建或运行全部测试，请阅读 `zhice-phase1/README.md`。\n'
instructions=instructions.encode('utf-8')

readme=(project/'README.md').read_text(encoding='utf-8')
readme=readme.replace('在上一级的“华为ict”文件夹双击','在上一级的“智策验证台”文件夹双击')
private_example="当前 Codex 环境可直接运行：\n\n```powershell\n& 'C:\\Users\\32257\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe' -m zhice all\n```\n\n"
assert private_example in readme
readme=readme.replace(private_example,'')
readme=readme.replace('上述命令应在本项目目录执行，其他电脑使用自己的 Python。','上述命令应在本项目目录执行。')

files=[]
for path in sorted(project.rglob('*')):
    if not path.is_file():continue
    relative=path.relative_to(project)
    if any(part in {'work','__pycache__','.venv','.git'} for part in relative.parts):continue
    if path.suffix in {'.pyc','.part'} or path.name.endswith('.building.sqlite'):continue
    if relative.as_posix()=='README.md':continue
    files.append((path,relative))

records=[]
def add_bytes(z,arc,content):
    z.writestr(arc,content)
    records.append({'file':arc.removeprefix(top+'/'),'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})

try:
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        add_bytes(z,f'{top}/打开智策验证台.cmd',launcher)
        add_bytes(z,f'{top}/验证台使用说明.md',instructions)
        add_bytes(z,f'{top}/zhice-phase1/README.md',readme.encode('utf-8'))
        for source,relative in files:
            arc=f'{top}/zhice-phase1/{relative.as_posix()}'
            z.write(source,arc)
            h=hashlib.sha256()
            with source.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
            records.append({'file':arc.removeprefix(top+'/'),'bytes':source.stat().st_size,'sha256':h.hexdigest()})
        add_bytes(z,f'{top}/分享包文件校验.json',(json.dumps({'algorithm':'SHA-256','files':records},ensure_ascii=False,indent=2)+'\n').encode('utf-8'))

    with zipfile.ZipFile(temporary) as z:
        assert z.testzip() is None,'ZIP CRC check failed'
        names=z.namelist()
        assert len(names)==len(set(names)),'duplicate archive entries'
        for info in z.infolist():
            name_path=Path(info.filename)
            assert not name_path.is_absolute() and '..' not in name_path.parts
        assert len([x for x in names if x.startswith(f'{top}/zhice-phase1/data/raw/') and x.endswith('.csv')])==9
        assert f'{top}/zhice-phase1/data/warehouse.sqlite' in names
        assert f'{top}/zhice-phase1/zhice/demo_server.py' in names
        assert f'{top}/zhice-phase1/reports/demo-batch-validation.json' in names
        assert b'C:\\Users\\32257' not in z.read(f'{top}/zhice-phase1/README.md')
        assert b'C:\\Users\\32257' not in z.read(f'{top}/打开智策验证台.cmd')

        with tempfile.TemporaryDirectory(prefix='share-check-',dir=working) as temporary_dir:
            extracted=Path(temporary_dir).resolve()
            assert extracted.is_relative_to(working.resolve())
            z.extractall(extracted)
            unpacked=extracted/top/'zhice-phase1'
            db=unpacked/'data/warehouse.sqlite'
            dbhash=hashlib.sha256()
            with db.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):dbhash.update(block)
            validation=json.loads((unpacked/'reports/validation.json').read_text(encoding='utf-8'))
            assert dbhash.hexdigest()==validation['database_sha256']
            check_code="from pathlib import Path; from zhice.demo_validation import verify_selection; r=verify_selection(Path('data/warehouse.sqlite'),month='2018-01'); assert r['status']=='passed' and r['program']['order_count']==7269 and r['program']['sales_gmv_cents']==92464500 and r['checks_passed']==21; print('stdlib-only verification passed')"
            process=subprocess.run([sys.executable,'-S','-X','utf8','-c',check_code],cwd=unpacked,capture_output=True,text=True,encoding='utf-8',timeout=45)
            assert process.returncode==0,(process.stdout,process.stderr)
            print(process.stdout.strip(),flush=True)

            # An ephemeral port avoids interfering with the user's running demo.
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            server=subprocess.Popen([sys.executable,'-S','-X','utf8','-m','zhice.demo_server','--port',str(port)],
                                    cwd=unpacked,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base=f'http://127.0.0.1:{port}'
            try:
                ready=False
                for _ in range(100):
                    if server.poll() is not None:raise RuntimeError(server.stderr.read().decode('utf-8',errors='replace'))
                    try:
                        with opener.open(base+'/api/health',timeout=1) as response:
                            health=json.load(response)
                        if health.get('service')=='zhice-verifier':ready=True;break
                    except (OSError,urllib.error.URLError):time.sleep(.2)
                assert ready,'unpacked demo server did not start'
                query=urllib.parse.urlencode({'month':'2018-01'})
                with opener.open(base+'/api/verify?'+query,timeout=30) as response:result=json.load(response)
                assert result['status']=='passed' and result['program']['order_count']==7269
                with opener.open(base+'/api/export?'+query+'&format=sql',timeout=30) as response:sql=response.read().decode('utf-8')
                statements=[];buffer=''
                for line in sql.splitlines(keepends=True):
                    buffer+=line
                    if sqlite3.complete_statement(buffer):statements.append(buffer);buffer=''
                assert not buffer.strip()
                outputs=[]
                with closing(sqlite3.connect(db)) as con:
                    con.row_factory=sqlite3.Row
                    for statement in statements:
                        cursor=con.execute(statement)
                        if cursor.description:outputs.append([dict(r) for r in cursor.fetchall()])
                assert len(outputs)==3 and outputs[0][0]['ambiguous_review_tie_groups']==0
                assert all(row['passed'] for row in compare_results(outputs[1][0],outputs[2][0]))
                print('unpacked HTTP and exported SQL passed',flush=True)
            finally:
                server.terminate()
                try:server.wait(timeout=10)
                except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
                if server.stderr:server.stderr.close()
    os.replace(temporary,destination)
    digest=hashlib.sha256()
    with destination.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    print(json.dumps({'status':'passed','zip':str(destination),'bytes':destination.stat().st_size,'sha256':digest.hexdigest(),
                      'csv_files':9,'included_files':len(records),'zip_crc':'passed','extracted_test':'passed'},ensure_ascii=False),flush=True)
except Exception:
    if temporary.exists():temporary.unlink()
    raise
