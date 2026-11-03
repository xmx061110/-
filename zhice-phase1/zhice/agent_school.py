"""使用已授权的本机配置，或在当前进程中接收学校模型密钥。"""
import getpass
import os
from .agent_server import main
from .model_settings import resolve_model_config

def run():
    url,key,model=resolve_model_config()
    if not key:
        key=getpass.getpass('请输入学校模型 API 密钥（输入时不显示，直接回车结束）：').strip()
        if not key: raise SystemExit('没有输入密钥，未启动模型服务。')
    for name,value in zip(('ZHICE_MODEL_URL','ZHICE_MODEL_KEY','ZHICE_MODEL_NAME'),(url,key,model)):
        os.environ[name]=value
    main()

if __name__ == '__main__': run()
