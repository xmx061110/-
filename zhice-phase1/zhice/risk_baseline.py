"""可复现的逻辑回归历史实验，仅依赖 NumPy、Pandas。"""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
NUMERIC=['promised_days','purchase_weekday','purchase_hour','item_count','seller_count','price_cents','freight_cents','weight_g']
CATEGORICAL=['customer_state','seller_state','category']
QUERY="""WITH items AS (
 SELECT i.order_id,count(*) item_count,count(DISTINCT i.seller_id) seller_count,
 sum(i.price_cents) price_cents,sum(i.freight_value_cents) freight_cents,
 CASE WHEN count(DISTINCT s.seller_state)=1 THEN min(s.seller_state) ELSE 'MULTI' END seller_state,
 CASE WHEN count(DISTINCT COALESCE(p.product_category_name,'UNKNOWN'))=1
 THEN min(COALESCE(p.product_category_name,'UNKNOWN')) ELSE 'MULTI' END category,
 sum(p.product_weight_g) weight_g
 FROM clean_order_items i JOIN clean_sellers s ON s.seller_id=i.seller_id
 JOIN clean_products p ON p.product_id=i.product_id GROUP BY i.order_id)
 SELECT f.order_id,f.order_purchase_timestamp,f.order_estimated_delivery_date,
 f.order_delivered_customer_date,f.delivery_late,c.customer_state,i.item_count,i.seller_count,
 i.price_cents,i.freight_cents,i.seller_state,i.category,i.weight_g
 FROM fact_orders f JOIN clean_customers c ON c.customer_id=f.customer_id
 JOIN items i ON i.order_id=f.order_id WHERE f.delivery_eligible=1"""

def load_data(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as con:
        df=pd.read_sql_query(QUERY,con)
    for col in ('order_purchase_timestamp','order_estimated_delivery_date','order_delivered_customer_date'):
        df[col]=pd.to_datetime(df[col],errors='coerce')
    df['promised_days']=(df.order_estimated_delivery_date-df.order_purchase_timestamp).dt.total_seconds()/86400
    df['purchase_weekday']=df.order_purchase_timestamp.dt.dayofweek
    df['purchase_hour']=df.order_purchase_timestamp.dt.hour
    for col in NUMERIC:df[col]=pd.to_numeric(df[col],errors='coerce')
    for col in CATEGORICAL:df[col]=df[col].fillna('UNKNOWN').astype(str)
    return df

def encode(train,frames):
    means=train[NUMERIC].mean(); std=train[NUMERIC].std().replace(0,1).fillna(1)
    cats={c:sorted(train[c].unique().tolist()) for c in CATEGORICAL}
    names=['intercept']+NUMERIC+[f'{c}={v}' for c in CATEGORICAL for v in cats[c]]
    encoded=[]
    for df in frames:
        numbers=((df[NUMERIC].fillna(means)-means)/std).clip(-5,5).to_numpy(dtype=np.float32)
        blocks=[np.ones((len(df),1),dtype=np.float32),numbers]
        for c in CATEGORICAL:
            values=pd.Index(cats[c]).get_indexer(df[c])
            block=np.zeros((len(df),len(cats[c])),dtype=np.float32)
            rows=np.where(values>=0)[0];block[rows,values[rows]]=1
            blocks.append(block)
        encoded.append(np.concatenate(blocks,axis=1))
    return encoded,{'means':means.to_dict(),'std':std.to_dict(),'categories':cats,'names':names}

def sigmoid(z):return 1/(1+np.exp(-np.clip(z,-30,30)))

def fit(x,y,iterations=250):
    w=np.zeros(x.shape[1],dtype=np.float32)
    w[0]=np.log(y.mean()/(1-y.mean()))
    for _ in range(iterations):
        gradient=x.T@(sigmoid(x@w)-y)/len(y)
        gradient[1:]+=0.002*w[1:]
        w-=0.2*gradient
    return w

def metrics(frame,scores):
    y=frame.delivery_late.astype(int).to_numpy();p=np.asarray(scores)
    n=len(y);positive=int(y.sum());negative=n-positive
    if not positive or not negative:raise ValueError('评估集中必须同时包含延迟和未延迟')
    ranks=pd.Series(p).rank(method='average').to_numpy()
    auc=(ranks[y==1].sum()-positive*(positive+1)/2)/(positive*negative)
    order=np.argsort(-p,kind='stable');ys=y[order]
    ap=float(((np.cumsum(ys)/np.arange(1,n+1))*ys).sum()/positive)
    k=max(1,int(np.ceil(n*.1)));hits=int(ys[:k].sum())
    return {'orders':n,'late_orders':positive,'late_rate':round(float(y.mean()),6),
            'roc_auc':round(float(auc),6),'average_precision':round(ap,6),
            'brier_score':round(float(np.mean((p-y)**2)),6),
            'top10_count':k,'top10_late_count':hits,'top10_precision':round(hits/k,6),'top10_recall':round(hits/positive,6)}

def main():
    db=ROOT/'data/warehouse.sqlite';df=load_data(db);p=df.order_purchase_timestamp;d=df.order_delivered_customer_date
    train=df[(p<'2018-05-01')&(d<'2018-05-01')].copy()
    valid=df[(p>='2018-05-01')&(p<'2018-07-01')&(d<'2018-07-01')].copy()
    test=df[(p>='2018-07-01')&(p<'2018-09-01')&(d<'2018-10-18')].copy()
    if min(map(len,[train,valid,test]))<100:raise RuntimeError('样本不足')
    (xtrain,xvalid,xtest),encoding=encode(train,[train,valid,test])
    w=fit(xtrain,train.delivery_late.to_numpy(dtype=np.float32))
    ps=sigmoid(xtest@w);vs=sigmoid(xvalid@w)
    out=ROOT/'models';out.mkdir(exist_ok=True)
    preds=test[['order_id','order_purchase_timestamp','order_estimated_delivery_date','delivery_late']].copy()
    preds['predicted_late_probability']=ps
    preds.sort_values('predicted_late_probability',ascending=False).to_csv(out/'historical-risk-test.csv',index=False,encoding='utf-8-sig')
    (out/'delivery-risk-logistic.json').write_text(json.dumps({'weights':w.tolist(),'encoding':encoding},ensure_ascii=False,indent=2),encoding='utf-8')
    h=hashlib.sha256()
    with db.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    report={'model':'NumPy 逻辑回归基线','data_sha256':h.hexdigest(),'features':NUMERIC+CATEGORICAL,
        'decision_time_assumption':'下单后、配送前；假设预计送达日期与商品和收货信息可获得，尚待业务验证',
        'train':{'purchase_before':'2018-05-01','delivered_before':'2018-05-01','orders':len(train),'late_rate':float(train.delivery_late.mean())},
        'validation':{'purchase_range':'2018-05-01 至 2018-06-30','delivered_before':'2018-07-01',**metrics(valid,vs)},
        'test':{'purchase_range':'2018-07-01 至 2018-08-31','delivered_before':'2018-10-18',**metrics(test,ps)},
        'random_selection_expected_top10_precision':float(test.delivery_late.mean()),
        'limitations':['只包括截止前已经送达且时间有效的订单，有选择偏差，不能代表取消和未送达订单',
                       '订单时预计送达日期是否可用为假设，必须业务验证',
                       '历史巴西数据不能证明当前或其他地区表现；没有实时数据及真实干预效果',
                       '风险评分未做独立概率校准']}
    (ROOT/'reports/delivery-risk-evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
