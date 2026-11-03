"""由真实运行产物生成中文数据字典、质量报告、经营分析与验收报告。"""
import csv
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .metrics import get_metrics, metric_query
from .ingest import sha256
from .schema import FILES, KEYS, RELATIONS, SCHEMA, SOURCE_URL


def pct(value):
    return "无有效分母" if value is None else f"{value:.2%}"


def export_csv(path, rows):
    with Path(path).open("w",encoding="utf-8-sig",newline="") as stream:
        if rows:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def write_md(path, lines):
    Path(path).write_text("\n".join(lines)+"\n",encoding="utf-8")


def generate_reports(root):
    root=Path(root); reports=root/"reports"; examples=root/"examples"
    reports.mkdir(exist_ok=True);examples.mkdir(exist_ok=True)
    profile=json.loads((reports/"data-profile.json").read_text(encoding="utf-8"))
    validation=json.loads((reports/"validation.json").read_text(encoding="utf-8"))
    if validation["status"]!="passed" or validation["input_fingerprint"]!=profile["input_fingerprint"]:
        raise ValueError("请先对当前数据成功运行 verify，不能为未验证数据生成验收报告")
    db=root/"data/warehouse.sqlite"
    if validation.get("database_sha256")!=sha256(db):
        raise ValueError("数据库已变化，请重新运行 verify")
    if any(sha256(root/"data/raw"/f["file"])!=f["sha256"] for f in profile["input_files"]):
        raise ValueError("原始 CSV 已变化，请重新构建与验证")
    summary=get_metrics(db)[0]
    monthly=get_metrics(db,group_by="month")
    categories=get_metrics(db,group_by="category")
    sellers=get_metrics(db,group_by="seller")
    for name,rows in [("summary",[summary]),("monthly",monthly),("categories",categories),("sellers",sellers)]:
        export_csv(examples/f"{name}.csv",rows)
        (examples/f"{name}.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    sql,params=metric_query()
    # Only the fixed summary threshold is rendered; interactive filters stay bound parameters.
    (examples/"summary.sql").write_text("-- Equivalent to python -m zhice metrics\n"+sql.replace("HAVING COUNT(*) >= ?","HAVING COUNT(*) >= 0")+";\n",encoding="utf-8")
    sample=sorted(categories,key=lambda r:r["sales_gmv_cents"],reverse=True)[0]
    with closing(sqlite3.connect(db)) as con:
        sample_tuple=con.execute("""SELECT f.order_month,COALESCE(p.product_category_name,'__unknown__'),i.seller_id
          FROM clean_order_items i JOIN clean_products p USING(product_id) JOIN fact_orders f USING(order_id)
          WHERE f.order_status='delivered' ORDER BY i.order_id,i.order_item_id LIMIT 1""").fetchone()
        selected=get_metrics(db,month=sample_tuple[0],category=sample_tuple[1],seller=sample_tuple[2])
        relation_rows=con.execute("""SELECT delivery_late,COUNT(*),SUM(review_score IN(1,2))
          FROM fact_orders WHERE order_status='delivered' AND delivery_eligible=1 AND review_score BETWEEN 1 AND 5
          GROUP BY delivery_late ORDER BY delivery_late""").fetchall()
        chronology=con.execute("""SELECT order_id,order_status,order_purchase_timestamp,order_delivered_carrier_date,
          order_delivered_customer_date,order_estimated_delivery_date,
          order_delivered_carrier_date<order_purchase_timestamp AS carrier_before_purchase,
          order_delivered_customer_date<order_delivered_carrier_date AS actual_before_carrier,
          order_status='delivered' AND order_delivered_customer_date IS NULL AS delivered_missing_actual,
          order_status!='delivered' AND order_delivered_customer_date IS NOT NULL AS not_delivered_with_actual
          FROM fact_orders WHERE order_delivered_carrier_date<order_purchase_timestamp
            OR order_delivered_customer_date<order_delivered_carrier_date
            OR (order_status='delivered' AND order_delivered_customer_date IS NULL)
            OR (order_status!='delivered' AND order_delivered_customer_date IS NOT NULL)""")
        names=[c[0] for c in chronology.description]
        export_csv(reports/"order-date-anomalies.csv",[dict(zip(names,row)) for row in chronology])
    (examples/"combined-filter.json").write_text(json.dumps({"filters":{"month":sample_tuple[0],"category":sample_tuple[1],"seller":sample_tuple[2]},"results":selected},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    dictionary=["# 数据字典与表关系","",f"原始数据：[Olist / Kaggle]({SOURCE_URL})。所有行数、缺失值与范围均由本地完整 CSV 计算。",
                "","## 表粒度与关联","","| 表 | 原始行数 | 字段数 | 主键/粒度 |","|---|---:|---:|---|"]
    for name,info in profile["tables"].items():
        grain=" + ".join(KEYS[name]) if name in KEYS else "无自然主键；一条地理点记录" if name=="geolocation" else "一条评价记录；review_id 与 order_id 均非唯一"
        dictionary.append(f"| {name} | {info['rows']:,} | {info['columns']} | {grain} |")
    dictionary += ["","```mermaid","erDiagram","  customers ||--o{ orders : customer_id","  orders ||--o{ order_items : order_id",
                   "  products ||--o{ order_items : product_id","  sellers ||--o{ order_items : seller_id",
                   "  orders ||--o{ order_payments : order_id","  orders ||--o{ order_reviews : order_id","```","",
                   "主外键在清洗数据库中创建约束，六条核心关系逐条检查。类别翻译不完整，不强制作为商品类别外键；邮编前缀也不直接关联原始地理多行表。",
                   "","客户 customer_id 标识订单对应的客户记录，customer_unique_id 才用于跨订单客户归并。金额为巴西雷亚尔（BRL），没有汇率换算；时间保留源数据无时区的本地墙钟时间，不推断为 UTC。"]
    for name,info in profile["tables"].items():
        dictionary += ["",f"## {name}","",f"文件：`{FILES[name]}`。","",
                       "| 字段 | 语义类型 | 说明 | 缺失行 | 非空不同值 | 非法格式 | 范围 |",
                       "|---|---|---|---:|---:|---:|---|"]
        for col,f in info["fields"].items():
            bounds=f"{f.get('min')} 至 {f.get('max')}" if "min" in f else "—"
            dictionary.append(f"| {col} | {f['semantic_type']} | {f['description']} | {f['missing']:,} | {f['distinct_nonnull']:,} | {f.get('invalid_nonnull',0):,} | {bounds} |")
    dictionary += ["","## 数据库派生层","",
                   "- `raw_*`：保留源字段文本，新增 `_source_row` 为含表头的 CSV 逻辑记录序号；评论内换行不等于新记录。原 CSV 字节单独保留。",
                   "- `clean_*`：仅移除完全重复记录；日期标准化、数值显式转换。原金额文本旁新增 `_cents`，用 Decimal 转为整数分。",
                   "- `fact_orders`：一单一行，商品、付款先各自汇总，评价按规则选一条后再关联；主键 order_id。",
                   "- `fact_order_seller`：视图，一订单一商家一行；商品金额分摊按真实商品归属，不分摊整单付款。",
                   "- `dim_zip_geolocation`：一邮编前缀一行，经纬度为去重后有效点的中位数；只供展示，不参与指标。",
                   "- `build_metadata`：原始数据指纹、指标契约版本、成本可用性。",
                   "","### fact_orders 关键字段","",
                   "| 字段 | 含义 |","|---|---|",
                   "| order_month | 下单时间的 YYYY-MM；非送达月份 |",
                   "| item_count / seller_count | 商品行数 / 商家去重数；无商品记录为 0 |",
                   "| item_subtotal_cents / freight_cents | 全状态商品合计 / 收取运费；无商品记录为 NULL |",
                   "| paid_total_cents / payment_record_count | 全状态付款合计 / 有效付款记录数；无付款时金额为 NULL，记录数为 0 |",
                   "| review_id / review_score / canonical_review_source_row | 最新有效评价及其原始记录定位 |",
                   "| delivery_eligible / delivery_late | 是否纳入送达延迟分母 / 是否晚于预计日；不合格订单延迟为 NULL |",
                   "| delivery_delay_calendar_days | 实际日减预计日，可为负；仅分母内有值 |",
                   "| handoff_eligible / carrier_late_proxy | 单商家、有效日期样本 / 交运晚于最早商品发货截止的代理值 |",
                   "| payment_item_difference_cents | 付款减商品价减收取运费；不自动冲平 |",
                   "| profit_cents | 始终 NULL：缺少真实成本与费用 |","",
                   "完整数据库建表及索引见 `database-schema.sql`；全部字段统计见 `data-profile.json`。"]
    write_md(reports/"数据字典.md",dictionary)
    quality=["# 数据质量与清洗结果","","本报告保留异常事实，不以填零、均值填充或删除取消订单使指标看起来完整。","",
             "| 表 | 原始行 | 完全重复冗余行 | 清洗后行 | 主键冲突冗余行 |","|---|---:|---:|---:|---:|"]
    for name,info in profile["tables"].items():
        quality.append(f"| {name} | {info['rows']:,} | {info['exact_duplicate_rows']:,} | {info['rows']-info['exact_duplicate_rows']:,} | {info.get('duplicate_key_rows_beyond_first','不适用')} |")
    d=profile["diagnostics"]
    quality += ["","## 缺失字段","","| 表.字段 | 缺失数 | 缺失率 |","|---|---:|---:|"]
    for name,info in profile["tables"].items():
        for col,f in info["fields"].items():
            if f["missing"]:
                quality.append(f"| {name}.{col} | {f['missing']:,} | {f['missing']/info['rows']:.2%} |")
    quality += ["","## 关键核查","",
                f"- 六条核心外键关系孤儿记录均为 0。地理邮编缺配另见 `geolocation-validation.json`，不影响订单保留。",
                f"- {d['orders_without_items']:,} 个订单缺少商品记录；总体订单量仍保留，商品维度查询无法分配这些订单。",
                f"- {d['orders_without_payments']} 个订单无付款记录，且为已送达；付款金额保留 NULL。",
                f"- {d['orders_with_multiple_payment_records']:,} 个订单有多条合法付款；不能按 order_id 或相同付款金额去重。",
                f"- {d['payment_item_difference_gt_1_cent_orders']:,} 个订单付款与商品价加运费差额绝对值超过 1 分，完整清单见 `payment-reconciliation-exceptions.csv`；未推断折扣、退款或手续费原因。",
                f"- {d['review_orders_with_multiple_records']:,} 个订单有多条评价；review_id 冗余出现 {profile['tables']['order_reviews']['review_id_duplicate_rows_beyond_first']:,} 行。保留原评价，仅在事实层按规则选择最新有效评分。",
                f"- {d['multi_seller_orders']:,} 个多商家订单，商家分组订单量不可相加。",
                f"- 类别翻译缺配：`{json.dumps(d['untranslated_product_categories'],ensure_ascii=False)}`。保留葡语类别，英语缺失不当作缺少商品类别。",
                "","## 日期与金额","",
                "| 日期异常 | 订单数 |","|---|---:|"]
    quality += [f"| {key} | {count:,} |" for key,count in d["chronology"].items()]
    invalid_dates=sum(f.get("invalid_nonnull",0) for table in profile["tables"].values() for f in table["fields"].values() if f["semantic_type"]=="datetime")
    quality += ["",f"日期格式无法解析的非空值：{invalid_dates}。异常日期行见 `order-date-anomalies.csv`，格式错误行见 `invalid-datetime-records.csv`。",
                f"送达率的分母排除 {d['delivered_excluded_from_delivery_denominator']} 个已送达订单（缺少实际送达时间或送达早于交运等日期矛盾）。交运早于下单的记录另行标注；如实际送达与预计日仍可比，保留在送达延迟分母中，但不纳入交运代理指标。",
                "金额字段逐值用 Decimal 解析为整数分；负数、非法金额、超过两位小数或关键金额缺失会终止构建，不自动取绝对值或填零。0 金额保留并在字段统计中披露。",
                "本次原始 price、freight_value、payment_value 没有缺失、负数或非法值。付款分期期数只作属性，不再乘到金额上。",
                "","订单状态计数：","","| 状态 | 订单数 |","|---|---:|"]
    quality += [f"| {status} | {count:,} |" for status,count in d["order_status_counts"].items()]
    write_md(reports/"数据质量报告.md",quality)
    business=["# 真实数据经营分析与示例查询","","所有数字来自当前九表完整 CSV 和交付数据库；金额单位为 BRL。",
              "月份均按下单时间分组。历史快照覆盖 2016-09 至 2018-10，边界月份、末期数据稀疏和订单尚未完结会影响比较；不将末期状态占比当作最终履约率。",
              "","## 核心指标","","| 指标 | 本次结果 | 可复核分子/分母或口径 |","|---|---:|---|",
              f"| 订单量 | {summary['order_count']:,} | 全部状态，order_id 去重 |",
              f"| 已送达订单 | {summary['delivered_order_count']:,} | order_status=delivered |",
              f"| 销售额（商品成交额） | {summary['sales_gmv_brl']} | 已送达商品价，不含运费 |",
              f"| 已送达收取运费 | {summary['delivered_freight_brl']} | 收取金额，非真实物流成本 |",
              f"| 配送延迟率 | {pct(summary['delivery_late_rate'])} | {summary['late_order_count']:,} / {summary['delivery_eligible_order_count']:,} |",
              f"| 低评分订单占比 | {pct(summary['low_score_rate'])} | {summary['low_score_order_count']:,} / {summary['reviewed_delivered_order_count']:,} 个已送达且有有效评分订单 |",
              f"| 单商家晚交运代理率 | {pct(summary['late_handoff_proxy_rate'])} | {summary['single_seller_late_handoff_proxy_count']:,} / {summary['single_seller_handoff_eligible_count']:,} |",
              "| 利润 / 毛利 / ROI | 不可计算 | 缺少采购成本、佣金、实际运费、税费、退款等 |",
              "","## 月份结果","","| 下单月 | 订单量 | 已送达商品成交额 BRL | 延迟率 | 延迟分母 | 低评分率 | 评分分母 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    business += [f"| {r['group_value']} | {r['order_count']:,} | {r['sales_gmv_brl']} | {pct(r['delivery_late_rate'])} | {r['delivery_eligible_order_count']:,} | {pct(r['low_score_rate'])} | {r['reviewed_delivered_order_count']:,} |" for r in monthly]
    business += ["","## 销售额最高的十个类别","","类别按葡萄牙语代码查询。完整类别和商家结果见 `examples/`，缺少类别的商品归入 `__unknown__`。",
                 "","| 类别 | 订单数 | 销售额 BRL | 配送延迟率 | 低评分率 |","|---|---:|---:|---:|---:|"]
    top=sorted(categories,key=lambda r:r["sales_gmv_cents"],reverse=True)[:10]
    business += [f"| {r['group_value']} | {r['order_count']:,} | {r['sales_gmv_brl']} | {pct(r['delivery_late_rate'])} | {pct(r['low_score_rate'])} |" for r in top]
    business += ["","## 商家履约观察","",
                 "下表为已送达商品成交额最高的十家商家。送达延迟和评分是相关订单的共同结果，涉及多商家时无法归因到单一商家。快照已送达占比含未完结订单，不能解释为商家最终履约成功率。单商家交运代理率更接近发货及时性，但仍非独立的商家责任证据。",
                 "","| 商家 ID | 订单数 | 已送达状态占比 | 商品成交额 BRL | 订单延迟率 | 延迟分母 | 多商家订单 | 晚交运代理率 |",
                 "|---|---:|---:|---:|---:|---:|---:|---:|"]
    top_sellers=sorted(sellers,key=lambda r:r["sales_gmv_cents"],reverse=True)[:10]
    business += [f"| {r['group_value']} | {r['order_count']:,} | {pct(r['delivered_status_share'])} | {r['sales_gmv_brl']} | {pct(r['delivery_late_rate'])} | {r['delivery_eligible_order_count']:,} | {r['multi_seller_order_count']:,} | {pct(r['late_handoff_proxy_rate'])} |" for r in top_sellers]
    business += ["","## 可以据此提出的业务检查","",
                 f"1. 类别 `{sample['group_value']}` 的已送达商品成交额最高，为 {sample['sales_gmv_brl']} BRL。可优先核查该类别库存与履约资源，但现有数据不能判断利润最高的类别。",
                 f"2. {summary['late_order_count']:,} 个已送达订单晚于预计送达日。应将交运代理异常与后续配送耗时分开检查，避免仅凭送达延迟处罚商家。",
                 f"3. 低评分率基于有评分的已送达订单，存在评价选择偏差。未评价订单不作为好评；取消订单的客户体验需要独立专题分析。"]
    for late_flag,count,low_count in relation_rows:
        business.append(f"   - {'延迟' if late_flag else '准时'}且有评分样本：{count:,} 单，低评分 {low_count:,} 单，占 {low_count/count:.2%}。")
    business += ["   上述是描述性相关，不能证明延迟造成低评分。","",
                 "4. 商家比较时建议至少有 30 个相关订单，并进一步检查有效延迟/评分分母。`--min-orders 30` 仅筛选订单样本量，不构成统计显著性检验。",
                 "5. 2018-09、2018-10 处于样本尾部且状态结构特殊，不直接据此判断经营崩塌；需要新增数据确认业务趋势。",
                 "","## 可直接运行的筛选示例","","```powershell","python -m zhice metrics --month 2018-01",
                 f"python -m zhice metrics --category {sample['group_value']} --group-by month",
                 "python -m zhice metrics --group-by seller --min-orders 30",
                 f"python -m zhice metrics --month {sample_tuple[0]} --category {sample_tuple[1]} --seller {sample_tuple[2]}",
                 "```","",f"最后一条组合筛选的真实结果见 `examples/combined-filter.json`：订单量 {selected[0]['order_count']}，销售额 {selected[0]['sales_gmv_brl']} BRL。",
                 "","类别与商家筛选必须在同一商品行同时满足；只计命中商品的金额，关联订单去重。订单量和延迟/低评分人数可跨类别或商家重复出现，不能把分组结果简单相加。付款在类别/商家维度返回 NULL，避免把整单付款重复归属。"]
    write_md(reports/"经营分析与示例查询.md",business)
    acceptance=["# 第一阶段验收报告","","项目：智策——基于华为云 AgentArts 的多 Agent 企业经营决策系统。",
                "","本次完成真实数据处理与业务分析，交付本地可复现的 Python/Pandas + SQLite 数据层。",
                "","| 验收项 | 结果 | 证据 |","|---|---|---|",
                "| 九个完整原始 CSV | 通过 | data/raw/；来源、大小、CRC 与 SHA-256 见 data/provenance/ |",
                "| 表结构与字段字典 | 通过 | 数据字典.md；data-profile.json；database-schema.sql |",
                "| 主外键、缺失、重复、日期、金额 | 通过 | 数据质量报告.md 与逐行异常清单 |",
                "| 可复现导入、清洗、事实表 | 通过 | python -m zhice all；原始文件不改写 |",
                "| 五类经营指标与三维筛选 | 通过 | examples/ 全量 CSV/JSON 与组合筛选结果 |",
                "| 商品/付款关联不放大金额 | 通过 | 原始 CSV 的独立 Pandas 对账；合成边界测试 |",
                "| 取消订单、评价多行、成本缺失 | 通过 | 指标口径与清洗方案.md；金额不计取消单；利润 NULL |",
                f"| 自动核验 | 通过 | {validation['check_count']} 项核验，含 {validation['unit_tests']} 个边界测试；validation.json |",
                "| Agent / 多 Agent / AgentArts 部署 | 未开展，符合本阶段边界 | 仅保留 get_metrics() 查询接口 |","",
                "测试版本：Python "+validation["python_version"]+"；Pandas "+validation["pandas_version"]+"；SQLite "+validation["sqlite_version"]+"。",
                "","### 核验方式","",
                "- 对九个原始文件逐个核对哈希与原始/清洗行数；SQLite 完整性及外键检查。",
                "- 从原始 CSV 独立重算全部订单量、已送达商品金额、运费、付款合计、延迟分子分母、低评分分子分母。",
                "- 全部月份、商品类别、商家逐组比较订单去重数和金额；验证商品金额跨维度可加总。",
                "- 同时筛选月份、类别、商家，验证必须命中同一商品行。",
                "- 合成测试覆盖付款拆分、完全重复/冲突付款、商品多行、取消单、同日送达、缺少付款/评价/日期、零分母、多商家代理限制、参数绑定与整数金额。",
                "","### 使用边界与待后续补充","",
                "1. 销售额是已送达商品成交额，不是企业净收入；没有真实成本、退款、佣金、税费，不能计算利润或预算收益。",
                "2. 历史订单状态为一次快照。未来预测/决策回测必须补充观察截止时间并避免使用未来送达或评价；当前接口只用于回顾性描述分析。",
                "3. 商家配送和评分表现是相关订单的观察结果；多商家订单无法做责任归因，单商家发货指标也只是代理量。",
                "4. 历史数据边界月份和末期存在不完整观察，不能代替当前企业实时经营事实。",
                "5. 后续 Agent 可调用 get_metrics() 或读取事实表；需要另行实现 AgentArts 适配、权限、工具契约及评估，本阶段未连接华为云。",
                "",f"输入指纹：`{profile['input_fingerprint']}`。","",
                "结论：本地第一阶段数据处理、指标查询与独立验证完成；不以此证明后续 Agent 能力或线上业务收益。"]
    replay_path=reports/"reproducibility.json"
    if replay_path.exists():
        replay=json.loads(replay_path.read_text(encoding="utf-8"))
        if replay.get("status")=="passed" and replay.get("database_sha256")==validation["database_sha256"]:
            acceptance += ["","### 独立重建验证","",
                           "已在独立临时数据库中从九个原始 CSV 完整重建。两个 SQLite 文件的 SHA-256 完全一致。",
                           f"数据库 SHA-256：`{replay['database_sha256']}`。详见 `reproducibility.json`。",
                           "此项说明在本次相同软件版本下的可复现性；跨 SQLite 版本以指标和逻辑内容一致为准。"]
    write_md(reports/"阶段验收报告.md",acceptance)
