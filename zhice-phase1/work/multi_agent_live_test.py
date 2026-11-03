import getpass,json,os
from pathlib import Path
from zhice.agent import ROOT
from zhice.multi_agent import run_workflow

key=getpass.getpass('学校模型密钥：')
result=run_workflow(ROOT/'data/warehouse.sqlite','2018-01',None,'https://maas.bit.edu.cn/v1/chat/completions',key,'deepseek-v4-flash')
out=ROOT/'reports'/'多智能体真实调用样例.json'
out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print('status:',result['status'],'roles:',[x['role'] for x in result['stages']],'report:',out,flush=True)
