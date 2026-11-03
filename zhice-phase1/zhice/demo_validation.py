"""交互验证：生产指标 SQL 与只读原始表 SQL 在同一快照中独立计算。"""
import json
import hashlib
import math
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .metrics import metric_query

LABELS = {
    "order_count": "订单量", "delivered_order_count": "已送达订单", "canceled_order_count": "取消订单",
    "unavailable_order_count": "不可用订单", "non_canceled_available_order_count": "非取消且可用订单",
    "item_count": "命中商品件数", "sales_gmv_cents": "已送达商品成交额（分）",
    "delivered_freight_cents": "已送达收取运费（分）", "delivered_observed_payment_cents": "已送达观察付款（分）",
    "delivered_missing_payment_order_count": "已送达缺少付款记录订单", "delivery_eligible_order_count": "有效配送分母",
    "late_order_count": "配送延迟订单", "delivery_late_rate": "配送延迟率",
    "reviewed_delivered_order_count": "已送达有效评分分母", "low_score_order_count": "低评分订单",
    "low_score_rate": "低评分订单占比", "multi_seller_order_count": "涉及多商家订单",
    "single_seller_handoff_eligible_count": "单商家有效交运分母",
    "single_seller_late_handoff_proxy_count": "单商家晚交运代理订单", "late_handoff_proxy_rate": "单商家晚交运代理率",
    "delivered_status_share": "已送达快照占比",
}
RATES = {"delivery_late_rate", "low_score_rate", "late_handoff_proxy_rate", "delivered_status_share"}
TIE_AUDIT_SQL = """SELECT COUNT(*) AS ambiguous_review_tie_groups FROM (
  SELECT order_id, review_answer_timestamp, review_creation_date, review_id
  FROM raw_order_reviews
  GROUP BY order_id, review_answer_timestamp, review_creation_date, review_id
  HAVING COUNT(DISTINCT review_score)>1
);"""


def cents_sql(column):
    # Input contract: validated nonnegative decimal strings with <= 2 places.
    # Parse integer and fractional strings separately, avoiding binary floats.
    return f"(CAST({column} AS INTEGER)*100 + CAST(substr(CASE WHEN instr({column},'.')=0 THEN '00' ELSE substr({column},instr({column},'.')+1)||'00' END,1,2) AS INTEGER))"


def reference_query(month=None, category=None, seller=None, group_by="none"):
    if group_by not in {"none", "month", "category", "seller"}:
        raise ValueError("无效分组")
    item_group = {"category": "COALESCE(p.product_category_name,'__unknown__')", "seller": "i.seller_id"}.get(group_by,"'all'")
    group_value = {"category": "s.item_group", "seller": "s.item_group", "month": "substr(o.order_purchase_timestamp,1,7)"}.get(group_by,"'all'")
    require_items = "1" if group_by in {"category", "seller"} else "0"
    payment_total = ("SUM(CASE WHEN order_status='delivered' THEN order_paid END)"
                     if category is None and seller is None and group_by in {"none", "month"} else "NULL")
    sql = f"""-- 独立复核：仅使用 raw_* 原始表，不读取 clean_* 或 fact_*。
-- 金额拆成整数部分与小数部分转分，避免浮点舍入。
-- 评价并列键的评分冲突数必须为 0（先执行前置检查）。
WITH filter_values(month,category,seller) AS (VALUES (?,?,?)),
items AS (
 SELECT DISTINCT order_id,order_item_id,product_id,seller_id,shipping_limit_date,price,freight_value
 FROM raw_order_items
), products AS (
 SELECT DISTINCT product_id,product_category_name FROM raw_products
), payments AS (
 SELECT DISTINCT order_id,payment_sequential,payment_type,payment_installments,payment_value
 FROM raw_order_payments
), payment_totals AS (
 SELECT order_id,SUM({cents_sql('payment_value')}) AS order_paid
 FROM payments GROUP BY order_id
), all_item_stats AS (
 SELECT order_id,COUNT(DISTINCT seller_id) AS seller_count,
        MIN(datetime(shipping_limit_date)) AS first_deadline
 FROM items GROUP BY order_id
), selected_items AS (
 SELECT i.order_id,{item_group} AS item_group,COUNT(*) AS selected_item_count,
        SUM({cents_sql('i.price')}) AS selected_price,
        SUM({cents_sql('i.freight_value')}) AS selected_freight
 FROM items i JOIN products p ON i.product_id=p.product_id CROSS JOIN filter_values v
 WHERE (v.category IS NULL OR COALESCE(p.product_category_name,'__unknown__')=v.category)
   AND (v.seller IS NULL OR i.seller_id=v.seller)
 GROUP BY i.order_id,item_group
), review_records AS (
 SELECT DISTINCT review_id,order_id,review_score,review_comment_title,review_comment_message,
        review_creation_date,review_answer_timestamp
 FROM raw_order_reviews
), ranked_reviews AS (
 SELECT order_id,CAST(review_score AS INTEGER) AS score,
 ROW_NUMBER() OVER(PARTITION BY order_id ORDER BY datetime(review_answer_timestamp) DESC,
   datetime(review_creation_date) DESC,review_id DESC,CAST(review_score AS INTEGER) DESC) AS rn
 FROM review_records
 WHERE CAST(review_score AS INTEGER) BETWEEN 1 AND 5
   AND CAST(review_score AS REAL)=CAST(review_score AS INTEGER)
), base AS (
 SELECT o.order_id,o.order_status,{group_value} AS group_value,
        datetime(o.order_purchase_timestamp) AS purchased,
        datetime(o.order_delivered_customer_date) AS arrived,
        datetime(o.order_estimated_delivery_date) AS expected,
        datetime(o.order_delivered_carrier_date) AS handed,
        a.seller_count,a.first_deadline,COALESCE(s.selected_item_count,0) AS selected_item_count,
        s.selected_price,s.selected_freight,t.order_paid,r.score
 FROM raw_orders o CROSS JOIN filter_values v
 LEFT JOIN selected_items s ON s.order_id=o.order_id
 LEFT JOIN all_item_stats a ON a.order_id=o.order_id
 LEFT JOIN payment_totals t ON t.order_id=o.order_id
 LEFT JOIN ranked_reviews r ON r.order_id=o.order_id AND r.rn=1
 WHERE (v.month IS NULL OR substr(o.order_purchase_timestamp,1,7)=v.month)
   AND ((v.category IS NULL AND v.seller IS NULL AND {require_items}=0) OR s.order_id IS NOT NULL)
), flags AS (
 SELECT *,CASE WHEN order_status='delivered' AND purchased IS NOT NULL AND arrived IS NOT NULL
   AND expected IS NOT NULL AND arrived>=purchased AND expected>=purchased
   AND (handed IS NULL OR arrived>=handed) THEN 1 ELSE 0 END AS delivery_ok,
 CASE WHEN seller_count=1 AND order_status NOT IN ('canceled','unavailable') AND handed IS NOT NULL
   AND purchased IS NOT NULL AND first_deadline IS NOT NULL AND handed>=purchased
   AND first_deadline>=purchased THEN 1 ELSE 0 END AS handoff_ok
 FROM base
), totals AS (
 SELECT group_value,COUNT(*) AS order_count,
 SUM(CASE WHEN order_status='delivered' THEN 1 ELSE 0 END) AS delivered_order_count,
 SUM(CASE WHEN order_status='canceled' THEN 1 ELSE 0 END) AS canceled_order_count,
 SUM(CASE WHEN order_status='unavailable' THEN 1 ELSE 0 END) AS unavailable_order_count,
 SUM(CASE WHEN order_status NOT IN ('canceled','unavailable') THEN 1 ELSE 0 END) AS non_canceled_available_order_count,
 SUM(selected_item_count) AS item_count,
 COALESCE(SUM(CASE WHEN order_status='delivered' THEN selected_price ELSE 0 END),0) AS sales_gmv_cents,
 COALESCE(SUM(CASE WHEN order_status='delivered' THEN selected_freight ELSE 0 END),0) AS delivered_freight_cents,
 {payment_total} AS delivered_observed_payment_cents,
 SUM(CASE WHEN order_status='delivered' AND order_paid IS NULL THEN 1 ELSE 0 END) AS delivered_missing_payment_order_count,
 SUM(delivery_ok) AS delivery_eligible_order_count,
 SUM(CASE WHEN delivery_ok=1 AND date(arrived)>date(expected) THEN 1 ELSE 0 END) AS late_order_count,
 SUM(CASE WHEN order_status='delivered' AND score BETWEEN 1 AND 5 THEN 1 ELSE 0 END) AS reviewed_delivered_order_count,
 SUM(CASE WHEN order_status='delivered' AND score<=2 THEN 1 ELSE 0 END) AS low_score_order_count,
 SUM(CASE WHEN seller_count>1 THEN 1 ELSE 0 END) AS multi_seller_order_count,
 SUM(handoff_ok) AS single_seller_handoff_eligible_count,
 SUM(CASE WHEN handoff_ok=1 AND handed>first_deadline THEN 1 ELSE 0 END) AS single_seller_late_handoff_proxy_count
 FROM flags GROUP BY group_value
)
SELECT *,1.0*late_order_count/NULLIF(delivery_eligible_order_count,0) AS delivery_late_rate,
 1.0*low_score_order_count/NULLIF(reviewed_delivered_order_count,0) AS low_score_rate,
 1.0*single_seller_late_handoff_proxy_count/NULLIF(single_seller_handoff_eligible_count,0) AS late_handoff_proxy_rate,
 1.0*delivered_order_count/NULLIF(order_count,0) AS delivered_status_share
FROM totals ORDER BY group_value"""
    return sql,[month,category,seller]


def open_readonly(database):
    con=sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro",uri=True,timeout=30)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("BEGIN")
    return con


def compare_results(program,reference):
    checks=[]
    for key,label in LABELS.items():
        a,b=program.get(key),reference.get(key)
        passed=(a is None and b is None) or (a is not None and b is not None and
                 (math.isclose(a,b,rel_tol=0,abs_tol=1e-12) if key in RATES else a==b))
        checks.append({"key":key,"label":label,"program":a,"reference":b,"passed":passed,
                       "difference":None if a is None or b is None else a-b})
    return checks


def empty_result():
    return {key:None if key in RATES or key=="delivered_observed_payment_cents" else 0 for key in LABELS}


def render_sql(con,sql,params):
    parts=sql.split("?")
    if len(parts)!=len(params)+1:
        raise ValueError("SQL 参数数量不一致")
    result=parts[0]
    for value,part in zip(params,parts[1:]):
        result+=con.execute("SELECT quote(?)",(value,)).fetchone()[0]+part
    return result.rstrip(";\n ")+";"


def verify_selection(database,month=None,category=None,seller=None):
    started=time.perf_counter()
    primary_sql,primary_params=metric_query(month,category,seller)
    raw_sql,raw_params=reference_query(month,category,seller)
    with closing(open_readonly(database)) as con:
        ambiguity=con.execute(TIE_AUDIT_SQL).fetchone()[0]
        primary_rows=[dict(r) for r in con.execute(primary_sql,primary_params)]
        raw_rows=[dict(r) for r in con.execute(raw_sql,raw_params)]
        program=primary_rows[0] if primary_rows else empty_result()
        reference=raw_rows[0] if raw_rows else empty_result()
        checks=compare_results(program,reference)
        row_presence_matches=len(primary_rows)==len(raw_rows)
        sql_program=render_sql(con,primary_sql,primary_params)
        sql_reference=render_sql(con,raw_sql,raw_params)
    passed=ambiguity==0 and row_presence_matches and all(c["passed"] for c in checks)
    return {"status":"passed" if passed else "failed","filters":{"month":month,"category":category,"seller":seller},
            "program":program,"reference":reference,"checks":checks,"checks_passed":sum(c["passed"] for c in checks),
            "check_count":len(checks),"ambiguous_review_tie_groups":ambiguity,"row_presence_matches":row_presence_matches,
            "empty":program["order_count"]==0,"elapsed_ms":round((time.perf_counter()-started)*1000),
            "computed_at":datetime.now().astimezone().isoformat(timespec="seconds"),
            "sql":{"program":sql_program,"reference":sql_reference,"review_precheck":TIE_AUDIT_SQL},
            "comparison_rule":"计数与整数分金额精确相等；比率绝对误差不超过 1e-12。无分母为 NULL。",
            "independent_source":"复核 SQL 只访问 raw_* 原始表，不复用事实表中的金额、评分或延迟标记。"}


def verify_all_groups(database):
    report=[]
    with closing(open_readonly(database)) as con:
        ambiguity=con.execute(TIE_AUDIT_SQL).fetchone()[0]
        if ambiguity:
            raise AssertionError("原始评价存在并列评分冲突，需先审查规则")
        for group in ["none","month","category","seller"]:
            primary_sql,primary_params=metric_query(group_by=group)
            raw_sql,raw_params=reference_query(group_by=group)
            a={r["group_value"]:dict(r) for r in con.execute(primary_sql,primary_params)}
            b={r["group_value"]:dict(r) for r in con.execute(raw_sql,raw_params)}
            if a.keys()!=b.keys():
                raise AssertionError(f"{group}: 分组集合不一致")
            failures=[]
            for value in a:
                failures.extend({"group":value,**c} for c in compare_results(a[value],b[value]) if not c["passed"])
            report.append({"group_by":group,"groups":len(a),"metric_checks":len(a)*len(LABELS),"passed":not failures,"failures":failures})
    if any(not r["passed"] for r in report):
        raise AssertionError(json.dumps(report,ensure_ascii=False))
    digest=hashlib.sha256()
    with Path(database).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return {"status":"passed","groups":sum(r["groups"] for r in report),
            "metric_checks":sum(r["metric_checks"] for r in report),"details":report,
            "ambiguous_review_tie_groups":ambiguity,"database_sha256":digest.hexdigest()}
