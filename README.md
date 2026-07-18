# TPU 高通量筛选数据库与多保真研究工作流

本仓库维护 TPU 高通量筛选的数据结构、抽取管道、质量控制、许可证门控、数据库快照构建方法和研究文档。主研究路线是线性分段 TPU 的“时序耗散—延迟有序化”设计，同时约束韧性、滞后、循环恢复和加工窗口。

**GitHub 私人仓库：** [fafasco16/TPU-HighThroughput-Screening](https://github.com/fafasco16/TPU-HighThroughput-Screening)（`PRIVATE`，默认分支 `main`）。仓库只维护代码、结构定义、配置、清单和文档；原始数据及受限附件留在本地分层目录中。

## 目录

- `01_原始数据/`：不可变原始文件，本地保存，不推送 GitHub。
- `02_暂存数据/`：逐来源忠实抽取结果。
- `03_规范数据/`：统一关系表与 Parquet 数据。
- `04_派生数据/`：曲线指标、质量异常和模型就绪表。
- `05_数据库快照/`：DuckDB 固定快照。
- `06_审核导出/`：Excel 人工审核目录。
- `代码/`：manifest、单位、曲线、适配器、QC 与快照代码。
- `结构定义/`：字段字典、枚举和 schema 版本。
- `配置/`：来源和管道配置。
- `清单/`：可提交的文件清单与快照元数据。
- `文档/`：设计规范、实施计划、质量报告和论文式参考文献。

第三方数据集和克隆仓库的内部目录保持上游原名，项目自己维护的目录使用中文且层级不超过三层。

## 许可边界

私人 GitHub 仓库不改变原始数据的许可证。受限、禁止演绎或禁止再分发的数据只登记来源元数据，不推送原文件或可逆派生数据。公开/共享视图采用许可证白名单。

## 构建

```powershell
uv venv .venv --python 3.11
uv sync --extra dev
.\.venv\Scripts\python.exe 代码\run_pipeline.py manifest
.\.venv\Scripts\python.exe 代码\run_pipeline.py build --version v0.1
.\.venv\Scripts\python.exe 代码\run_pipeline.py qc --version v0.1
```

## 证据与引用

数据规模、许可证、哈希、适用边界及正式参考文献见[数据来源与研究路线台账](文档/TPU_数据来源与研究路线台账.md)。数据库边界与执行顺序分别见[设计规范](文档/设计规范/2026-07-18-TPU数据库v0.1设计规范.md)和[实施计划](文档/实施计划/2026-07-18-TPU数据库v0.1实施计划.md)。
