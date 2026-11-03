# 数据来源与使用说明

- 数据集：Brazilian E-Commerce Public Dataset by Olist。
- 发布者：Olist；来源：[Kaggle 数据集页面](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)。
- 源页面标示的数据许可：CC BY-NC-SA 4.0，许可文本见 [Creative Commons](https://creativecommons.org/licenses/by-nc-sa/4.0/)。
- 原始 CSV：`data/raw/` 中的九个文件未经内容改写。
- 本次使用工作环境中已有的完整下载包；来源模式、原始包名称及哈希、逐 CSV 大小与哈希在 `data/provenance/source-manifest.json` 中。没有重新抓取在线订单。
- `data/provenance/official-files.json` 为随原始下载保存的官方清单；本次使用其逐文件字节数再次核对完整性，不声称该清单是本次实时请求的结果。
- 清洗数据库、统计、报告为本项目基于上述数据产生的整理结果；其中的数据及其整理结果继续按 CC BY-NC-SA 4.0 标注来源、非商业及相同方式共享要求。

## LaDe独立数据源

- 发布方托管：[Cainiao-AI/LaDe](https://huggingface.co/datasets/Cainiao-AI/LaDe)，论文：[LaDe: The First Comprehensive Last-mile Delivery Dataset from Industry](https://arxiv.org/abs/2306.10675)。
- 下载固定版本：`be2cec02775cafc8d52230303f32134382bcc50b`，20个文件保留在 `data/lade/raw/`，原始README与元数据随附，未经清洗改写。
- 数据卡标示Apache-2.0，原始README另有研究用途说明；原文保留于 `data/lade/raw/README.md` 和 `data/lade/provenance/repository-metadata.json`。本项目分别保留两数据源的许可与来源，不将Olist的许可当作LaDe的许可。
- 数据未与Olist订单直接关联，未导入原Olist分析库；下载和文件校验证据见 `reports/lade-download-validation.json`。
- 已独立导入 `data/lade/warehouse.sqlite`，包括13个数据文件及ZIP内详细轨迹CSV；导入与算法接入证据分别在 `reports/lade-import-validation.json`、`reports/lade-replay-evaluation.json`。


分享包范围：Olist九张原始CSV及完整两数据库已附带；LaDe大体积原始下载文件未重复附带，保留原始README、来源清单与固定版本下载脚本。
