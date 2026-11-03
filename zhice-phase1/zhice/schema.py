"""源字段契约。保留 Olist 原字段拼写，不将编码、邮编误作数量。"""

SCHEMA = {
    "customers": {
        "customer_id": ("text", "订单对应的客户记录标识；不可当作跨订单自然人标识"),
        "customer_unique_id": ("text", "跨订单客户标识，用于复购分析"),
        "customer_zip_code_prefix": ("text", "邮编前缀，保留前导零"),
        "customer_city": ("text", "客户城市，保留原文"),
        "customer_state": ("text", "客户州代码"),
    },
    "geolocation": {
        "geolocation_zip_code_prefix": ("text", "邮编前缀；一对多，不是主键"),
        "geolocation_lat": ("real", "纬度，度"),
        "geolocation_lng": ("real", "经度，度"),
        "geolocation_city": ("text", "城市原文，可能存在拼写变体"),
        "geolocation_state": ("text", "州代码"),
    },
    "orders": {
        "order_id": ("text", "订单主键"),
        "customer_id": ("text", "客户表外键"),
        "order_status": ("text", "订单快照状态，不代表完整状态流转历史"),
        "order_purchase_timestamp": ("datetime", "下单时间；月份归属依据"),
        "order_approved_at": ("datetime", "付款获批时间，可缺失"),
        "order_delivered_carrier_date": ("datetime", "订单级交运时间，可缺失；不是各商家独立发货时间"),
        "order_delivered_customer_date": ("datetime", "实际送达时间，可缺失"),
        "order_estimated_delivery_date": ("datetime", "预计送达日期；延迟比较采用日历日"),
    },
    "order_items": {
        "order_id": ("text", "订单外键；与 order_item_id 组成主键"),
        "order_item_id": ("integer", "订单内商品行序号；每行一件商品，不是数量字段"),
        "product_id": ("text", "商品外键"),
        "seller_id": ("text", "商家外键"),
        "shipping_limit_date": ("datetime", "商品行发货截止时间"),
        "price": ("money", "商品行成交价，BRL；不含运费；不是成本"),
        "freight_value": ("money", "商品行收取的运费，BRL；不是可确认的实际物流成本"),
    },
    "order_payments": {
        "order_id": ("text", "订单外键；与 payment_sequential 组成主键"),
        "payment_sequential": ("integer", "同一订单付款记录序号；多记录不等于重复"),
        "payment_type": ("text", "支付方式；保留 not_defined"),
        "payment_installments": ("integer", "分期期数；不得再乘以 payment_value"),
        "payment_value": ("money", "该支付记录总金额，BRL；没有退款流水"),
    },
    "order_reviews": {
        "review_id": ("text", "评价标识；源数据不唯一，不能单独作为主键"),
        "order_id": ("text", "订单外键；一个订单可能有多条评价"),
        "review_score": ("integer", "1至5分；1、2分为本项目低评分"),
        "review_comment_title": ("text", "评价标题，可缺失，不填充为负面评价"),
        "review_comment_message": ("text", "评价正文，可缺失"),
        "review_creation_date": ("datetime", "评价创建日期"),
        "review_answer_timestamp": ("datetime", "评价填写时间；选择最新有效评分的排序依据"),
    },
    "products": {
        "product_id": ("text", "商品主键"),
        "product_category_name": ("text", "葡萄牙语类别代码；缺失单独归入 __unknown__"),
        "product_name_lenght": ("integer", "商品名称字符数；原始拼写保留"),
        "product_description_lenght": ("integer", "商品描述字符数；原始拼写保留"),
        "product_photos_qty": ("integer", "商品图片数"),
        "product_weight_g": ("integer", "重量，克；缺失不臆造"),
        "product_length_cm": ("integer", "长度，厘米"),
        "product_height_cm": ("integer", "高度，厘米"),
        "product_width_cm": ("integer", "宽度，厘米"),
    },
    "sellers": {
        "seller_id": ("text", "商家主键"),
        "seller_zip_code_prefix": ("text", "商家邮编前缀，保留前导零"),
        "seller_city": ("text", "商家城市原文"),
        "seller_state": ("text", "商家州代码"),
    },
    "category_translation": {
        "product_category_name": ("text", "葡语类别主键；不覆盖全部商品类别"),
        "product_category_name_english": ("text", "英语类别名称，仅用于展示"),
    },
}
FILES = {name: ("product_category_name_translation.csv" if name == "category_translation"
                else f"olist_{name}_dataset.csv") for name in SCHEMA}
KEYS = {
    "customers": ["customer_id"], "orders": ["order_id"],
    "order_items": ["order_id", "order_item_id"],
    "order_payments": ["order_id", "payment_sequential"],
    "products": ["product_id"], "sellers": ["seller_id"],
    "category_translation": ["product_category_name"],
}
RELATIONS = [
    ("orders", "customer_id", "customers", "customer_id"),
    ("order_items", "order_id", "orders", "order_id"),
    ("order_items", "product_id", "products", "product_id"),
    ("order_items", "seller_id", "sellers", "seller_id"),
    ("order_payments", "order_id", "orders", "order_id"),
    ("order_reviews", "order_id", "orders", "order_id"),
]
LOAD_ORDER = ["customers", "sellers", "products", "category_translation", "geolocation",
              "orders", "order_items", "order_payments", "order_reviews"]
SOURCE_URL = "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce"
DOWNLOAD_URL = "https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce"
