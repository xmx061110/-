import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from zhice.demo_validation import compare_results

root=Path(__file__).resolve().parents[1]
db=root/'data/warehouse.sqlite'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
base='http://127.0.0.1:8787'

def get(path,params=None):
    url=base+path+('?' + urllib.parse.urlencode(params) if params else '')
    with opener.open(url,timeout=40) as r:
        return r.status,r.read()

_,payload=get('/api/catalog')
catalog=json.loads(payload)
assert catalog['batch_validation']['status']=='passed'
with sqlite3.connect(db) as con:
    seller=con.execute("SELECT i.seller_id FROM clean_order_items i JOIN clean_products p USING(product_id) JOIN fact_orders f USING(order_id) WHERE f.order_month='2018-01' AND p.product_category_name='beleza_saude' ORDER BY i.order_id LIMIT 1").fetchone()[0]
scenarios=[('all',{}),('month',{'month':'2018-01'}),('category',{'category':'beleza_saude'}),
           ('seller',{'seller':seller}),('combined',{'month':'2018-01','category':'beleza_saude','seller':seller}),
           ('empty',{'month':'2018-10','category':'beleza_saude','seller':seller})]
results=[]
for name,params in scenarios:
    status,payload=get('/api/verify',params)
    data=json.loads(payload)
    assert status==200 and data['status']=='passed' and data['checks_passed']==data['check_count']==21,(name,data)
    if name=='combined':
        assert data['program']['order_count']>0
        (root/'examples/interactive-case.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    if name=='empty':assert data['empty'] and data['program']['order_count']==0
    results.append({'case':name,'filters':params,'order_count':data['program']['order_count'],
                    'sales_gmv_cents':data['program']['sales_gmv_cents'],'checks_passed':data['checks_passed'],'status':data['status']})
combined=scenarios[4][1]
_,payload=get('/api/export',{**combined,'format':'sql'})
sql=payload.decode('utf-8')
(root/'examples/interactive-case.sql').write_text(sql,encoding='utf-8')
statements=[];buffer=''
for line in sql.splitlines(keepends=True):
    buffer+=line
    if sqlite3.complete_statement(buffer):statements.append(buffer);buffer=''
assert not buffer.strip()
outputs=[]
with sqlite3.connect(db) as con:
    con.row_factory=sqlite3.Row
    for statement in statements:
        cursor=con.execute(statement)
        if cursor.description:outputs.append([dict(row) for row in cursor.fetchall()])
assert len(outputs)==3 and outputs[0][0]['ambiguous_review_tie_groups']==0
assert len(outputs[1])==len(outputs[2])==1
assert all(row['passed'] for row in compare_results(outputs[1][0],outputs[2][0]))
try:
    get('/api/verify',{'month':'2018-13'})
    raise AssertionError('Invalid filter accepted')
except urllib.error.HTTPError as e:
    assert e.code==400
report={'status':'passed','cases':results,'exported_sql_executed_independently':True,
        'invalid_filter_http_400':True,'exhaustive_single_dimension_groups':catalog['batch_validation']['groups'],
        'exhaustive_single_dimension_metric_checks':catalog['batch_validation']['metric_checks']}
(root/'reports/demo-http-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
