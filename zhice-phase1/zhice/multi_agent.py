"""四角色物流排查：可信数据、模型诊断、模型建议、模型审核。"""
import json
import re
import urllib.request
from datetime import datetime, timezone
from .triage import build_triage

HYPOTHESES={'handoff':'核查商家交运记录与承诺交运时间', 'carrier':'核查承运环节的流转和异常记录', 'address':'核查收货地址和配送区域信息'}
ACTIONS={'check_handoff':'核对订单交运时间与交运期限', 'check_carrier':'调取承运商流转与异常记录', 'check_address':'核对地址和配送区域信息', 'contact_seller':'由工作人员联系商家核实情况'}
LIMIT='历史订单级统计不能证明延迟原因，也不能据此直接认定商家责任；建议均需人工核实。'

def model_json(role, instruction, facts, endpoint, key, model, transport=None):
    payload={'model':model,'temperature':0,'messages':[{'role':'system','content':instruction},
        {'role':'user','content':json.dumps(facts,ensure_ascii=False)}]}
    if transport:
        data=transport(role,payload)
    else:
        req=urllib.request.Request(endpoint,data=json.dumps(payload,ensure_ascii=False).encode(),
             headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=90) as response:data=json.load(response)
    content=data['choices'][0]['message']['content']
    if not isinstance(content,str) or len(content)>12000:raise ValueError(role+' 输出格式异常')
    content=re.sub(r'^\s*```(?:json)?\s*|\s*```\s*$','',content.strip(),flags=re.I)
    obj=json.loads(content)
    if not isinstance(obj,dict):raise ValueError(role+' 输出必须是对象')
    return obj

def run_workflow(database, month, category, endpoint, key, model, transport=None):
    evidence=build_triage(database,month,category,min_orders=30,limit=5)
    if evidence['status']!='passed':raise RuntimeError('数据复核失败')
    sellers=[{'seller':x['seller'],'late':x['metrics']['late_order_count'],
              'eligible':x['metrics']['delivery_eligible_order_count'],
              'multi_seller':x['metrics']['multi_seller_order_count']} for x in evidence['priority']]
    base={'filters':evidence['filters'],'sellers':sellers,'limitation':LIMIT}
    stages=[{'role':'数据智能体','status':'passed','method':'两条SQL分别复核历史数据','seller_count':evidence['checked_sellers']}]
    if not sellers:
        return {'status':'no_candidates','filters':evidence['filters'],'stages':stages,'evidence':evidence,'limitation':LIMIT}
    diag=model_json('diagnosis','你是诊断智能体。根据数据提出需要核实的方向，不作因果归责。仅输出JSON对象，格式 {"hypotheses":["handoff","carrier"]}。可选代码仅有 handoff、carrier、address；选1到3个，不要输出其他字段或文字。',base,endpoint,key,model,transport)
    hs=diag.get('hypotheses')
    if set(diag)!={'hypotheses'} or not isinstance(hs,list) or not 1<=len(hs)<=3 or any(not isinstance(x,str) or x not in HYPOTHESES for x in hs) or len(set(hs))!=len(hs):raise ValueError('诊断输出未通过校验')
    stages.append({'role':'诊断智能体','status':'passed','hypotheses':[{'code':x,'text':HYPOTHESES[x]} for x in hs]})
    plan=model_json('recommendation','你是建议智能体。基于待核查方向选择人工调查行动，不得自动执行。只输出JSON对象，格式 {"actions":["check_handoff","check_carrier"]}。可选代码仅有 check_handoff、check_carrier、check_address、contact_seller；选1到4个，不要其他字段或文字。',{'facts':base,'hypotheses':hs},endpoint,key,model,transport)
    acts=plan.get('actions')
    if set(plan)!={'actions'} or not isinstance(acts,list) or not 1<=len(acts)<=4 or any(not isinstance(x,str) or x not in ACTIONS for x in acts) or len(set(acts))!=len(acts):raise ValueError('建议输出未通过校验')
    stages.append({'role':'建议智能体','status':'passed','actions':[{'code':x,'text':ACTIONS[x]} for x in acts]})
    audit=model_json('review','你是审核智能体。检查建议是否仅为人工核实动作，不声称原因或商家责任已经确定。只输出JSON对象 {"approved":true} 或 {"approved":false}，不要其他字段或文字。',{'facts':base,'hypotheses':hs,'actions':acts,'meaning':ACTIONS},endpoint,key,model,transport)
    if set(audit)!={'approved'} or not isinstance(audit['approved'],bool):raise ValueError('审核输出未通过校验')
    stages.append({'role':'审核智能体','status':'approved' if audit['approved'] else 'rejected'})
    return {'status':'passed' if audit['approved'] else 'review_rejected','filters':evidence['filters'],
       'stages':stages,'evidence':evidence,'limitation':LIMIT,'model':model,'created_at':datetime.now(timezone.utc).isoformat()}
