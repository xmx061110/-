"""智策团队工作台启动器；配置仅供已授权项目组使用。"""
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
        if not path.is_file():raise SystemExit('缺少数据库包：'+name+'，请把数据库包与程序包解压到同一个文件夹。')
        with sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True) as con:con.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone()
    if args.tools:
        for name in ('ZHICE_MODEL_URL','ZHICE_MODEL_KEY','ZHICE_MODEL_NAME'):os.environ.pop(name,None)
    else:
        config_path=base/'团队模型配置.json'
        if not config_path.is_file():raise SystemExit('请复制团队模型配置.example.json为团队模型配置.json并填写密钥，或双击启动工具演练.cmd。')
        config=json.loads(config_path.read_text(encoding='utf8'))
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
