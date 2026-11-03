"""本地选择式经营指标验证台；标准库 HTTP 服务，仅监听本机。"""
import argparse
import hashlib
import json
import mimetypes
import sqlite3
import sys
import urllib.parse
import urllib.request
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

from .demo_validation import open_readonly,verify_selection


def catalog(database,root):
    with closing(open_readonly(database)) as con:
        months=[dict(r) for r in con.execute("SELECT order_month AS value,COUNT(*) AS orders FROM fact_orders GROUP BY order_month ORDER BY order_month DESC")]
        categories=[dict(r) for r in con.execute("""SELECT COALESCE(p.product_category_name,'__unknown__') AS value,
            COALESCE(t.product_category_name_english,p.product_category_name,'未分类商品') AS label,
            COUNT(DISTINCT i.order_id) AS orders FROM clean_products p
            LEFT JOIN clean_category_translation t USING(product_category_name)
            JOIN clean_order_items i USING(product_id) GROUP BY value ORDER BY orders DESC,value""")]
        sellers=[dict(r) for r in con.execute("""SELECT s.seller_id AS value,s.seller_city AS city,s.seller_state AS state,
            COUNT(DISTINCT i.order_id) AS orders FROM clean_sellers s LEFT JOIN clean_order_items i USING(seller_id)
            GROUP BY s.seller_id ORDER BY orders DESC,s.seller_id""")]
        fingerprint=con.execute("SELECT value FROM build_metadata WHERE key='input_fingerprint'").fetchone()[0]
    digest=hashlib.sha256()
    with Path(database).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    batch_path=root/'reports/demo-batch-validation.json'
    batch=json.loads(batch_path.read_text(encoding='utf-8')) if batch_path.exists() else None
    if batch and batch.get('database_sha256')!=digest.hexdigest():batch=None
    return {"months":months,"categories":categories,"sellers":sellers,
            "database_sha256":digest.hexdigest(),"input_fingerprint":fingerprint,"batch_validation":batch}


def make_handler(root,database,options):
    static=root/'demo'
    allowed={"month":{r['value'] for r in options['months']},"category":{r['value'] for r in options['categories']},"seller":{r['value'] for r in options['sellers']}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,fmt,*args):
            print(f"[{self.log_date_time_string()}] {fmt % args}",flush=True)

        def send(self,status,body,kind='application/json; charset=utf-8',filename=None):
            if not isinstance(body,bytes):body=body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            if filename:self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass

        def json_response(self,status,value):
            self.send(status,json.dumps(value,ensure_ascii=False,allow_nan=False))

        def do_GET(self):
            host=self.headers.get('Host','').split(':')[0]
            if host not in {'127.0.0.1','localhost'}:
                self.json_response(403,{"error":"仅允许本机访问"});return
            url=urllib.parse.urlparse(self.path)
            try:
                if url.path=='/api/health':
                    self.json_response(200,{"service":"zhice-verifier","status":"ready","project_root":str(root)});return
                if url.path=='/api/catalog':
                    self.json_response(200,options);return
                if url.path in {'/api/verify','/api/export'}:
                    query=urllib.parse.parse_qs(url.query,keep_blank_values=True)
                    filters={}
                    for key in ('month','category','seller'):
                        values=query.get(key,[''])
                        if len(values)!=1:raise ValueError('筛选条件不能重复')
                        value=values[0] or None
                        if value is not None and value not in allowed[key]:raise ValueError('请选择列表中的月份、类别或商家')
                        filters[key]=value
                    result=verify_selection(database,**filters)
                    if url.path=='/api/verify':
                        self.json_response(200,result);return
                    if query.get('format',['json'])[0]=='sql':
                        content='-- 智策：同一筛选条件的生产查询与原始表独立复核\n-- 数据库：data/warehouse.sqlite；可在 SQLite 客户端直接运行。\n-- 筛选：'+json.dumps(filters,ensure_ascii=False)+'\nBEGIN;\n\n'
                        content+='-- 0. 前置检查：结果应为 0\n'+result['sql']['review_precheck']+'\n\n'
                        content+='-- 1. 程序使用的 SQL\n'+result['sql']['program']+'\n\n-- 2. 仅从原始表复算的 SQL\n'+result['sql']['reference']+'\n\nROLLBACK;\n'
                        self.send(200,content,'text/plain; charset=utf-8','zhice-verification.sql')
                    else:self.send(200,json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),'application/json; charset=utf-8','zhice-verification.json')
                    return
                names={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css'}
                if url.path in names:
                    path=static/names[url.path]
                    kind=mimetypes.guess_type(path)[0] or 'application/octet-stream'
                    self.send(200,path.read_bytes(),kind+'; charset=utf-8');return
                self.json_response(404,{"error":"页面不存在"})
            except ValueError as exc:self.json_response(400,{"error":str(exc)})
            except Exception as exc:
                print(f"查询失败：{type(exc).__name__}: {exc}",file=sys.stderr,flush=True)
                self.json_response(500,{"error":"查询或复核失败，请查看本地服务窗口。程序没有将其标记为通过。"})
    return Handler


def main():
    parser=argparse.ArgumentParser(description='智策本地经营指标验证台')
    parser.add_argument('--port',type=int,default=8787)
    parser.add_argument('--open',action='store_true')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args()
    root=args.root.resolve();database=root/'data/warehouse.sqlite'
    if not database.exists():raise SystemExit('未找到数据库，请先运行 python -m zhice all')
    url=f'http://127.0.0.1:{args.port}'
    # On Windows multiple SO_REUSEADDR listeners can bind the same port. Check
    # the existing service before binding, otherwise browsers can hang.
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url+'/api/health',timeout=2) as response:
            existing=json.load(response)
        if existing.get('service')=='zhice-verifier':
            if existing.get('project_root')!=str(root):
                raise SystemExit('此端口已有其他项目版本的智策验证台，请使用 --port 8788。')
            print('验证台已经运行：'+url,flush=True)
            if args.open:webbrowser.open(url)
            return
    except (OSError,ValueError):
        pass
    options=catalog(database,root)
    class ExclusiveServer(ThreadingHTTPServer):
        allow_reuse_address=False
    try:server=ExclusiveServer(('127.0.0.1',args.port),make_handler(root,database,options))
    except OSError:
        raise SystemExit('端口已占用，请使用 --port 8788 等其他端口。')
    print('智策验证台已启动：'+url,flush=True)
    print('每次选择均实时查询 SQLite，并独立复核原始表。关闭此窗口或按 Ctrl+C 停止。',flush=True)
    if args.open:webbrowser.open(url)
    try:server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
