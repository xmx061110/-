"""用原始 CSV 的 Pandas 聚合独立核对 SQL 指标，不用查询实现验证自身。"""
import hashlib
import json
import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path

import pandas as pd

from .ingest import sha256
from .metrics import get_metrics
from .pipeline import money_cents, write_json
from .schema import FILES


def verify(root):
    root = Path(root)
    checks = []

    def check(name, passed, detail=None):
        checks.append({"name":name,"passed":bool(passed),"detail":detail})
        if not passed:
            raise AssertionError(f"核验失败：{name}；{detail}")

    raw = {name:pd.read_csv(root/"data/raw"/filename,dtype="string",keep_default_na=False,na_values=[""])
           for name,filename in FILES.items()}
    profile = json.loads((root/"reports/data-profile.json").read_text(encoding="utf-8"))
    for f in profile["input_files"]:
        check("raw_hash_"+f["file"],sha256(root/"data/raw"/f["file"])==f["sha256"])
    manifest_path = root/"data/provenance/source-manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for f in manifest["files"]:
            check("source_manifest_hash_"+f["file"],sha256(root/"data/raw"/f["file"])==f["sha256"])
    with closing(sqlite3.connect(root/"data/warehouse.sqlite")) as con:
        check("sqlite_integrity",con.execute("PRAGMA integrity_check").fetchone()[0]=="ok")
        check("sqlite_foreign_keys",not con.execute("PRAGMA foreign_key_check").fetchall())
        check("one_fact_per_order",con.execute("SELECT COUNT(*),COUNT(DISTINCT order_id) FROM fact_orders").fetchone()==(len(raw["orders"]),len(raw["orders"])))
        for name,frame in raw.items():
            check("raw_row_count_"+name, con.execute(f"SELECT COUNT(*) FROM raw_{name}").fetchone()[0]==len(frame))
            check("clean_exact_dedup_"+name,con.execute(f"SELECT COUNT(*) FROM clean_{name}").fetchone()[0]==len(frame.drop_duplicates()))
        check("payment_amount_integer_storage", con.execute("SELECT COUNT(*) FROM clean_order_payments WHERE typeof(payment_value_cents)!='integer'").fetchone()[0]==0)
        check("profit_unknown",con.execute("SELECT COUNT(*) FROM fact_orders WHERE profit_cents IS NOT NULL").fetchone()[0]==0)

    orders=raw["orders"].drop_duplicates();items=raw["order_items"].drop_duplicates();payments=raw["order_payments"].drop_duplicates()
    products=raw["products"].drop_duplicates()
    rows=get_metrics(root/"data/warehouse.sqlite")
    summary=rows[0]
    check("all_order_count",summary["order_count"]==len(orders))
    check("canceled_count",summary["canceled_order_count"]==int(orders.order_status.eq("canceled").sum()))
    items=items.assign(cents=items.price.map(money_cents),freight_cents=items.freight_value.map(money_cents))
    joined=items.merge(orders[["order_id","order_status","order_purchase_timestamp"]],on="order_id",validate="many_to_one").merge(
        products[["product_id","product_category_name"]],on="product_id",validate="many_to_one")
    joined["month"]=joined.order_purchase_timestamp.str[:7]
    joined["category"]=joined.product_category_name.fillna("__unknown__")
    delivered=joined.loc[joined.order_status.eq("delivered")]
    check("delivered_gmv_from_raw_items",summary["sales_gmv_cents"]==int(delivered.cents.sum()))
    check("delivered_freight_from_raw_items",summary["delivered_freight_cents"]==int(delivered.freight_cents.sum()))
    paid=payments.assign(cents=payments.payment_value.map(money_cents)).merge(orders[["order_id","order_status"]],on="order_id",validate="many_to_one")
    check("payments_sum_without_installment_multiplication",summary["delivered_observed_payment_cents"]==int(paid.loc[paid.order_status.eq("delivered"),"cents"].sum()))

    for group in ["month","category","seller"]:
        dimension={"month":"month","category":"category","seller":"seller_id"}[group]
        expected=delivered.groupby(dimension).cents.sum().to_dict()
        queried=get_metrics(root/"data/warehouse.sqlite",group_by=group)
        check("gmv_partition_"+group,sum(r["sales_gmv_cents"] for r in queried)==summary["sales_gmv_cents"])
        check("gmv_each_group_"+group,all(r["sales_gmv_cents"]==int(expected.get(r["group_value"],0)) for r in queried))
        if group in ["category","seller"]:
            counts=joined.groupby(dimension).order_id.nunique().to_dict()
        else:
            counts=orders.assign(month=orders.order_purchase_timestamp.str[:7]).groupby("month").order_id.nunique().to_dict()
        check("distinct_orders_each_group_"+group,all(r["order_count"]==int(counts[r["group_value"]]) for r in queried))

    purchase=pd.to_datetime(orders.order_purchase_timestamp,format="%Y-%m-%d %H:%M:%S")
    actual_date=pd.to_datetime(orders.order_delivered_customer_date,format="%Y-%m-%d %H:%M:%S",errors="coerce")
    estimate=pd.to_datetime(orders.order_estimated_delivery_date,format="%Y-%m-%d %H:%M:%S",errors="coerce")
    carrier=pd.to_datetime(orders.order_delivered_carrier_date,format="%Y-%m-%d %H:%M:%S",errors="coerce")
    valid=orders.order_status.eq("delivered") & actual_date.notna() & estimate.notna() & actual_date.ge(purchase) & estimate.ge(purchase) & (carrier.isna()|actual_date.ge(carrier))
    late=actual_date.loc[valid].dt.date.gt(estimate.loc[valid].dt.date)
    check("delivery_denominator_from_raw_dates",summary["delivery_eligible_order_count"]==int(valid.sum()))
    check("delivery_numerator_calendar_day",summary["late_order_count"]==int(late.sum()))
    # Independent review selection using the published order and hash tie rule.
    reviews=raw["order_reviews"].drop_duplicates().copy()
    reviews["review_score"]=pd.to_numeric(reviews.review_score)
    reviews=reviews.loc[reviews.review_score.between(1,5)]
    columns=raw["order_reviews"].columns.tolist()
    reviews["hash"]=reviews[columns].fillna("").astype(str).apply(lambda r:hashlib.sha256(json.dumps(r.tolist(),ensure_ascii=False).encode()).hexdigest(),axis=1)
    chosen=reviews.sort_values(["order_id","review_answer_timestamp","review_creation_date","review_id","hash"],na_position="first").drop_duplicates("order_id",keep="last")
    scores=orders.loc[orders.order_status.eq("delivered"),["order_id"]].merge(chosen[["order_id","review_score"]],on="order_id",how="inner",validate="one_to_one")
    check("latest_valid_review_denominator",summary["reviewed_delivered_order_count"]==len(scores))
    check("latest_valid_low_review_numerator",summary["low_score_order_count"]==int(scores.review_score.le(2).sum()))
    # Select a real tuple, then intersect all filters at item grain.
    sample=delivered.sort_values(["order_id","order_item_id"]).iloc[0]
    criteria=dict(month=sample["month"],category=sample["category"],seller=sample["seller_id"])
    scoped=joined.loc[joined.month.eq(criteria["month"]) & joined.category.eq(criteria["category"]) & joined.seller_id.eq(criteria["seller"])]
    got=get_metrics(root/"data/warehouse.sqlite",**criteria)[0]
    check("combined_filters_order_count",got["order_count"]==scoped.order_id.nunique(),criteria)
    check("combined_filters_item_gmv",got["sales_gmv_cents"]==int(scoped.loc[scoped.order_status.eq("delivered"),"cents"].sum()),criteria)
    suite=unittest.defaultTestLoader.discover(str(root/"tests"))
    result=unittest.TextTestRunner(verbosity=1,stream=sys.stderr).run(suite)
    check("synthetic_edge_cases",result.wasSuccessful() and result.testsRun>0,{"tests_run":result.testsRun})
    result_data={"status":"passed","input_fingerprint":profile["input_fingerprint"],"check_count":len(checks),"unit_tests":result.testsRun,
                 "database_sha256":sha256(root/"data/warehouse.sqlite"),
                 "python_version":sys.version.split()[0],"pandas_version":pd.__version__,"sqlite_version":sqlite3.sqlite_version,"checks":checks}
    write_json(root/"reports/validation.json",result_data)
    return result_data
