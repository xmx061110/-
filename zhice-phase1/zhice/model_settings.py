"""解析本机既有模型配置；环境变量优先，不依赖历史分享压缩包。"""
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def resolve_model_config(config_path=None):
    path=Path(config_path) if config_path is not None else ROOT/'config/团队模型配置.json'
    local={}
    if path.is_file():
        try:
            local=json.loads(path.read_text(encoding='utf8'))
            if not isinstance(local,dict):raise ValueError()
        except (ValueError,OSError):raise ValueError('本地团队模型配置无法读取，请检查配置文件。') from None
    values=tuple(os.getenv(name) or local.get(field) or default for name,field,default in (
        ('ZHICE_MODEL_URL','url','https://maas.bit.edu.cn/v1/chat/completions'),
        ('ZHICE_MODEL_KEY','key',None),('ZHICE_MODEL_NAME','model','deepseek-v4-flash')))
    if any(value is not None and not isinstance(value,str) for value in values):
        raise ValueError('模型配置字段须为字符串。')
    return values
