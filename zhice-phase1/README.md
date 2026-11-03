# 智策第一阶段：真实数据处理与业务分析

第一阶段完成 Olist 完整真实数据的检查、清洗、SQLite 导入和经营指标查询。后续已加入本地单Agent、历史排查实验、模拟预算优化与反馈回放，以及LaDe场景的原定四Agent受控协作。四角色已接通，但尚未实现跨数据源自主编排、反馈后反复调用模型决策或华为云接入。指标统一由 `zhice.metrics.get_metrics()` 提供。

最新界面已改成四个独立工作区：协作工作台、经营分析、算法实验、证据与说明。在“协作工作台”选择范围并点击“开始四Agent协作”，流程节点会随后台执行状态更新；结果以指标卡、任务表和策略对照图展示。真实模型模式调用4次，工具演练调用0次。见 [工作台改版说明](docs/前端工作台改版说明.md)、[四Agent使用与验收](docs/原定四Agent协作与验收.md) 和 [项目推进清单](docs/项目推进清单.md)。

最新算法、运行方式与验收见 [预算优化与反馈回放](docs/预算优化与反馈回放.md)。预算实验和反馈演示无需模型密钥，可在物流助手第⑦、⑧部分操作。

新一轮优化加入“少改方案、按门槛重排”的稳定策略，在第⑨部分选择日期和预算，对比四种策略并下载本次独立复核证据。完整对照结果及复现方式见 [滚动重规划优化报告](docs/滚动重规划优化报告.md)。200组保留场景属于模拟评估，不是实际物流收益。

LaDe真实物流数据已完整下载、独立导入约5.30 GB的SQLite库并接入预算算法。页面第⑩部分可选城市、月日、快递员，查询原始SQL复核指标并回放真实任务事件；资源费用和关注行动仍为模拟。见 [数据库与算法接入报告](docs/LaDe数据库与算法接入报告.md)。Olist与LaDe分别保存，不硬拼订单。

## 打开交互式验证台

在上一级的“华为ict”文件夹双击 `打开智策验证台.cmd`，等待浏览器打开。选择月份、类别或商家，页面会从数据库实时计算，同时用原始表 SQL 独立复算，并显示每项是否一致。可选择“查看 SQL”，或下载包含两条查询的 SQL 文件。

也可在本项目目录直接运行 `python -m zhice.demo_server --open`。详细使用步骤、计算口径及验证范围见上一级的 `验证台使用说明.md`。服务仅监听本机；启动窗口关闭后，重新双击启动文件即可。

## 先看结果

- [阶段验收报告](reports/阶段验收报告.md)：交付范围、验证证据、已知限制。
- [经营分析与示例查询](reports/经营分析与示例查询.md)：真实结果、月份趋势、类别与商家表现。
- [数据字典](reports/数据字典.md)：全部字段、主外键、范围、缺失数与事实表含义。
- [数据质量报告](reports/数据质量报告.md) 与 [指标口径与清洗方案](docs/指标口径与清洗方案.md)。

## 环境与运行

建议 Python 3.12，依赖只有 Pandas；SQLite 和测试框架使用 Python 标准库。本次实际验证版本见 `reports/validation.json`。

在本目录打开终端：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m zhice all
```

交付包已经包含九个原始 CSV，因此 `all` 可以完全离线重建数据库、验证、更新报告，不会重新下载。无须使用 PowerShell 激活脚本。首次依赖安装需要网络；已有 Pandas 的环境可直接运行 `python -m zhice all`。

当前 Codex 环境可直接运行：

```powershell
& 'C:\Users\32257\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m zhice all
```

上述命令应在本项目目录执行，其他电脑使用自己的 Python。如需读取其他项目副本，可用 `--root` 明确数据根目录（放在子命令前）。

### 单独执行各阶段

```powershell
python -m zhice build
python -m zhice verify
python -m zhice report
python -m zhice reproduce
python -m unittest discover -s tests -v
```

`build` 先写入临时 SQLite，成功完成后替换 `data/warehouse.sqlite`，失败保留原来的数据库。请关闭其他软件对此数据库的写入连接。`verify` 会独立从原始 CSV 对账，并运行边界测试；失败返回非零状态。`report` 必须在当前数据通过验证后运行。

`reproduce` 在项目 `work/` 临时目录独立重建数据库，检查相同运行环境中数据库 SHA-256 完全一致，成功后清理临时数据库，证据写入 `reports/reproducibility.json`。完成后可再运行 `report` 将该项纳入验收报告。数据库约 328 MB，重建时建议至少有 1 GB 可用空间。

### 获取原始数据（只在缺少 CSV 时需要）

本次复用了本地已有完整 Olist 压缩包，经过 ZIP 全文件 CRC、九表清单及官方文件大小、CSV 结构和 SHA-256 校验，不需要 Kaggle 登录。原始字节完整保存在 `data/raw/`，来源证据在 `data/provenance/`。

如果使用新的工作环境：

```powershell
# 尝试官方公开接口下载完整 ZIP
python -m zhice acquire

# 或由用户在 Kaggle 页面登录并下载，再导入本地完整 ZIP
python -m zhice acquire --archive 'D:\datasets\brazilian-ecommerce.zip'

# 如有官方文件清单，还可核对各文件字节数
python -m zhice acquire --archive 'D:\datasets\brazilian-ecommerce.zip' --official-list data/provenance/official-files.json
python -m zhice all
```

下载失败时脚本给出手动下载方式；不保存登录凭据。不接受抽样数据、派生宽表或压缩包内额外文件。若原始目录已有不同字节的 CSV，脚本拒绝覆盖，应在新项目副本中处理新版本。

## 查询指标

```powershell
python -m zhice metrics
python -m zhice metrics --month 2018-01
python -m zhice metrics --category beleza_saude --group-by month
python -m zhice metrics --group-by category
python -m zhice metrics --group-by seller --min-orders 30
python -m zhice metrics --seller 4869f7a5dfa277a7dca6462dcf3b52b2
python -m zhice metrics --month 2018-01 --category beleza_saude --seller 4869f7a5dfa277a7dca6462dcf3b52b2
python -m zhice metrics --group-by month --output examples/my-monthly.csv
```

商家 ID 与类别代码来自数据本身，完整列表见 `examples/sellers.csv` 和 `examples/categories.csv`。上面的任意筛选组合可能没有命中，空结果返回 `[]`；保证有命中的组合及其真实结果见 `examples/combined-filter.json`。缺少类别使用 `__unknown__`；类别查询采用葡萄牙语代码，英语翻译仅作展示。

接口返回原始计数、比率分子分母、整数分金额和 BRL 格式字符串。比率 `0.0677` 表示约 `6.77%`，没有有效分母返回 `null`，不返回虚假的 0%。商品维度上的整单付款不归因，返回 `null`。`profit_brl` 始终为 `null`。

## 代码与数据结构

```text
zhice/                 Python 包：采集、字段契约、清洗、指标、独立验证、报告
tests/                 24 个合成边界测试（不冒充真实数据结果）
data/raw/              九个未经改写的完整原始 CSV
data/provenance/       来源清单、原始包哈希、官方文件清单
data/warehouse.sqlite  可直接查询的完整 SQLite 数据库
docs/                  指标口径、清洗规则与后续接口约定
reports/               数据字典、质量、经营分析、验收与机器可读验证记录
examples/              总体、月份、类别、商家的完整查询结果与 SQL 示例
```

`raw_*` 保存导入文本；`clean_*` 含显式类型和外键约束；`fact_orders` 一单一行；`fact_order_seller` 一订单一商家一行。保留原始输入，构建指纹用于核对来源。

## 必须了解的口径

订单量包括全部状态；销售额仅指已送达订单的商品成交额，不含收取运费。取消、不可用和未送达订单不计销售额。付款存在拆分和分期，先按付款组合键审查，再按订单汇总，不能与商品多行直接连接求和。

延迟按实际送达日是否晚于预计送达日判断；低评分为最新有效评价 1 或 2 分，占已送达且有有效评分订单的比例。商家结果只能反映相关订单，无法仅凭多商家订单的整单日期判断商家责任。缺少真实成本、退款和各类费用，不能计算利润、毛利或投资回报。

数据源：[Olist Brazilian E-Commerce Public Dataset / Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)，发布者 Olist。源页面标示许可为 CC BY-NC-SA 4.0；本包注明来源并将原始数据及其整理结果按该许可保留，详见 `DATA-ATTRIBUTION.md`。
