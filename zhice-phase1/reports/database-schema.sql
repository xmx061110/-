CREATE INDEX idx_fact_month ON fact_orders(order_month);

CREATE UNIQUE INDEX idx_fact_order_id ON fact_orders(order_id);

CREATE UNIQUE INDEX idx_geo_zip ON dim_zip_geolocation(geolocation_zip_code_prefix);

CREATE INDEX idx_item_product ON clean_order_items(product_id,order_id);

CREATE INDEX idx_item_seller ON clean_order_items(seller_id,order_id);

CREATE INDEX idx_product_category ON clean_products(product_category_name);

CREATE INDEX idx_reviews_order ON clean_order_reviews(order_id);

CREATE TABLE "build_metadata" (
"key" TEXT,
  "value" TEXT
);

CREATE TABLE "clean_category_translation" ("product_category_name" TEXT, "product_category_name_english" TEXT, "_source_row" INTEGER, PRIMARY KEY ("product_category_name"));

CREATE TABLE "clean_customers" ("customer_id" TEXT, "customer_unique_id" TEXT, "customer_zip_code_prefix" TEXT, "customer_city" TEXT, "customer_state" TEXT, "_source_row" INTEGER, PRIMARY KEY ("customer_id"));

CREATE TABLE "clean_geolocation" ("geolocation_zip_code_prefix" TEXT, "geolocation_lat" REAL, "geolocation_lng" REAL, "geolocation_city" TEXT, "geolocation_state" TEXT, "_source_row" INTEGER);

CREATE TABLE "clean_order_items" ("order_id" TEXT, "order_item_id" INTEGER, "product_id" TEXT, "seller_id" TEXT, "shipping_limit_date" TEXT, "price" TEXT, "freight_value" TEXT, "_source_row" INTEGER, "price_cents" INTEGER, "freight_value_cents" INTEGER, PRIMARY KEY ("order_id","order_item_id"), FOREIGN KEY ("order_id") REFERENCES "clean_orders"("order_id"), FOREIGN KEY ("product_id") REFERENCES "clean_products"("product_id"), FOREIGN KEY ("seller_id") REFERENCES "clean_sellers"("seller_id"));

CREATE TABLE "clean_order_payments" ("order_id" TEXT, "payment_sequential" INTEGER, "payment_type" TEXT, "payment_installments" INTEGER, "payment_value" TEXT, "_source_row" INTEGER, "payment_value_cents" INTEGER, PRIMARY KEY ("order_id","payment_sequential"), FOREIGN KEY ("order_id") REFERENCES "clean_orders"("order_id"));

CREATE TABLE "clean_order_reviews" ("review_id" TEXT, "order_id" TEXT, "review_score" INTEGER, "review_comment_title" TEXT, "review_comment_message" TEXT, "review_creation_date" TEXT, "review_answer_timestamp" TEXT, "_source_row" INTEGER, FOREIGN KEY ("order_id") REFERENCES "clean_orders"("order_id"));

CREATE TABLE "clean_orders" ("order_id" TEXT, "customer_id" TEXT, "order_status" TEXT, "order_purchase_timestamp" TEXT, "order_approved_at" TEXT, "order_delivered_carrier_date" TEXT, "order_delivered_customer_date" TEXT, "order_estimated_delivery_date" TEXT, "_source_row" INTEGER, PRIMARY KEY ("order_id"), FOREIGN KEY ("customer_id") REFERENCES "clean_customers"("customer_id"));

CREATE TABLE "clean_products" ("product_id" TEXT, "product_category_name" TEXT, "product_name_lenght" INTEGER, "product_description_lenght" INTEGER, "product_photos_qty" INTEGER, "product_weight_g" INTEGER, "product_length_cm" INTEGER, "product_height_cm" INTEGER, "product_width_cm" INTEGER, "_source_row" INTEGER, PRIMARY KEY ("product_id"));

CREATE TABLE "clean_sellers" ("seller_id" TEXT, "seller_zip_code_prefix" TEXT, "seller_city" TEXT, "seller_state" TEXT, "_source_row" INTEGER, PRIMARY KEY ("seller_id"));

CREATE TABLE "dim_zip_geolocation" (
"geolocation_zip_code_prefix" TEXT,
  "latitude" REAL,
  "longitude" REAL,
  "unique_point_records" INTEGER
);

CREATE TABLE "fact_orders" (
"order_id" TEXT,
  "customer_id" TEXT,
  "order_status" TEXT,
  "order_purchase_timestamp" TEXT,
  "order_approved_at" TEXT,
  "order_delivered_carrier_date" TEXT,
  "order_delivered_customer_date" TEXT,
  "order_estimated_delivery_date" TEXT,
  "_source_row" INTEGER,
  "item_count" INTEGER,
  "seller_count" INTEGER,
  "item_subtotal_cents" INTEGER,
  "freight_cents" INTEGER,
  "first_shipping_limit" TEXT,
  "paid_total_cents" INTEGER,
  "payment_record_count" INTEGER,
  "review_id" TEXT,
  "review_score" INTEGER,
  "canonical_review_source_row" INTEGER,
  "order_month" TEXT,
  "delivery_eligible" INTEGER,
  "delivery_late" INTEGER,
  "delivery_delay_calendar_days" INTEGER,
  "handoff_eligible" INTEGER,
  "carrier_late_proxy" INTEGER,
  "payment_item_difference_cents" INTEGER,
  "profit_cents" INTEGER
);

CREATE TABLE "raw_category_translation" (
"_source_row" INTEGER,
  "product_category_name" TEXT,
  "product_category_name_english" TEXT
);

CREATE TABLE "raw_customers" (
"_source_row" INTEGER,
  "customer_id" TEXT,
  "customer_unique_id" TEXT,
  "customer_zip_code_prefix" TEXT,
  "customer_city" TEXT,
  "customer_state" TEXT
);

CREATE TABLE "raw_geolocation" (
"_source_row" INTEGER,
  "geolocation_zip_code_prefix" TEXT,
  "geolocation_lat" TEXT,
  "geolocation_lng" TEXT,
  "geolocation_city" TEXT,
  "geolocation_state" TEXT
);

CREATE TABLE "raw_order_items" (
"_source_row" INTEGER,
  "order_id" TEXT,
  "order_item_id" TEXT,
  "product_id" TEXT,
  "seller_id" TEXT,
  "shipping_limit_date" TEXT,
  "price" TEXT,
  "freight_value" TEXT
);

CREATE TABLE "raw_order_payments" (
"_source_row" INTEGER,
  "order_id" TEXT,
  "payment_sequential" TEXT,
  "payment_type" TEXT,
  "payment_installments" TEXT,
  "payment_value" TEXT
);

CREATE TABLE "raw_order_reviews" (
"_source_row" INTEGER,
  "review_id" TEXT,
  "order_id" TEXT,
  "review_score" TEXT,
  "review_comment_title" TEXT,
  "review_comment_message" TEXT,
  "review_creation_date" TEXT,
  "review_answer_timestamp" TEXT
);

CREATE TABLE "raw_orders" (
"_source_row" INTEGER,
  "order_id" TEXT,
  "customer_id" TEXT,
  "order_status" TEXT,
  "order_purchase_timestamp" TEXT,
  "order_approved_at" TEXT,
  "order_delivered_carrier_date" TEXT,
  "order_delivered_customer_date" TEXT,
  "order_estimated_delivery_date" TEXT
);

CREATE TABLE "raw_products" (
"_source_row" INTEGER,
  "product_id" TEXT,
  "product_category_name" TEXT,
  "product_name_lenght" TEXT,
  "product_description_lenght" TEXT,
  "product_photos_qty" TEXT,
  "product_weight_g" TEXT,
  "product_length_cm" TEXT,
  "product_height_cm" TEXT,
  "product_width_cm" TEXT
);

CREATE TABLE "raw_sellers" (
"_source_row" INTEGER,
  "seller_id" TEXT,
  "seller_zip_code_prefix" TEXT,
  "seller_city" TEXT,
  "seller_state" TEXT
);

CREATE VIEW fact_order_seller AS
                  SELECT i.order_id, i.seller_id, COUNT(*) AS seller_item_count,
                    SUM(i.price_cents) AS seller_item_subtotal_cents,
                    SUM(i.freight_value_cents) AS seller_freight_cents,
                    MIN(i.shipping_limit_date) AS first_seller_shipping_limit,
                    f.order_month,f.order_status,f.seller_count,f.delivery_eligible,f.delivery_late,
                    f.review_score,f.handoff_eligible,f.carrier_late_proxy
                  FROM clean_order_items i JOIN fact_orders f ON f.order_id=i.order_id
                  GROUP BY i.order_id,i.seller_id;
