-- Equivalent to python -m zhice metrics

    WITH selected_items AS (
      SELECT i.order_id, 'all' AS item_group,
             COUNT(*) AS scoped_item_count, SUM(i.price_cents) AS scoped_gmv,
             SUM(i.freight_value_cents) AS scoped_freight
      FROM clean_order_items i JOIN clean_products p ON p.product_id=i.product_id
      
      GROUP BY i.order_id, item_group
    ), scoped AS (
      SELECT f.*, 'all' AS group_value,
             COALESCE(s.scoped_item_count,0) AS scoped_item_count,
             s.scoped_gmv, s.scoped_freight
      FROM fact_orders f LEFT JOIN selected_items s ON s.order_id=f.order_id
      
    )
    SELECT group_value, COUNT(*) AS order_count,
      SUM(order_status='delivered') AS delivered_order_count,
      SUM(order_status='canceled') AS canceled_order_count,
      SUM(order_status='unavailable') AS unavailable_order_count,
      SUM(order_status NOT IN ('canceled','unavailable')) AS non_canceled_available_order_count,
      COALESCE(SUM(scoped_item_count),0) AS item_count,
      COALESCE(SUM(CASE WHEN order_status='delivered' THEN scoped_gmv ELSE 0 END),0) AS sales_gmv_cents,
      COALESCE(SUM(CASE WHEN order_status='delivered' THEN scoped_freight ELSE 0 END),0) AS delivered_freight_cents,
      SUM(CASE WHEN order_status='delivered' THEN paid_total_cents END) AS delivered_observed_payment_cents,
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
    FROM scoped GROUP BY group_value HAVING COUNT(*) >= 0 ORDER BY group_value
    ;
