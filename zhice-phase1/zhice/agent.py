"""单智能体：模型决定是否调用指标工具，所有数字由程序和数据库生成。"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .demo_validation import verify_selection

ROOT = Path(__file__).resolve().parents[1]
TOOL = {"type": "function", "function": {"name": "query_logistics_metrics", "description": "查询一个时间、类别、商家范围的订单和配送指标。仅当问题需要一个范围内的指标时调用；多范围比较、排行、预测、成本或原因分析不可调用。", "parameters": {"type": "object", "properties": {"month": {"type": "string", "description": "月份 YYYY-MM；不限定时省略"}, "category": {"type": "string", "description": "数据中的葡萄牙语商品类别代码，未确定时不要猜"}, "seller": {"type": "string", "description": "完整的 32 位商家 ID"}}, "additionalProperties": False}}}
SYSTEM = """你是物流经营分析助手，只处理 Olist 历史数据。需要一个范围的指标时调用 query_logistics_metrics。支持的指标包括订单量、已送达销售额、取消订单数、配送延迟率、低评分订单占比和商家履约代理指标；询问低评分占比或取消订单数可以正常调用。类别和商家必须有明确代码，不能猜测或省略用户提出的筛选；相对时间、未给年份、比较、排名、趋势、预测、成本、利润、原因等当前工具不能回答，说明限制并请求明确条件。工具仅支持完整购买月份、一个原始类别代码和一个完整商家ID。按天或日期区间、多个类别或商家、地区、额外筛选某个评分或订单状态、修改删除数据均不支持；例如只查询1星订单量需要拒绝，但查询低评分订单占比需要调用工具。不得把日范围扩大为整月，或忽略额外筛选。不得编造任何数据。调用工具时只输出工具调用。"""


def clarification():
    return {'status':'needs_clarification', 'answer':'当前问题无法安全映射为单范围查询。请提供完整购买月份（YYYY-MM）、一个类别原始代码或一个完整商家 ID；暂不支持日范围、额外条件、多范围比较、排行、原因、预测或利润。'}


def unsupported_granularity(question):
    # The database tool has month precision only. A day must never be silently dropped.
    return bool(re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}\s*月\s*\d{1,2}\s*[日号]|\d{1,2}\s*[日号]\s*(?:至|到|—|-)', question))


def metric_tool(database, args):
    if not isinstance(args, dict) or set(args) - {"month", "category", "seller"}:
        raise ValueError("工具参数不合法")
    for k, v in args.items():
        if not isinstance(v, str) or not v or len(v) > 100:
            raise ValueError(f"{k} 参数不合法")
    if "month" in args and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", args["month"]):
        raise ValueError("月份应为 YYYY-MM")
    if "category" in args and not re.fullmatch(r"[a-z0-9_]+", args["category"]):
        raise ValueError("商品类别应使用原始代码")
    if "seller" in args and not re.fullmatch(r"[a-f0-9]{32}", args["seller"]):
        raise ValueError("商家 ID 应为完整的 32 位代码")
    result = verify_selection(database, **args)
    if result["status"] != "passed":
        raise RuntimeError("原始数据复核未通过，已停止输出指标")
    return result


def render(result):
    p = result["program"]
    def rate(value):
        return "无法计算（没有有效分母）" if value is None else f"{value * 100:.2f}%"
    lines = ["筛选条件：" + json.dumps(result["filters"], ensure_ascii=False),
             "订单数：{}；已送达订单数：{}；取消订单数：{}".format(p['order_count'], p['delivered_order_count'], p['canceled_order_count']),
             "已送达商品销售额：{}.{:02d} BRL（不含运费）".format(p['sales_gmv_cents']//100, p['sales_gmv_cents']%100),
             "配送延迟率：{}（延迟 {} 单 / 有效配送 {} 单）".format(rate(p['delivery_late_rate']), p['late_order_count'], p['delivery_eligible_order_count']),
             "低评分占比：{}（低评分 {} 单 / 已送达且有评分 {} 单）".format(rate(p['low_score_rate']), p['low_score_order_count'], p['reviewed_delivered_order_count']),
             "单商家订单延迟交接代理指标：{}（{} / {}）".format(rate(p['late_handoff_proxy_rate']), p['single_seller_late_handoff_proxy_count'], p['single_seller_handoff_eligible_count']),
             "原始表独立复核：通过（{}/{} 项）；数据为历史记录，缺少真实成本，不能计算利润。".format(result['checks_passed'], result['check_count'])]
    if result['empty']:
        lines.insert(0, "没有符合条件的订单。")
    return "\n".join(lines)


def ask_model(question, endpoint, key, model, transport=None):
    payload = {"model": model, "messages": [{"role":"system", "content":SYSTEM}, {"role":"user", "content":question}], "tools": [TOOL], "tool_choice":"auto", "temperature":0}
    if transport:
        data = transport(payload)
    else:
        req = urllib.request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"}, method='POST')
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.load(response)
    msg = data['choices'][0]['message']
    calls = msg.get('tool_calls') or []
    if not calls or unsupported_granularity(question) or re.search(r'删除|修改|更新|写入|编造|伪造|最近|上个月|本月|今年|去年|本周|昨天|今天|上月', question):
        # Do not display unsupported model-authored numeric claims as data.
        return clarification()
    if len(calls) != 1 or calls[0].get('function', {}).get('name') != 'query_logistics_metrics':
        return clarification()
    try:
        args = json.loads(calls[0]['function']['arguments'])
    except (ValueError, TypeError, KeyError):
        return clarification()
    if not isinstance(args, dict) or set(args) - {'month','category','seller'}:
        return clarification()
    patterns = {'month':r'\d{4}-(0[1-9]|1[0-2])', 'category':r'[a-z0-9_]+', 'seller':r'[a-f0-9]{32}'}
    if any(not isinstance(v,str) or not re.fullmatch(patterns[k],v) for k,v in args.items()):
        return clarification()
    # Unconfirmed name-to-code mapping must not invent a category or seller ID.
    if any(k in args and args[k].lower() not in question.lower() for k in ('category','seller')):
        return clarification()
    return {"status":"tool_call", "arguments":args}


def main():
    parser = argparse.ArgumentParser(description="智策单智能体原型（模型选择工具，数据由 SQLite 计算并复核）")
    parser.add_argument('question', help='自然语言问题')
    parser.add_argument('--evidence', type=Path, help='保存完整结果及两份 SQL 的 JSON 文件')
    args = parser.parse_args()
    endpoint, key, model = (os.getenv(x) for x in ('ZHICE_MODEL_URL', 'ZHICE_MODEL_KEY', 'ZHICE_MODEL_NAME'))
    if not all((endpoint, key, model)):
        raise SystemExit('请配置 ZHICE_MODEL_URL、ZHICE_MODEL_KEY、ZHICE_MODEL_NAME；参见阶段二说明。')
    try:
        decision = ask_model(args.question, endpoint, key, model)
        if decision['status'] != 'tool_call':
            print(decision['answer']); return
        result = metric_tool(ROOT/'data/warehouse.sqlite', decision['arguments'])
        print(render(result))
        if args.evidence:
            args.evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print('证据已保存：', args.evidence)
    except (ValueError, KeyError, OSError, RuntimeError, urllib.error.URLError) as exc:
        print('调用或查询失败，未输出指标：', type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
