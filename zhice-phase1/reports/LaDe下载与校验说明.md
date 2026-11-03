# LaDe下载与完整性校验说明

2026-11-02完成。**完整下载与文件校验通过。**

- 来源：[Cainiao-AI/LaDe](https://huggingface.co/datasets/Cainiao-AI/LaDe)。
- 固定版本：`be2cec02775cafc8d52230303f32134382bcc50b`。
- 原始文件：20个，2,883,404,432字节（约2.88 GB，2.69 GiB）。
- 保存目录：`data/lade/raw/`；发布方清单与本地回执：`data/lade/provenance/`。
- 原始字节保持不变；所有文件与发布方LFS SHA-256或Git blob SHA-1一致，并记录本地SHA-256。
- 13个.csv完整严格读取：12个逗号分隔文件、1个实际Tab分隔的roads.csv；全部行列结构检查通过。
- 1个ZIP执行CRC验证，3个XZ执行全流完整性验证，均通过；未反序列化pickle。
- 已确认实际原始文件集合与发布方清单一致，无遗留.part文件。

## 城市明细记录规模

揽收五城市明细共6,136,147行，派送五城市明细共4,514,661行，合计10,650,808行。这是未去重的明细记录数，不宣称已验证唯一包裹数量。

两个顶层聚合文件的记录未追加到上述总数，避免重叠计数；路网行也不作为包裹数。

## 数据文件清单

| 文件 | 字节 | 数据行数 | 分隔符 |
|---|---:|---:|---|
| courier_detailed_trajectory.zip | 160,005,925 | 压缩容器 | 不适用 |
| data_with_trajectory_20s/courier_detailed_trajectory_20s.pkl.xz | 438,656,312 | 压缩容器 | 不适用 |
| data_with_trajectory_20s/delivery_five_cities.pkl.xz | 34,028,796 | 压缩容器 | 不适用 |
| data_with_trajectory_20s/pickup_five_cities.pkl.xz | 41,345,044 | 压缩容器 | 不适用 |
| delivery/delivery_cq.csv | 145,068,417 | 931,351 | comma |
| delivery/delivery_hz.csv | 286,893,611 | 1,861,600 | comma |
| delivery/delivery_jl.csv | 4,736,342 | 31,415 | comma |
| delivery/delivery_sh.csv | 228,069,571 | 1,483,864 | comma |
| delivery/delivery_yt.csv | 31,532,960 | 206,431 | comma |
| delivery_five_cities.csv | 135,539,702 | 472,419 | comma |
| pickup/pickup_cq.csv | 190,522,468 | 1,172,703 | comma |
| pickup/pickup_hz.csv | 335,817,674 | 2,130,456 | comma |
| pickup/pickup_jl.csv | 43,801,745 | 261,801 | comma |
| pickup/pickup_sh.csv | 228,339,849 | 1,424,406 | comma |
| pickup/pickup_yt.csv | 190,190,713 | 1,146,781 | comma |
| pickup_five_cities.csv | 154,069,529 | 531,115 | comma |
| road-network/roads.csv | 231,746,519 | 531,280 | tab |

README、.gitattributes和发布方图片也完整保存在原始树中，包含在20文件总数内。逐文件哈希见 `data/lade/SHA256SUMS.txt`，完整机器校验见 `reports/lade-download-validation.json`。

## 与Olist一起使用

两份数据可以作为同一系统中的两个独立数据源，分别支持经营分析与末端物流任务分析；没有共同订单映射，不能连接成同一条交易—物流链。实际LaDe城市CSV也叫order_id，不代表与Olist同名列可以连接。

LaDe城市时间值省略年份；未核实日期语义前不补造年份。LaDe-D没有已核实的承诺送达截止字段，LaDe-P的有效时间窗可用于揽收时效分析。未提供真实加急费用或干预效果，不将原模拟评估改称真实业务收益。

后续已完成独立数据库导入、关键时间及重复键检查和真实事件接入；本报告仍只描述原始下载校验。当前接入结果见 [数据库与算法接入报告](../docs/LaDe数据库与算法接入报告.md)。

## 复现

从项目根目录运行 `python -m zhice.lade_acquire --verify-only` 可重新核验。`python -m zhice.lade_acquire --workers 4` 可复用固定版本文件并续传缺失文件。当前电脑的Python可执行文件位于 `%USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`。

下载与格式边界测试7项通过，记录见 `reports/lade-acquire-tests.log`。
