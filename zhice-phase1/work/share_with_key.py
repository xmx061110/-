import getpass
import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

base=Path(__file__).resolve().parents[2]
source=base/'智策物流助手_分享包.zip'
target=base/'智策物流助手_团队共享版_含密钥.zip'
if target.exists():raise SystemExit('目标已存在，未覆盖')
key=getpass.getpass('共享模型密钥：').strip()
if not key:raise SystemExit('未输入密钥')
root='智策物流助手_分享包/'
with zipfile.ZipFile(source) as src:
    manifest=json.loads(src.read(root+'文件校验清单.json'))
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as dst:
        for item in manifest:
            path=item['path']; data=src.read(root+path)
            if path=='启动.py':
                data=data.replace(b'    from zhice.agent_school import run',('    os.environ.setdefault("ZHICE_MODEL_KEY", '+repr(key)+')\n    from zhice.agent_school import run').encode())
            if path=='先读这里.md':
                s=data.decode('utf8').replace('按提示输入自己有权使用的北理工 BIT MaaS 密钥，输入时不显示。网络须能访问学校模型接口。不含任何预置密钥。','已包含团队共享的北理工 BIT MaaS 密钥，网络须能访问学校模型接口。此压缩包仅限项目组授权队员使用，请勿公开传播。')
                s=s.replace('密钥、Python本体和第三方在线模型服务不包含在包内。','团队共享密钥已包含在包内；Python本体和第三方在线模型服务不包含在包内。')
                data=s.encode('utf8')
            item['size']=len(data);item['sha256']=hashlib.sha256(data).hexdigest()
            dst.writestr(root+path,data)
        dst.writestr(root+'文件校验清单.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode())
with zipfile.ZipFile(target) as z:
    assert z.testzip() is None
    for item in manifest:assert hashlib.sha256(z.read(root+item['path'])).hexdigest()==item['sha256']
payload={'model':'deepseek-v4-flash','messages':[{'role':'user','content':'你好，请回复你好'}],'max_tokens':30}
req=urllib.request.Request('https://maas.bit.edu.cn/v1/chat/completions',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(req,timeout=60) as r:
        response=json.load(r); print('模型测试:', '成功' if response.get('choices') else '未获得有效回复')
except Exception as e:print('模型测试失败:',type(e).__name__)
print('打包与文件校验完成:',target)
