"""CSV -> 原始层 -> 清洗层 -> 订单/商家事实层；不接入 Agent 或云服务。"""
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from .ingest import sha256
from .schema import FILES, KEYS, LOAD_ORDER, RELATIONS, SCHEMA


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def money_cents(value):
    if pd.isna(value):
        return pd.NA
    try:
        number = Decimal(str(value)) * 100
        if not number.is_finite() or number != number.to_integral_value() or number < 0:
            raise ValueError(f"金额应为非负且不多于两位小数：{value}")
        return int(number)
    except InvalidOperation as exc:
        raise ValueError(f"非法金额：{value}") from exc


def parse_date(series):
    return pd.to_datetime(series, format="%Y-%m-%d %H:%M:%S", errors="coerce")


def profile_table(name, frame):
    result = {"file": FILES[name], "rows": len(frame), "columns": len(frame.columns),
              "exact_duplicate_rows": int(frame.duplicated().sum()), "fields": {}}
    for column, (kind, description) in SCHEMA[name].items():
        values = frame[column]
        field = {"semantic_type": kind, "description": description,
                 "missing": int(values.isna().sum()), "distinct_nonnull": int(values.nunique())}
        if kind == "datetime":
            dates = parse_date(values)
            field.update({"invalid_nonnull": int((values.notna() & dates.isna()).sum()),
                          "min": str(dates.min()) if dates.notna().any() else None,
                          "max": str(dates.max()) if dates.notna().any() else None})
        elif kind in ("integer", "real", "money"):
            numbers = pd.to_numeric(values, errors="coerce")
            field.update({"invalid_nonnull": int((values.notna() & numbers.isna()).sum()),
                          "negative": int((numbers < 0).sum()), "zero": int(numbers.eq(0).sum()),
                          "min": float(numbers.min()) if numbers.notna().any() else None,
                          "max": float(numbers.max()) if numbers.notna().any() else None})
        result["fields"][column] = field
    if name in KEYS:
        key = KEYS[name]
        result.update({"primary_key": key, "null_key_rows": int(frame[key].isna().any(axis=1).sum()),
                       "duplicate_key_rows_beyond_first": int(frame.duplicated(key).sum())})
    if name == "order_reviews":
        result["review_id_duplicate_rows_beyond_first"] = int(frame.review_id.duplicated().sum())
        result["order_id_duplicate_rows_beyond_first"] = int(frame.order_id.duplicated().sum())
    return result


def clean_table(name, raw):
    frame = raw.loc[~raw.duplicated()].copy()
    frame["_source_row"] = frame.index + 2  # CSV logical record ordinal incl. header; not physical text line.
    for column, (kind, _) in SCHEMA[name].items():
        if kind == "money":
            frame[column + "_cents"] = frame[column].map(money_cents).astype("Int64")
            if frame[column + "_cents"].isna().any():
                raise ValueError(f"关键金额缺失：{name}.{column}；拒绝填零")
        elif kind == "datetime":
            dates = parse_date(frame[column])
            frame[column] = dates.dt.strftime("%Y-%m-%d %H:%M:%S").astype("string")
        elif kind == "integer":
            numbers = pd.to_numeric(frame[column], errors="coerce")
            frame[column] = numbers.where(numbers.mod(1).eq(0)).astype("Int64")
        elif kind == "real":
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Float64")
    if name in KEYS:
        key = KEYS[name]
        if frame[key].isna().any(axis=None) or frame.duplicated(key).any():
            raise ValueError(f"{name} 主键为空或存在冲突；请审查原始记录，不能任意保留一行")
    return frame


def canonical_reviews(reviews):
    valid = reviews.loc[reviews.review_score.between(1, 5).fillna(False)].copy()
    if valid.empty:
        return valid[["order_id", "review_id", "review_score", "_source_row"]].rename(
            columns={"_source_row": "canonical_review_source_row"})
    # Stable hash makes equal-time ties independent of dataframe order.
    columns = list(SCHEMA["order_reviews"])
    valid["_tie_hash"] = valid[columns].fillna("").astype(str).apply(
        lambda row: hashlib.sha256(json.dumps(row.tolist(), ensure_ascii=False).encode()).hexdigest(), axis=1)
    valid = valid.sort_values(["order_id", "review_answer_timestamp", "review_creation_date", "review_id", "_tie_hash"],
                             na_position="first", kind="stable")
    return valid.drop_duplicates("order_id", keep="last")[["order_id", "review_id", "review_score", "_source_row"]].rename(
        columns={"_source_row": "canonical_review_source_row"})


def build_order_fact(clean):
    orders, items = clean["orders"], clean["order_items"]
    payments, reviews = clean["order_payments"], clean["order_reviews"]
    item_sum = items.groupby("order_id", as_index=False).agg(
        item_count=("order_item_id", "size"), seller_count=("seller_id", "nunique"),
        item_subtotal_cents=("price_cents", "sum"), freight_cents=("freight_value_cents", "sum"),
        first_shipping_limit=("shipping_limit_date", "min"))
    payment_sum = payments.groupby("order_id", as_index=False).agg(
        paid_total_cents=("payment_value_cents", "sum"), payment_record_count=("payment_sequential", "size"))
    fact = orders.merge(item_sum, on="order_id", how="left", validate="one_to_one").merge(
        payment_sum, on="order_id", how="left", validate="one_to_one").merge(
        canonical_reviews(reviews), on="order_id", how="left", validate="one_to_one")
    for col in ["item_count", "seller_count", "payment_record_count"]:
        fact[col] = fact[col].fillna(0).astype("Int64")
    for col in ["item_subtotal_cents", "freight_cents", "paid_total_cents", "review_score", "canonical_review_source_row"]:
        fact[col] = fact[col].astype("Int64")
    fact["order_month"] = fact.order_purchase_timestamp.str.slice(0, 7)
    purchase = parse_date(fact.order_purchase_timestamp)
    actual = parse_date(fact.order_delivered_customer_date)
    carrier = parse_date(fact.order_delivered_carrier_date)
    estimate = parse_date(fact.order_estimated_delivery_date)
    deadline = parse_date(fact.first_shipping_limit)
    eligible = (fact.order_status.eq("delivered") & purchase.notna() & actual.notna() & estimate.notna()
                & actual.ge(purchase) & estimate.ge(purchase) & (carrier.isna() | actual.ge(carrier)))
    fact["delivery_eligible"] = eligible.astype(int)
    fact["delivery_late"] = actual.dt.normalize().gt(estimate.dt.normalize()).astype("Int64").where(eligible)
    fact["delivery_delay_calendar_days"] = (actual.dt.normalize()-estimate.dt.normalize()).dt.days.astype("Int64").where(eligible)
    handoff = (fact.seller_count.eq(1) & ~fact.order_status.isin(["canceled", "unavailable"])
               & carrier.notna() & purchase.notna() & deadline.notna()
               & carrier.ge(purchase) & deadline.ge(purchase))
    fact["handoff_eligible"] = handoff.astype(int)
    fact["carrier_late_proxy"] = carrier.gt(deadline).astype("Int64").where(handoff)
    fact["payment_item_difference_cents"] = fact.paid_total_cents - fact.item_subtotal_cents - fact.freight_cents
    fact["profit_cents"] = pd.Series(pd.NA, index=fact.index, dtype="Int64")
    return fact


def write_clean_table(con, name, frame):
    table = "clean_" + name
    definitions = []
    for column in frame.columns:
        kind = SCHEMA[name].get(column, (None,))[0]
        dtype = "INTEGER" if column == "_source_row" or column.endswith("_cents") or kind == "integer" else "REAL" if kind == "real" else "TEXT"
        definitions.append(f'"{column}" {dtype}')
    if name in KEYS:
        definitions.append("PRIMARY KEY (" + ",".join('"'+c+'"' for c in KEYS[name]) + ")")
    for child, child_col, parent, parent_col in RELATIONS:
        if child == name:
            definitions.append(f'FOREIGN KEY ("{child_col}") REFERENCES "clean_{parent}"("{parent_col}")')
    con.execute(f'CREATE TABLE "{table}" ({", ".join(definitions)})')
    frame.to_sql(table, con, if_exists="append", index=False, chunksize=5000)


def build(root, database=None):
    root = Path(root)
    database = Path(database) if database else root / "data/warehouse.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    raw, clean, profiles = {}, {}, {}
    input_files = []
    for name, filename in FILES.items():
        path = root / "data/raw" / filename
        frame = pd.read_csv(path, dtype="string", keep_default_na=False, na_values=[""])
        if frame.columns.tolist() != list(SCHEMA[name]):
            raise ValueError(f"{filename} 表结构与契约不一致")
        raw[name] = frame
        profiles[name] = profile_table(name, frame)
        clean[name] = clean_table(name, frame)
        input_files.append({"file": filename, "sha256": sha256(path), "rows": len(frame), "bytes": path.stat().st_size})
        print(f"profile {name}: {len(frame):,} -> {len(clean[name]):,}", flush=True)
    foreign_keys = []
    for child, cc, parent, pc in RELATIONS:
        missing = clean[child][cc].isna() | ~clean[child][cc].isin(clean[parent][pc])
        foreign_keys.append({"child": child, "column": cc, "parent": parent, "parent_column": pc,
                             "orphan_rows": int(missing.sum())})
        if missing.any():
            raise ValueError(f"外键失配：{child}.{cc} -> {parent}.{pc}")
    if clean["orders"].order_purchase_timestamp.isna().any():
        raise ValueError("下单时间缺失/无效，拒绝构建无法归属月份的经营事实")
    fact = build_order_fact(clean)
    p = clean["products"]
    untranslated = p.loc[p.product_category_name.notna() & ~p.product_category_name.isin(clean["category_translation"].product_category_name)]
    chronology = chronology_audit(clean["orders"])
    diagnostics = {
        "order_status_counts": {str(k): int(v) for k, v in clean["orders"].order_status.value_counts().items()},
        "review_orders_with_multiple_records": int((clean["order_reviews"].groupby("order_id").size()>1).sum()),
        "invalid_review_score_rows": int((~clean["order_reviews"].review_score.between(1,5).fillna(False)).sum()),
        "untranslated_product_categories": {str(k): int(v) for k,v in untranslated.product_category_name.value_counts().items()},
        "orders_without_items": int(fact.item_count.eq(0).sum()),
        "orders_without_payments": int(fact.payment_record_count.eq(0).sum()),
        "orders_with_multiple_payment_records": int(fact.payment_record_count.gt(1).sum()),
        "payment_item_difference_gt_1_cent_orders": int(fact.payment_item_difference_cents.abs().gt(1).sum()),
        "delivered_missing_payment_orders": int((fact.order_status.eq("delivered") & fact.paid_total_cents.isna()).sum()),
        "delivered_excluded_from_delivery_denominator": int((fact.order_status.eq("delivered") & fact.delivery_eligible.eq(0)).sum()),
        "multi_seller_orders": int(fact.seller_count.gt(1).sum()),
        "chronology": chronology,
    }
    profile = {"input_files": input_files, "tables": profiles, "foreign_keys": foreign_keys, "diagnostics": diagnostics,
               "input_fingerprint": hashlib.sha256(json.dumps(input_files, sort_keys=True).encode()).hexdigest()}
    temporary = database.with_suffix(".building.sqlite")
    if temporary.exists():
        temporary.unlink()
    try:
        with closing(sqlite3.connect(temporary)) as con:
            con.execute("PRAGMA foreign_keys=ON")
            for name in LOAD_ORDER:
                raw_frame = raw[name].copy()
                raw_frame.insert(0, "_source_row", range(2, len(raw_frame)+2))
                raw_frame.to_sql("raw_" + name, con, index=False, chunksize=5000)
                write_clean_table(con, name, clean[name])
            fact.to_sql("fact_orders", con, index=False, chunksize=5000)
            con.executescript("""
                CREATE UNIQUE INDEX idx_fact_order_id ON fact_orders(order_id);
                CREATE INDEX idx_fact_month ON fact_orders(order_month);
                CREATE INDEX idx_item_seller ON clean_order_items(seller_id,order_id);
                CREATE INDEX idx_item_product ON clean_order_items(product_id,order_id);
                CREATE INDEX idx_product_category ON clean_products(product_category_name);
                CREATE INDEX idx_reviews_order ON clean_order_reviews(order_id);
                CREATE VIEW fact_order_seller AS
                  SELECT i.order_id, i.seller_id, COUNT(*) AS seller_item_count,
                    SUM(i.price_cents) AS seller_item_subtotal_cents,
                    SUM(i.freight_value_cents) AS seller_freight_cents,
                    MIN(i.shipping_limit_date) AS first_seller_shipping_limit,
                    f.order_month,f.order_status,f.seller_count,f.delivery_eligible,f.delivery_late,
                    f.review_score,f.handoff_eligible,f.carrier_late_proxy
                  FROM clean_order_items i JOIN fact_orders f ON f.order_id=i.order_id
                  GROUP BY i.order_id,i.seller_id;
            """)
            geo = clean["geolocation"]
            valid_geo = geo.loc[geo.geolocation_lat.between(-90,90) & geo.geolocation_lng.between(-180,180)]
            dim_geo = valid_geo.groupby("geolocation_zip_code_prefix", as_index=False).agg(
                latitude=("geolocation_lat","median"), longitude=("geolocation_lng","median"),
                unique_point_records=("geolocation_lat","size"))
            dim_geo.to_sql("dim_zip_geolocation", con, index=False)
            con.execute("CREATE UNIQUE INDEX idx_geo_zip ON dim_zip_geolocation(geolocation_zip_code_prefix)")
            write_json(reports / "geolocation-validation.json", {
                "invalid_coordinate_rows": len(geo)-len(valid_geo), "unique_zip_prefixes": len(dim_geo),
                "customers_missing_geo_prefix": int((~clean["customers"].customer_zip_code_prefix.isin(dim_geo.geolocation_zip_code_prefix)).sum()),
                "sellers_missing_geo_prefix": int((~clean["sellers"].seller_zip_code_prefix.isin(dim_geo.geolocation_zip_code_prefix)).sum()),
                "note": "经纬度中位数仅供展示；不用于当前经营指标或路线距离计算。"})
            metadata = pd.DataFrame([{"key":"input_fingerprint", "value":profile["input_fingerprint"]},
                                     {"key":"metric_contract_version", "value":"1.0.0"},
                                     {"key":"cost_availability", "value":"not_available"}])
            metadata.to_sql("build_metadata", con, index=False)
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not con.execute("PRAGMA foreign_key_check").fetchall()
            ddl = ";\n\n".join(row[0] for row in con.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")) + ";\n"
            (reports / "database-schema.sql").write_text(ddl, encoding="utf-8")
            con.commit()
        os.replace(temporary, database)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    write_json(reports / "data-profile.json", profile)
    # Explicit exception exports retain order identities for traceability.
    mismatch = fact.loc[fact.payment_item_difference_cents.abs().gt(1).fillna(False),
                        ["order_id","order_status","paid_total_cents","item_subtotal_cents","freight_cents","payment_item_difference_cents"]]
    mismatch.to_csv(reports / "payment-reconciliation-exceptions.csv", index=False, encoding="utf-8-sig")
    write_date_exceptions(raw, reports)
    return profile


def chronology_audit(orders):
    purchase = parse_date(orders.order_purchase_timestamp)
    approved = parse_date(orders.order_approved_at)
    carrier = parse_date(orders.order_delivered_carrier_date)
    actual = parse_date(orders.order_delivered_customer_date)
    estimate = parse_date(orders.order_estimated_delivery_date)
    masks = {
        "approved_before_purchase": approved.lt(purchase), "carrier_before_purchase": carrier.lt(purchase),
        "actual_before_purchase": actual.lt(purchase), "actual_before_carrier": actual.lt(carrier),
        "estimate_before_purchase": estimate.lt(purchase),
        "delivered_missing_actual": orders.order_status.eq("delivered") & actual.isna(),
        "not_delivered_with_actual": ~orders.order_status.eq("delivered") & actual.notna(),
    }
    return {key: int(value.sum()) for key,value in masks.items()}


def write_date_exceptions(raw, reports):
    records = []
    for name, frame in raw.items():
        for col,(kind,_) in SCHEMA[name].items():
            if kind == "datetime":
                mask = frame[col].notna() & parse_date(frame[col]).isna()
                for idx,value in frame.loc[mask,col].items():
                    records.append({"table": name, "source_row": idx+2, "column": col, "raw_value": value})
    pd.DataFrame(records, columns=["table","source_row","column","raw_value"]).to_csv(
        reports / "invalid-datetime-records.csv", index=False, encoding="utf-8-sig")
