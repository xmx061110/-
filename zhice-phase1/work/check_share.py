"""从分享目录启动服务；-S 验证演示不依赖安装的第三方库。"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

share=Path(__file__).resolve().parents[2]/'智策物流助手_分享包'
proc=subprocess.Popen([sys.executable,'-S',str(share/'启动.py'),'--port','8791'],cwd=share,
    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    for attempt in range(30):
        try:
            with opener.open('http://127.0.0.1:8791/api/catalog',timeout=3) as r:catalog=json.load(r)
            break
        except OSError:
            if proc.poll() is not None:raise RuntimeError('分享包服务启动失败')
            time.sleep(0.3)
    else:raise RuntimeError('服务未就绪')
    for path,data in [('/api/verify',{'month':'2018-01'}),('/api/triage',{'month':'2018-01'})]:
        req=urllib.request.Request('http://127.0.0.1:8791'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=60) as r:result=json.load(r)
        if path=='/api/verify':assert result['evidence']['status']=='passed' and result['evidence']['program']['order_count']==7269
        else:assert result['status']=='passed' and result['checked_sellers']==970 and len(result['priority'])==10
    print('SHARE_OK: standard-library-only boot, catalog, 7269 orders, 970 seller checks, 10 priority rows')
finally:
    proc.terminate();proc.wait(timeout=10)
