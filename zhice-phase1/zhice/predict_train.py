"""按时间划分的 Olist 配送延迟回顾实验；结果不是实时配送服务。"""
import hashlib
import json
import sys
from pathlib import Path
from contextlib import closing
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.prediction_deps'))
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss
import joblib

QUERY="""
WITH items AS (
 SELECT i.order_id, count(*) item_count, count(DISTINCT i.seller_id) seller_count,
        sum(i.price_cents) price_cents, sum(i.freight_value_cents) freight_cents,
        CASE WHEN count(DISTINCT s.seller_state)=1 THEN min(s.seller_state) ELSE 'MULTI' END seller_state,
        CASE WHEN count(DISTINCT COALESCE(p.product_category_name,'UNKNOWN'))=1
             THEN min(COALESCE(p.product_category_name,'UNKNOWN')) ELSE 'MULTI' END category,
        sum(p.product_weight_g) weight_g
 FROM clean_order_items i JOIN clean_sellers s ON s.seller_id=i.seller_id
 JOIN clean_products p ON p.product_id=i.product_id GROUP BY i.order_id
)
SELECT f.order_id,f.order_purchase_timestamp,f.order_estimated_delivery_date,
       f.order_delivered_customer_date,f.delivery_late,c.customer_state,
       i.item_count,i.seller_count,i.price_cents,i.freight_cents,i.seller_state,i.category,i.weight_g
FROM fact_orders f JOIN clean_customers c ON c.customer_id=f.customer_id
JOIN items i ON i.order_id=f.order_id WHERE f.delivery_eligible=1
"""
NUMERIC=['promised_days','purchase_weekday','purchase_hour','item_count','seller_count','price_cents','freight_cents','weight_g']
CATEGORICAL=['customer_state','seller_state','category']
FEATURES=NUMERIC+CATEGORICAL

def load_data(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as con:
        df=pd.read_sql_query(QUERY,con)
    for col in ('order_purchase_timestamp','order_estimated_delivery_date','order_delivered_customer_date'):
        df[col]=pd.to_datetime(df[col],errors='coerce')
    df['promised_days']=(df.order_estimated_delivery_date-df.order_purchase_timestamp).dt.total_seconds()/86400
    df['purchase_weekday']=df.order_purchase_timestamp.dt.dayofweek
    df['purchase_hour']=df.order_purchase_timestamp.dt.hour
    for col in NUMERIC: df[col]=pd.to_numeric(df[col],errors='coerce')
    for col in CATEGORICAL: df[col]=df[col].fillna('UNKNOWN').astype(str)
    return df

def prepare(df, categories):
    x=df[FEATURES].copy()
    for col in CATEGORICAL: x[col]=pd.Categorical(x[col],categories=categories[col])
    return x

def metrics(frame,scores):
    y=frame.delivery_late.astype(int).to_numpy()
    scores=np.asarray(scores,dtype=float)
    k=max(1,int(np.ceil(len(y)*.1)))
    top=np.argsort(-scores,kind='stable')[:k]
    return {'orders':len(y),'late_orders':int(y.sum()),'late_rate':round(float(y.mean()),6),
            'roc_auc':round(float(roc_auc_score(y,scores)),6),
            'average_precision':round(float(average_precision_score(y,scores)),6),
            'brier_score':round(float(brier_score_loss(y,scores)),6),
            'top10_count':k,'top10_late_count':int(y[top].sum()),
            'top10_precision':round(float(y[top].mean()),6),
            'top10_recall':round(float(y[top].sum()/y.sum()),6)}

def main():
    database=ROOT/'data/warehouse.sqlite'; df=load_data(database)
    purchase=df.order_purchase_timestamp; delivered=df.order_delivered_customer_date
    # 只使用切分时间前已经观察到的送达标签。
    train=df[(purchase<'2018-05-01') & (delivered<'2018-05-01')].copy()
    valid=df[(purchase>='2018-05-01') & (purchase<'2018-07-01') & (delivered<'2018-07-01')].copy()
    test=df[(purchase>='2018-07-01') & (purchase<'2018-09-01') & (delivered<'2018-10-18')].copy()
    if min(len(train),len(valid),len(test))<100:raise RuntimeError('样本不足，停止训练')
    cats={c:sorted(train[c].unique().tolist()) for c in CATEGORICAL}
    model=lgb.LGBMClassifier(n_estimators=200,learning_rate=.05,num_leaves=15,min_child_samples=100,
                              random_state=42,verbosity=-1,n_jobs=2)
    model.fit(prepare(train,cats),train.delivery_late.astype(int),categorical_feature=CATEGORICAL)
    scores=model.predict_proba(prepare(test,cats))[:,1]
    v_scores=model.predict_proba(prepare(valid,cats))[:,1]
    base=np.full(len(test),train.delivery_late.astype(int).mean())
    test_metrics=metrics(test,scores)
    model_dir=ROOT/'models'; model_dir.mkdir(exist_ok=True)
    report_dir=ROOT/'reports';report_dir.mkdir(exist_ok=True)
    preds=test[['order_id','order_purchase_timestamp','order_estimated_delivery_date','delivery_late']].copy()
    preds['predicted_late_probability']=scores
    preds=preds.sort_values('predicted_late_probability',ascending=False)
    preds.to_csv(model_dir/'historical-risk-test.csv',index=False,encoding='utf-8-sig')
    joblib.dump({'model':model,'categories':cats,'features':FEATURES},model_dir/'delivery-risk.joblib')
    digest=hashlib.sha256()
    with database.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
    summary={'data_sha256':digest.hexdigest(),'model':'LightGBM','seed':42,
             'feature_names':FEATURES,'label':'实际送达日期晚于预计日期（按日）',
             'decision_time_assumption':'下单后、配送前；假设预计送达日期及商品与收货地信息可获得，需业务验证',
             'train':{'purchase_before':'2018-05-01','delivered_before':'2018-05-01','orders':len(train),'late_rate':round(float(train.delivery_late.astype(int).mean()),6)},
             'validation':{'purchase_range':'2018-05-01 至 2018-06-30','delivered_before':'2018-07-01',**metrics(valid,v_scores)},
             'test':{'purchase_range':'2018-07-01 至 2018-08-31','delivered_before':'2018-10-18',**test_metrics},
             'constant_baseline_test':metrics(test,base),
             'random_selection_expected_top10_precision':round(float(test.delivery_late.astype(int).mean()),6),
             'limitations':['仅含已送达、时间有效且在观察截止前完成的订单，不能代表全部订单；时间截断可能带来选择偏差',
                            '下单时部分字段是否可用是未验证的业务假设；没有实时订单或配送轨迹',
                            '不包含取消、未送达订单，不能据此评价那些订单风险',
                            '历史巴西数据不能证明对当前或其他地区适用；没有真实干预成本和效果'],
             'versions':{'lightgbm':lgb.__version__,'pandas':pd.__version__}}
    (report_dir/'delivery-risk-evaluation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
