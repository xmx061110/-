"""稳定的本地查询接口：参数化 SQL、明确分母、整数分金额。"""
import re
import sqlite3
from contextlib import closing
from decimal import Decimal

GROUPS = {"none": None, "month": "month", "category": "category", "seller": "seller"}


def metric_query(month=None, category=None, seller=None, group_by="none", min_orders=0):
    if group_by not in GROUPS:
        raise ValueError(f"group_by 必须属于 {list(GROUPS)}")
    if month is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("month 格式必须是 YYYY-MM")
    if not isinstance(min_orders, int) or min_orders < 0:
        raise ValueError("min_orders 必须为非负整数")
    params = []
    item_where = []
    if category is not None:
        item_where.append("COALESCE(p.product_category_name, '__unknown__') = ?")
        params.append(category)
    if seller is not None:
        item_where.append("i.seller_id = ?")
        params.append(seller)
    item_group = {"category": "COALESCE(p.product_category_name, '__unknown__')",
                  "seller": "i.seller_id"}.get(group_by, "'all'")
    selected_join = "JOIN" if category is not None or seller is not None or group_by in ("category", "seller") else "LEFT JOIN"
    group_expr = {"month": "f.order_month", "category": "s.item_group", "seller": "s.item_group"}.get(group_by, "'all'")
    order_where = "WHERE f.order_month = ?" if month else ""
    if month:
        params.append(month)
    # Payments cannot be assigned to a seller/category without a synthetic allocation rule.
    payment_expr = ("SUM(CASE WHEN order_status='delivered' THEN paid_total_cents END)"
                    if category is None and seller is None and group_by in ("none", "month") else "NULL")
    sql = f"""
    WITH selected_items AS (
      SELECT i.order_id, {item_group} AS item_group,
             COUNT(*) AS scoped_item_count, SUM(i.price_cents) AS scoped_gmv,
             SUM(i.freight_value_cents) AS scoped_freight
      FROM clean_order_items i JOIN clean_products p ON p.product_id=i.product_id
      {('WHERE ' + ' AND '.join(item_where)) if item_where else ''}
      GROUP BY i.order_id, item_group
    ), scoped AS (
      SELECT f.*, {group_expr} AS group_value,
             COALESCE(s.scoped_item_count,0) AS scoped_item_count,
             s.scoped_gmv, s.scoped_freight
      FROM fact_orders f {selected_join} selected_items s ON s.order_id=f.order_id
      {order_where}
    )
    SELECT group_value, COUNT(*) AS order_count,
      SUM(order_status='delivered') AS delivered_order_count,
      SUM(order_status='canceled') AS canceled_order_count,
      SUM(order_status='unavailable') AS unavailable_order_count,
      SUM(order_status NOT IN ('canceled','unavailable')) AS non_canceled_available_order_count,
      COALESCE(SUM(scoped_item_count),0) AS item_count,
      COALESCE(SUM(CASE WHEN order_status='delivered' THEN scoped_gmv ELSE 0 END),0) AS sales_gmv_cents,
      COALESCE(SUM(CASE WHEN order_status='delivered' THEN scoped_freight ELSE 0 END),0) AS delivered_freight_cents,
      {payment_expr} AS delivered_observed_payment_cents,
      SUM(order_status='delivered' AND paid_total_cents IS NULL) AS delivered_missing_payment_order_count,
      SUM(delivery_eligible) AS delivery_eligible_order_count,
      SUM(COALESCE(delivery_late,0)) AS late_order_count,
      1.0*SUM(COALESCE(delivery_late,0))/NULLIF(SUM(delivery_eligible),0) AS delivery_late_rate,
      SUM(CASE WHEN order_status='delivered' AND review_score BETWEEN 1 AND 5 THEN 1 ELSE 0 END) AS reviewed_delivered_order_count,
      SUM(CASE WHEN order_status='delivered' AND review_score IN (1,2) THEN 1 ELSE 0 END) AS low_score_order_count,
      1.0*SUM(CASE WHEN order_status='delivered' AND review_score IN (1,2) THEN 1 ELSE 0 END)/
        NULLIF(SUM(CASE WHEN order_status='delivered' AND review_score BETWEEN 1 AND 5 THEN 1 ELSE 0 END),0) AS low_score_rate,
      SUM(seller_count>1) AS multi_seller_order_count,
      SUM(handoff_eligible) AS single_seller_handoff_eligible_count,
      SUM(COALESCE(carrier_late_proxy,0)) AS single_seller_late_handoff_proxy_count,
      1.0*SUM(COALESCE(carrier_late_proxy,0))/NULLIF(SUM(handoff_eligible),0) AS late_handoff_proxy_rate,
      1.0*SUM(order_status='delivered')/NULLIF(COUNT(*),0) AS delivered_status_share
    FROM scoped GROUP BY group_value HAVING COUNT(*) >= ? ORDER BY group_value
    """
    params.append(min_orders)
    return sql, params


def get_metrics(database, *, month=None, category=None, seller=None, group_by="none", min_orders=0):
    """返回 JSON 可序列化记录。空集返回 []；无有效分母的比率为 None。

    category 使用葡语代码或 __unknown__，seller 使用原始 seller_id。
    一个订单可跨类别/商家，因此分组订单数与比率不能直接相加。
    sales_gmv_cents 可跨商品维度相加，付款在商品维度不归因，返回 None。
    """
    sql, params = metric_query(month, category, seller, group_by, min_orders)
    from pathlib import Path
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(sql, params)]
    for row in rows:
        row["sales_gmv_brl"] = str((Decimal(row["sales_gmv_cents"])/100).quantize(Decimal("0.00")))
        row["delivered_freight_brl"] = str((Decimal(row["delivered_freight_cents"])/100).quantize(Decimal("0.00")))
        row["profit_brl"] = None
    return rows
