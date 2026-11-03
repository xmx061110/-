"""智策本机单智能体验证界面，模型仅负责提取查询条件。"""
import argparse
import csv
import json
import os
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .agent import ROOT, ask_model, metric_tool, render
from .demo_server import catalog
from .triage import build_triage
from .multi_agent import run_workflow
from .budget_optimizer import scenario
from .feedback_replay import demo as feedback_demo
from .rolling_demo import scenario as rolling_scenario
from .lade_query import catalog as lade_catalog, verified_metrics as lade_metrics
from .lade_replay import scenario as lade_scenario
from .four_agents import run as run_four_agents, RunStore, validated_request


def handler_for(root, database, options):
    allowed = {k: {r['value'] for r in options[name]} for k, name in
               (('month', 'months'), ('category', 'categories'), ('seller', 'sellers'))}
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, payload, content_type='application/json; charset=utf-8'):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            host = self.headers.get('Host', '').lower()
            allowed_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            origin = self.headers.get('Origin')
            return host in allowed_hosts and (not origin or origin in {f'http://{h}' for h in allowed_hosts})

        def do_GET(self):
            if not self.local_request(): return self.reply(403, {'error':'仅允许本机访问'})
            path = urllib.parse.urlsplit(self.path).path
            if path == '/api/catalog':
                return self.reply(200, {k:options[k] for k in ('months', 'categories', 'sellers')})
            if path == '/api/four-agents/config':
                ready = all(os.getenv(k) for k in ('ZHICE_MODEL_URL','ZHICE_MODEL_KEY','ZHICE_MODEL_NAME'))
                return self.reply(200, {'model_configured': ready,
                    'model': os.getenv('ZHICE_MODEL_NAME') if ready else None,
                    'roles': ['总控Agent','经营分析Agent','优化决策Agent','方案评审Agent']})
            if path.startswith('/api/four-agents/runs/'):
                try:
                    return self.reply(200, RunStore(root/'data/workflow-runs.sqlite').read(path.rsplit('/',1)[-1]))
                except ValueError as exc: return self.reply(400, {'error': str(exc)})
            if path == '/api/lade/catalog':
                if not (root/'data/lade/warehouse.sqlite').exists():
                    return self.reply(404,{'error':'LaDe数据库尚未导入'})
                query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                try:
                    if set(query)-{'city','day'}:raise ValueError('LaDe目录仅支持城市和月日')
                    return self.reply(200,lade_catalog(root/'data/lade/warehouse.sqlite',query.get('city',['jl'])[0],query.get('day',[None])[0]))
                except ValueError as exc:return self.reply(400,{'error':str(exc)})
            if path == '/api/risk':
                report=root/'reports/delivery-risk-evaluation.json'
                predictions=root/'models/historical-risk-test.csv'
                if not report.exists() or not predictions.exists():
                    return self.reply(404, {'error':'预测实验尚未完成，请先训练模型。'})
                with predictions.open(encoding='utf-8-sig',newline='') as f:
                    rows=[]
                    for row in csv.DictReader(f):
                        rows.append(row)
                        if len(rows)>=20:break
                return self.reply(200, {'report':json.loads(report.read_text(encoding='utf-8')),'top_orders':rows})
            files = {'/':'index.html', '/app.js':'app.js', '/style.css':'style.css',
                     '/workspace.js':'workspace.js', '/workspace.css':'workspace.css'}
            if path not in files: return self.reply(404, {'error':'未找到页面'})
            kind = {'/':'text/html', '/app.js':'text/javascript', '/style.css':'text/css',
                    '/workspace.js':'text/javascript', '/workspace.css':'text/css'}[path]
            return self.reply(200, (root/'agent_demo'/files[path]).read_bytes(), kind+'; charset=utf-8')

        def do_POST(self):
            if not self.local_request(): return self.reply(403, {'error':'仅允许本机访问'})
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 8192: raise ValueError('请求长度不合法')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict): raise ValueError('请求格式不合法')
                if self.path in ('/api/four-agents', '/api/four-agents/stream'):
                    config = None
                    if data.get('mode','model') == 'model':
                        url,key,model = (os.getenv(k) for k in ('ZHICE_MODEL_URL','ZHICE_MODEL_KEY','ZHICE_MODEL_NAME'))
                        if not all((url,key,model)): raise ValueError('尚未配置模型；可选择仅工具演练，或用学校模型启动方式运行')
                        parsed = urllib.parse.urlsplit(url)
                        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1','localhost','::1'}):
                            raise ValueError('模型地址须为HTTPS或本机HTTP')
                        config = (url,key,model)
                    if self.path.endswith('/stream'):
                        validated_request(root/'data/lade/warehouse.sqlite', data)
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.close_connection = True
                        connected = True
                        def publish(event):
                            nonlocal connected
                            if connected:
                                try:
                                    self.wfile.write((json.dumps(event,ensure_ascii=False)+'\n').encode('utf8'))
                                    self.wfile.flush()
                                except OSError: connected = False
                        try:
                            record = run_four_agents(root/'data/lade/warehouse.sqlite',
                                root/'data/workflow-runs.sqlite', data, config, progress=publish)
                            publish({'type':'result','record':record})
                        except Exception:
                            publish({'type':'error','message':'协作执行失败，请检查服务后重试。'})
                        return
                    return self.reply(200, run_four_agents(root/'data/lade/warehouse.sqlite',
                        root/'data/workflow-runs.sqlite', data, config))
                if self.path == '/api/feedback-demo':
                    if data:raise ValueError('回放示例不接受自定义事件')
                    return self.reply(200,feedback_demo())
                if self.path == '/api/lade/replay':
                    if set(data)-{'city','day','courier_id','budget_points','start_time','duration_minutes'}:
                        raise ValueError('LaDe回放包含不支持的筛选字段')
                    return self.reply(200,lade_scenario(root/'data/lade/warehouse.sqlite',data.get('city'),data.get('day'),
                        data.get('courier_id'),data.get('budget_points',50),data.get('start_time'),data.get('duration_minutes',120)))
                if self.path == '/api/lade/metrics':
                    if set(data)-{'kind','city','day','courier_id'}:raise ValueError('LaDe指标包含不支持的字段')
                    return self.reply(200,lade_metrics(root/'data/lade/warehouse.sqlite',data.get('kind','pickup'),
                        data.get('city'),data.get('day'),data.get('courier_id')))
                if self.path == '/api/rolling':
                    if set(data)-{'day','budget_cents'}:raise ValueError('重排演示仅支持日期和预算')
                    return self.reply(200,rolling_scenario(root,data.get('day'),data.get('budget_cents')))
                if self.path == '/api/budget':
                    if set(data)-{'day','budget_cents'}:raise ValueError('预算演示仅支持日期和预算')
                    budget=data.get('budget_cents')
                    if type(budget) is not int or not 0<=budget<=500000:raise ValueError('演示预算范围为0到500000分')
                    return self.reply(200,scenario(root/'models/historical-risk-test.csv',data.get('day'),budget))
                if self.path in ('/api/triage','/api/multi-agent'):
                    if set(data)-{'month','category'}:raise ValueError('排查清单仅支持月份和类别')
                    if not isinstance(data.get('month'),str) or data['month'] not in allowed['month']:raise ValueError('请选择数据集中的完整月份')
                    category=data.get('category')
                    if category is not None and (not isinstance(category,str) or category not in allowed['category']):
                        raise ValueError('类别不在数据集中')
                    if self.path == '/api/triage':return self.reply(200,build_triage(database,data['month'],category))
                    url,key,model=(os.getenv(k) for k in ('ZHICE_MODEL_URL','ZHICE_MODEL_KEY','ZHICE_MODEL_NAME'))
                    if not all((url,key,model)):return self.reply(400,{'error':'尚未配置模型，请用学校模型启动方式运行。'})
                    parsed=urllib.parse.urlsplit(url)
                    if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost','::1'}):raise ValueError('模型地址须为 HTTPS 或本机 HTTP')
                    return self.reply(200,run_workflow(database,data['month'],category,url,key,model))
                if self.path == '/api/interpret':
                    question = data.get('question')
                    if not isinstance(question,str) or not question.strip() or len(question)>500: raise ValueError('请输入 1–500 字的问题')
                    url, key, model = (os.getenv(k) for k in ('ZHICE_MODEL_URL','ZHICE_MODEL_KEY','ZHICE_MODEL_NAME'))
                    if not all((url,key,model)): return self.reply(400, {'error':'尚未配置模型。请参照本地运行说明配置；也可直接选择条件查询。'})
                    parsed = urllib.parse.urlsplit(url)
                    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1','localhost','::1'}):
                        raise ValueError('模型地址须为 HTTPS，或本机 HTTP 地址')
                    decision = ask_model(question, url, key, model)
                    return self.reply(200, decision)
                if self.path == '/api/verify':
                    if set(data) - {'month','category','seller'}: raise ValueError('存在不支持的筛选字段')
                    selected = {}
                    for k in ('month','category','seller'):
                        v = data.get(k)
                        if v == '': v = None
                        if v is not None and (not isinstance(v,str) or v not in allowed[k]): raise ValueError('筛选值不在数据集中')
                        if v is not None: selected[k] = v
                    result = metric_tool(database, selected)
                    return self.reply(200, {'answer':render(result),'evidence':result})
                return self.reply(404, {'error':'未知接口'})
            except ValueError as exc:
                return self.reply(400, {'error':str(exc)})
            except Exception as exc:
                print('请求失败:',type(exc).__name__,flush=True)
                return self.reply(500, {'error':'模型调用或数据库复核失败；未输出结果。请检查本地运行窗口与配置。'})
    return Handler


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8788)
    parser.add_argument('--open',action='store_true')
    args=parser.parse_args()
    database=ROOT/'data/warehouse.sqlite'
    if not database.exists(): raise SystemExit('未找到第一阶段数据库')
    options=catalog(database,ROOT)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(ROOT,database,options))
    url=f'http://127.0.0.1:{args.port}'
    print('智策单智能体验证台：'+url,flush=True)
    if args.open: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
