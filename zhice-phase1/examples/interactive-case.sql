-- 智策：同一筛选条件的生产查询与原始表独立复核
-- 数据库：data/warehouse.sqlite；可在 SQLite 客户端直接运行。
-- 筛选：{"month": "2018-01", "category": "beleza_saude", "seller": "edb1ef5e36e0c8cd84eb3c9b003e486d"}
BEGIN;

-- 0. 前置检查：结果应为 0
SELECT COUNT(*) AS ambiguous_review_tie_groups FROM (
  SELECT order_id, review_answer_timestamp, review_creation_date, review_id
  FROM raw_order_reviews
  GROUP BY order_id, review_answer_timestamp, review_creation_date, review_id
  HAVING COUNT(DISTINCT review_score)>1
);

-- 1. 程序使用的 SQL

    WITH selected_items AS (
      SELECT i.order_id, 'all' AS item_group,
             COUNT(*) AS scoped_item_count, SUM(i.price_cents) AS scoped_gmv,
             SUM(i.freight_value_cents) AS scoped_freight
      FROM clean_order_items i JOIN clean_products p ON p.product_id=i.product_id
      WHERE COALESCE(p.product_category_name, '__unknown__') = 'beleza_saude' AND i.seller_id = 'edb1ef5e36e0c8cd84eb3c9b003e486d'
      GROUP BY i.order_id, item_group
    ), scoped AS (
      SELECT f.*, 'all' AS group_value,
             COALESCE(s.scoped_item_count,0) AS scoped_item_count,
             s.scoped_gmv, s.scoped_freight
      FROM fact_orders f JOIN selected_items s ON s.order_id=f.order_id
      WHERE f.order_month = '2018-01'
    )
    SELECT group_value, COUNT(*) AS order_count,
      SUM(order_status='delivered') AS delivered_order_count,
      SUM(order_status='canceled') AS canceled_order_count,
      SUM(order_status='unavailable') AS unavailable_order_count,
      SUM(order_status NOT IN ('canceled','unavailable')) AS non_canceled_available_order_count,
      COALESCE(SUM(scoped_item_count),0) AS item_count,
      COALESCE(SUM(CASE WHEN order_status='delivered' THEN scoped_gmv ELSE 0 END),0) AS sales_gmv_cents,
      COALESCE(SUM(CASE WHEN order_status='delivered' THEN scoped_freight ELSE 0 END),0) AS delivered_freight_cents,
      NULL AS delivered_observed_payment_cents,
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
    FROM scoped GROUP BY group_value HAVING COUNT(*) >= 0 ORDER BY group_value;

-- 2. 仅从原始表复算的 SQL
-- 独立复核：仅使用 raw_* 原始表，不读取 clean_* 或 fact_*。
-- 金额拆成整数部分与小数部分转分，避免浮点舍入。
-- 评价并列键的评分冲突数必须为 0（先执行前置检查）。
WITH filter_values(month,category,seller) AS (VALUES ('2018-01','beleza_saude','edb1ef5e36e0c8cd84eb3c9b003e486d')),
items AS (
 SELECT DISTINCT order_id,order_item_id,product_id,seller_id,shipping_limit_date,price,freight_value
 FROM raw_order_items
), products AS (
 SELECT DISTINCT product_id,product_category_name FROM raw_products
), payments AS (
 SELECT DISTINCT order_id,payment_sequential,payment_type,payment_installments,payment_value
 FROM raw_order_payments
), payment_totals AS (
 SELECT order_id,SUM((CAST(payment_value AS INTEGER)*100 + CAST(substr(CASE WHEN instr(payment_value,'.')=0 THEN '00' ELSE substr(payment_value,instr(payment_value,'.')+1)||'00' END,1,2) AS INTEGER))) AS order_paid
 FROM payments GROUP BY order_id
), all_item_stats AS (
 SELECT order_id,COUNT(DISTINCT seller_id) AS seller_count,
        MIN(datetime(shipping_limit_date)) AS first_deadline
 FROM items GROUP BY order_id
), selected_items AS (
 SELECT i.order_id,'all' AS item_group,COUNT(*) AS selected_item_count,
        SUM((CAST(i.price AS INTEGER)*100 + CAST(substr(CASE WHEN instr(i.price,'.')=0 THEN '00' ELSE substr(i.price,instr(i.price,'.')+1)||'00' END,1,2) AS INTEGER))) AS selected_price,
        SUM((CAST(i.freight_value AS INTEGER)*100 + CAST(substr(CASE WHEN instr(i.freight_value,'.')=0 THEN '00' ELSE substr(i.freight_value,instr(i.freight_value,'.')+1)||'00' END,1,2) AS INTEGER))) AS selected_freight
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
 SELECT o.order_id,o.order_status,'all' AS group_value,
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
   AND ((v.category IS NULL AND v.seller IS NULL AND 0=0) OR s.order_id IS NOT NULL)
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
 NULL AS delivered_observed_payment_cents,
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
FROM totals ORDER BY group_value;

ROLLBACK;
