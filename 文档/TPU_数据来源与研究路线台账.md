# TPU 高通量筛选：数据来源、证据等级与研究路线台账

> 项目状态：起步期；本文件为持续更新的来源台账，不是最终论文稿。  
> 首次建立：2026-07-18  
> 研究目标：通过公开实验数据、DFT、MD、多保真机器学习与少量定向合成验证，筛选具有高性能、可合成性、应用潜力和论文新颖性的 TPU/PUE 体系。

## 1. 台账使用规则

1. 将“论文声称的数据规模”“实际获得的文件”“可机器读取的有效样本数”分别记录，不能混用。
2. 每条原始记录必须保留来源 DOI/URL、表格或图号、实验条件、单位、数据版本和提取方式；无法追溯到具体出处的数据不得进入最终高置信度训练集。
3. GitHub 仓库只作为代码或数据载体；论文写作优先引用原始论文和正式数据 DOI，同时记录仓库提交哈希以保证可复现。
4. 许可证未知或仓库无 LICENSE 的数据，只用于内部学术研究和方法复现，不重新分发，不直接并入拟公开的数据包。
5. 文献提取值、作者提供的原始表格、由图像数字化获得的值、DFT 值和 MD 值必须使用不同的 `fidelity` 与 `extraction_method` 标记。
6. 任何性能值都必须绑定样品配方、合成/加工过程和测试条件。只含单体 SMILES、但不含配比与工艺的数据，不能直接训练可靠的 TPU 终性能模型。

## 2. 当前数据资产总览

截至 2026-07-18，`01_原始数据` 已分为基础数据、外部数据、代码仓库镜像和仅供参考四层，共 1,606 个文件、642,262,263 字节（不含嵌套 Git 对象）。这个数字是文件资产量，不等于独立 TPU 配方数；真正可用于化学—性能主模型的高置信样品远少于文件数。

### 2.1 工作区原有文件

| 数据资产 | 实际规模 | 当前价值 | 关键限制 | 建议用途 | 主要引用 |
|---|---:|---|---|---|---|
| `01_原始数据/基础数据/openpoly.csv` | 741 行，32 列 | 含 Tg、模量、强度、断裂伸长率等通用聚合物实验属性 | 性能字段高度稀疏；缺少 TPU 配方、合成工艺、测试条件和原始曲线 | 通用聚合物表征预训练、迁移学习和描述符筛选；不能单独作为 TPU 模型 | [5] |
| `01_原始数据/基础数据/PI1M_v2.csv` | 995,799 行，2 列（SMILES、SA Score） | 大规模假想聚合物化学空间 | 无 TPU 标签、无实验性能、含部分不适合 TPU 的结构；不是“可直接合成的 TPU 候选库” | 自监督表征预训练、生成模型化学空间先验 | [6] |
| `01_原始数据/基础数据/smipoly_monomers.csv` | 1,083 行，5 列；SMILES 有 12 个重复 | 有规则驱动的可聚合单体种子 | 缺少 TPU 角色、官能度、纯度、供应商、EHS、价格等字段 | 候选单体种子库；需二次分类为二异氰酸酯/多元醇/扩链剂等 | [7] |
| `01_原始数据/基础数据/TPU_开源数据库与建库方案.xlsx` | 6 个工作表 | 已包含数据库比较、任务映射、字段与实施路线雏形 | 当前偏“单张宽表”思路，无法完整表达批次、重复、曲线、多保真计算与来源关系 | 作为需求草案；后续重构为规范化关系数据库 | 本项目内部文件 |

### 2.2 已核验并下载的外部来源

| 来源/本地位置 | 论文或页面声称的规模 | 实际获得并核验的内容 | 许可证/使用边界 | 对本项目的价值 | 主要引用 |
|---|---:|---|---|---|---|
| `01_原始数据/外部数据/PUE643_2023_ESI.pdf` | 原始集 643 个 PUE；其中 386 条完整应力–应变曲线；基准集 326 条 | 官方 ESI PDF；说明 32 种多元醇、117 种硬段组合、20 个输入特征，但 PDF 内没有完整可机器读取的 643 行数据表 | 出版商版权；当前仅作学术核验与字段依据 | TPU/PUE 机械性能字段体系和基准设计的核心依据 | [1] |
| `01_原始数据/代码仓库镜像/DQ/experiment/datasets/PUE.csv` | 对应 PUE 基准数据 | 326 行 × 24 列，无缺失；输入多为 Z-score/对数变换，输出为 `logEB`、`logYM`、`logTS` | 仓库未发现 LICENSE；不宜重新分发 | 可复现 326 条基准 ML；不能恢复原始配方身份，也不能直接生成新化学体系 | [1], [12] |
| `01_原始数据/代码仓库镜像/MatImpute/experiment/dataset/PUE.csv` | 同一 PUE 基准数据 | 与上述 326 × 24 数据一致 | 仓库未发现 LICENSE | 缺失值处理/鲁棒性研究；不是新的独立实验数据 | [12] |
| `01_原始数据/外部数据/Nature2025_Supplementary_Data.xlsx` | 10 组超分子聚氨酯体系的小分子计算数据 | 1 个工作表，94 行 × 76 列；含封端片段/二聚体的 DFT 优化坐标 | 论文与数据按 CC BY-NC-ND 4.0；可引用和非商业研究，修改/再分发需谨慎 | 建立“小分子氢键结合能 → TPU 强度/韧性”机理路线 | [3] |
| `01_原始数据/外部数据/Nature2025_Source_Data.xlsx` | 文中各主图源数据 | 包含 2,558 行完整应力–应变点、10 个体系的韧性重复值、DMA/介电、SAXS/WAXS、循环拉伸、疲劳、自愈和回收数据 | CC BY-NC-ND 4.0 | 当前质量最高的“计算—结构—性能—验证”闭环范例；适合二次计算和机理验证 | [3] |
| `01_原始数据/外部数据/Nature2025_Supplementary_Information.pdf` | 合成、DFT、表征和测试细节 | 14.06 MB 官方补充信息 | CC BY-NC-ND 4.0 | 复现实验与计算协议，定义后续验证标准 | [3] |
| `01_原始数据/代码仓库镜像/viscosity-modeling/FeatureSpaces.xlsx` | 39 种 PU 预聚体配方 | 两个特征空间：化学空间 39 × 14 个有效字段；物理化学空间 39 × 27 个有效字段 | 论文 CC BY-NC 3.0；GitHub 仓库未单独标明 LICENSE | 构建加工可行性/预聚体黏度约束模型 | [4] |
| `01_原始数据/代码仓库镜像/viscosity-modeling/ViscTempData.xlsx` | 39 个配方的温度–黏度曲线 | 39 个有数据的工作表；每个约 121–122 个温度–黏度点，主要覆盖 40–80 °C | 同上 | 防止只筛到“性能高但不可混合、不可浇注”的候选 | [4] |
| `01_原始数据/外部数据/jp0c06439_si_002.xlsx` | 43 种工业相关线性 PU 组合的 Tg 文献数据 | 43 个样品；`Samples` 表保留原文来源、样品名、硬/软段、密度、分子量、硬段质量分数和 Tg；另含 45 列模型特征与单体 SMILES | ACS 补充数据；当前仅作内部学术研究，不重新分发 | 高价值 TPU 专用 Tg 小样本，可与 DFT/溶解度参数/硬段描述符联合建模 | [13] |
| `01_原始数据/外部数据/jp0c06439_si_003.xlsx` | 与上述论文模型对应的数据矩阵 | 43 行样品 × 45 列目标/特征；无样品身份列，必须与 `_002` 联用 | 同上 | 可直接复现实验；训练/拆分时必须按化学组合防止泄漏 | [13] |
| `01_原始数据/外部数据/am1c24715_si_002.xlsx` | 63 个工业相关线性 PU/PUU 弹性体 | `Sample ID, E, and Stoich` 含 63 个样品的 ID、预聚体 NCO/OH 和 Young's modulus；另含 17 列化学特征、74 列物理/红外/量化特征及 693 个优化空间候选 | ACS 补充数据；当前仅作内部学术研究，不重新分发 | 当前最有价值的 TPU 专用模量数据；支持物理化学模型复现、特征选择和候选逆向设计 | [11] |
| `01_原始数据/外部数据/ma4c02559_si_001.pdf` | 48 个多组分 PUE 全原子 MD 系统 | 官方 SI 给出 PDMS/PCDL 软段、HDI/IPDI/MDI 硬段的共聚、共混及混合异氰酸酯模型配置；每个系统 10 条链、约 16,110–17,860 个原子，并定义氢键、密度、Rg²、扩散和拉伸等 ML 特征；没有公开逐系统数值表或代码 | ACS 补充信息；出版商版权 | 重要竞争路线与 MD 协议参考；不能直接作为训练表，但能帮助设计更强的中间机制和对照 | [15] |
| `01_原始数据/外部数据/PUE_StressStrain_2026_ESI.pdf` | 2026 PUE 应力–应变 Transformer 方法与代码 | 69 页官方 ESI；含模型超参数、描述符物理意义及 Transformer/XGBoost/LSTM/GRU 的完整评估代码，但不含训练曲线表 | 出版商版权 | 曲线模型复现模板与最新基准；原始训练数据仍需向作者索取 | [16] |
| `01_原始数据/外部数据/WPU_DCR_2025_Source_Data.xlsx` | 高性能水性 PU 的全套源数据 | 22 个工作表；包含 HPWPUE 与 8 个配方变体的完整真实应力–应变曲线、3 个重复的强度/韧性、FTIR、SAXS/WAXS 和 DFT 坐标；多个曲线表达 16,000–27,000 行 | CC BY-NC-ND 4.0；允许非商业研究与引用，不分发改编版本 | 极高价值的“绿色制程—层级氢键—延迟结晶—高韧性”机理标定集 | [17] |
| `01_原始数据/外部数据/WPU_DCR_2025_Supplementary_Information.pdf` | 上述水性 PU 的完整实验与计算细节 | 17.18 MB 官方 SI；含原料、配方、分散/成膜、拉伸与散射测试、DFT 方法和放大验证信息 | CC BY-NC-ND 4.0 | 用于复现实验、提取配方与测试条件，不能用 Source Data 单独替代 | [17] |
| `01_原始数据/外部数据/TPU_HBond_2021_Source_Main.xlsx` | 碳酸酯型自修复 TPU 主图源数据 | 12 个工作表；含完整拉伸、FTIR、DMA/WAXS、温度依赖、循环与自修复相关数据 | CC BY 4.0 | 可合法再分析的 TPU 氢键阵列与应变诱导有序化基准 | [18] |
| `01_原始数据/外部数据/TPU_HBond_2021_Source_Supplementary.xlsx` | 上述 TPU 的补充图源数据 | 20 个工作表；包含多组 1,000–16,000 行曲线与重复数据 | CC BY 4.0 | 与主图数据联合用于曲线级和机制级模型 | [18] |
| `01_原始数据/外部数据/TPU_HBond_2021_Supplementary_Information.pdf` | 上述 TPU 的合成与测试细节 | 2.93 MB 官方 SI | CC BY 4.0 | 复现实验设计并把曲线回连到配方、温度和测试协议 | [18] |
| `01_原始数据/外部数据/PolyOmics_general.csv` | PolyOmics 通用计算聚合物数据 | 当前快照实测 95,335 行 × 255 列，覆盖单体/重复单元、DFT、Mn/Mw/PDI、密度、Rg、自扩散、热容、体积模量、介电、折射、导热、Tg、溶解度参数等 | 仓库 README 声明 CC BY 4.0，但 Hugging Face card 的结构化 license 字段为空；引用时保留这一差异 | 大规模 Sim2Real/低保真预训练，不是 TPU 配方实验库 | [20] |
| `01_原始数据/外部数据/PolyOmics_PURT.csv` | 从上述固定快照筛出的 PURT 子集 | 3,384 行 × 255 列；3,264 个唯一 `monomer_ID`；由 `class_PURT=True` 确定 | 派生子集仅内部使用；公开时需核对 CC BY 4.0 与数据卡元数据 | 当前最大的 PU 专用计算子集；适合计算表示预训练和模拟到实验迁移 | [20] |
| `01_原始数据/外部数据/PolyOmics_README.md` | PolyOmics 数据卡快照 | 对应 Hugging Face revision `43c8c74cac5bef00e7c3a6cca95a9fab9ba1979c` | README 声明 CC BY 4.0 | 固定许可与引用上下文 | [20] |
| `01_原始数据/代码仓库镜像/PolyGraphMT/data/raw/*.csv` | 论文整合约 62,000 个值、28 种属性、多保真 | 当前仓库 `data/raw` 核验到 21 个 CSV；包括 Young's modulus 1,012、Poisson 1,012、Tg 152、密度 1,935 等，大部分为 DFT/MD/基团贡献数据 | 论文 CC BY-NC 3.0；仓库未发现 LICENSE | 通用聚合物多任务/多保真预训练；不应冒充 TPU 实验数据 | [8] |
| `01_原始数据/代码仓库镜像/ADEPT` | 自动构建聚合物并进行 MD/DFT | 已克隆代码与工作流 | 论文 CC BY-NC 3.0；仓库未发现 LICENSE | 用作候选的自动化 MD/DFT 计算骨架，需先做 TPU 专用验证 | [8] |
| `01_原始数据/外部数据/PU18_Menon2019_figshare.zip` | 论文使用 18 个 PU 样品 | Figshare 压缩包仅 3,985 字节，解压后只有 4 个 Python 脚本；脚本引用的 `PU training dataset.xlsx` 并未包含在压缩包中 | Figshare/论文标为 CC BY 4.0 | 可审查算法结构，但当前不能据此复现 18 样本模型；需联系作者或从补充材料另行追索 | [2] |

### 2.3 新增的曲线、环境、加工和循环力学数据层

下表数据可以明显增强曲线编码、本构拟合、速率/温度/湿度迁移、循环耗散与加工约束，但大多是商业牌号、打印件、复合材料或交联 PUE。它们必须与“配方身份完整的线性 TPU 数据层”隔离，不能用数量优势掩盖化学标签缺失。

| 本地位置 | 实际获得内容 | 许可证 | 适合任务 | 禁止误用 | 引用 |
|---|---|---|---|---|---|
| `01_原始数据/外部数据/力学曲线/SelfHealingTPU_4TU/source_data.zip` | 36 条 SH-TPU/Ninjaflex 压缩切割力–位移曲线，覆盖原始/愈合、XY/XZ、225–235 °C；18 个 195–240 °C 流变 CSV；另有 DSC、FTIR、TGA 原始数据 | CC BY 4.0 | 熔融加工窗口、打印工艺、愈合保持率、多模态表征 | 只有 1 个实验 SH-TPU 配方和 1 个商业 TPU，且机械曲线不是标准拉伸应力–应变 | [21] |
| `01_原始数据/外部数据/力学曲线/Jiang2021_SHE/source_data.xlsx` | 27 个工作表；5 种交联度，原始/5 min 愈合，强度/模量 n=6，20% 应变 100 周期及恢复，25/37 °C 流变和干湿态数据 | CC BY 4.0 | 交联密度—力学—愈合—生理环境关系；循环表示学习 | 是交联医用 PU 弹性体，不是可熔融线性 TPU；仅 5 个配方 | [22] |
| `01_原始数据/外部数据/力学曲线/Li2026_Mechanophore/source_data.zip` | 20 个 XLSX；大应变循环、200% 应变后 0–600 min 恢复、DMA 与不同加载速率曲线；含 rotaxane mechanophore 梯度和多组对照 | CC BY 4.0 | 多通路耗散、滞回、恢复时间和速率效应 | 特殊交联/机械互锁体系，成本和合成门槛高；不能当常规 TPU 配方库 | [23] |
| `01_原始数据/外部数据/力学曲线/Schwarz2022_EPU40/Raw_Data.xlsx` | 45 条干燥/浸水/物理老化应力–伸长曲线及失效汇总、扩散数据 | CC BY 4.0 | 环境老化、曲线域迁移、Arruda–Boyce 等本构验证 | EPU 40 为光固化 3D 打印弹性聚氨酯，不是传统热塑性 TPU 化学筛选数据 | [24] |
| `01_原始数据/外部数据/力学曲线/TPU95A_2026/*.csv` | 12 个原始 CSV：3 拉伸、3 压缩、6 松弛；商业 TPU-95A 的 TPMS/打印力学数据 | CC BY 4.0 | 应变率、松弛和几何/工艺迁移 | 商业牌号化学组成未知；不能用于单体或配方逆向设计 | [25] |
| `01_原始数据/外部数据/力学曲线/Zenodo4156000/*.csv` | 官方记录全部 15 个 CSV：TPU/TPS 导电丝材的断裂、准静态与动态拉伸、电阻响应及不同喷嘴/加载条件 | CC BY 4.0 | 机电耦合曲线、传感应用、打印过程约束 | 导电商业 TPU/TPS，结构标签不足，不进入主化学性能模型 | [26] |
| `01_原始数据/外部数据/力学曲线/Zenodo1098206/Supronics_Porous-TPU-Nanocomposites Dataset.xlsx` | 多孔导电 TPU 纳米复合膜的 FTIR、拉伸、导电、压阻、阻力–应变与孔隙率 | 开放数据记录；论文 CC BY 4.0 | 可穿戴传感应用、力电耦合多目标评价 | 纳米复合与孔结构效应占主导，不代表本征线性 TPU | [27] |
| `01_原始数据/外部数据/力学曲线/Zenodo15490464/` | 4 个 XLSX 和原始 ZIP；纯 TPU 各向异性/加载速率曲线及 TPU/竹炭/连续纤维复合材料曲线 | CC BY 4.0 | 速率、打印方向和复合增强的外部验证 | 关联论文仍“in preparation”，材料元数据有限；暂不作为高置信化学训练集 | [28] |
| `01_原始数据/外部数据/力学曲线/TPU_literature_ftntxg4zdz/TPU_literature_Fig18_Fig19.xlsx` | 热塑性聚氨酯力学文献数据库所附 Fig. 18/19 数值表；另有独立参考文献 RIS | CC BY 4.0 | 文献覆盖图、性能区间和后续逐文献追溯种子 | 只是图 18/19 的数值，不是完整配方—条件—曲线库；必须回到原论文核验 | [34] |
| `01_原始数据/仅供参考/受限来源/DiMPU2025/source_data.xlsx` | 71 个工作表；金属–吡唑 PU 的工程/真实应力–应变、循环、DMA/流变、原位 SAXS 与 DFT 坐标 | CC BY-NC-ND 4.0 | 仅作内部机制比较和外部测试 | 不生成或公开派生训练集，不重新分发改编数据 | [29] |
| `01_原始数据/仅供参考/受限来源/PUN2026/source_data.xlsx` | 19 个工作表；动态解交联 PUN 的拉伸、循环、DMA、应力松弛及再加工曲线 | CC BY-NC-ND 4.0 | 仅作可修复、再加工和升级回收机制参照 | 热固性动态网络而非 TPU，且 ND 许可；不得并入公开派生数据集 | [30] |

### 2.4 TPU 数据库 v0.1 首次可复现构建

首轮正式构建快照为 `snapshot_3195c290d7dc2d44`，管道标识为 `pipeline_94e0bb066d425128`（`tpu-db/0.1.1`）。它用于验证“候选结构—变换特征—实验曲线—加工曲线”四条数据路径，不代表现有数据已经足以训练可外推的新 TPU 配方终模型。

| 垂直切片 | 暂存结果 | 规范/派生结果 | 公开再分发门控 |
|---|---:|---:|---|
| SMiPoly 候选单体 [7] | 1,083 条来源记录 | 按 `chemical_id` 合并为 1,071 个唯一候选；角色与官能度保持未分类 | BSD-3-Clause；可进入候选结构公开视图，但不得表述为 TPU 实验标签 |
| PUE 326 基准 [1], [12] | 326 条变换特征记录 | 326 个稳定 `lineage_record_id` 与 `split_group`；不尝试在缺少缩放参数时反演原始配方 | 许可证未知；公开视图 0 条，只在本地辅助表使用 |
| Eom 氢键 TPU [18] | 53 条曲线、25,972 个点、19 个标量；另有 6 个未映射 sheet 审计项 | 16 条拉伸曲线派生 48 个强度、断裂伸长与韧性指标 | CC BY 4.0；公开视图保留 53 条曲线、25,972 个点、19 个标量和 48 个派生指标 |
| 预聚体温度—黏度 [4] | 39 条曲线、4,559 个点；61 个空 sheet 审计项 | 单位规范为 K 与 Pa·s；明确标记为没有 specimen/test 链的加工辅助数据 | 仓库许可证未知；公开视图 0 条，只在本地使用 |

本轮 QC 对主键、来源追溯、有限数值、PUE 谱系拆分、曲线点数、单位状态、拉伸派生覆盖和公开发布门进行了检查，结果为 0 个错误、2 个聚合警告：28 条 Eom 流变曲线及 1,036 个曲线点缺少足够轴单位证据。发布门同时校验许可证、衍生/再分发权、来源状态和访问限制；同一候选结构汇集多来源时采用保守许可继承。相同输入与构建参数的真实重建中，54 个 Parquet/JSON/CSV 基准文件哈希完全一致；DuckDB 因内部存储元数据不保证字节级哈希一致，只作为可由这些 Parquet 重建的查询缓存。详细结果见[质量报告](质量报告/TPU数据库_v0.1_质量报告.md)、[数据集卡](质量报告/TPU数据库_v0.1_数据集卡.md)和本地 Excel 审核目录。

## 3. 文件版本与完整性指纹

### 3.1 GitHub 私人仓库与固定提交

本项目代码、结构定义、配置、清单和文档维护在 [fafasco16/TPU-HighThroughput-Screening](https://github.com/fafasco16/TPU-HighThroughput-Screening)。已通过 GitHub CLI 核验仓库可见性为 `PRIVATE`、默认分支为 `main`；`01_原始数据/`、受限附件及本地生成的大体积数据不推送。私人仓库属性不改变任何第三方数据的许可证或再分发限制。

本轮使用本机 GitHub CLI `gh 2.96.0` 完成访问和克隆，因此不依赖 Codex GitHub 插件。以下第三方仓库提交均已固定：

| 仓库 | 固定提交哈希 |
|---|---|
| ADEPT | `5bbf4bbd29f545ca9bca8841efbea31a65219d34` |
| PolyGraphMT | `ae8641d4d969eb814fe86b838ca5d222901479ca` |
| DQ | `9964cc749955290c38fe9dd00c756d55624f9148` |
| MatImpute | `f3f5270a5d2711c433832b19d1542b864f8b4ff8` |
| viscosity-modeling | `57ede2b22f964a99d6467d627236c37d0a7231d2` |

### 3.2 下载文件 SHA-256

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `01_原始数据/外部数据/PUE643_2023_ESI.pdf` | 1,041,624 | `7147B653CE1B00E8B65ABAD45A511CBAB21CB05CC65D79D4DC9B3C93DCB404EC` |
| `01_原始数据/外部数据/Nature2025_Supplementary_Information.pdf` | 14,060,419 | `53F188B97BF60E71D3A70D7EA091833CC84436A7FF45EAADCBF88B8D15F779B3` |
| `01_原始数据/外部数据/Nature2025_Supplementary_Data.xlsx` | 71,867 | `B1FC2E574A60F42AFD5165FB2518960DAC242E25F3F258D916E32F981B8CB311` |
| `01_原始数据/外部数据/Nature2025_Source_Data.xlsx` | 771,749 | `C73FF1DFEB1F4F1ADB94E61F6AD133275CF9853EC6D10AE6AA6DBF2AA72372F0` |
| `01_原始数据/外部数据/PU18_Menon2019_figshare.zip` | 3,985 | `B1958507976B8782438534EBDC328BDDB3CF12E3FBF66518500646643B9E0BFD` |
| `01_原始数据/外部数据/jp0c06439_si_002.xlsx` | 28,335 | `AF6D505D532984DEDD45ACA9C0CF1133B393D97FD900D332961AFBFE62C4F797` |
| `01_原始数据/外部数据/jp0c06439_si_003.xlsx` | 18,771 | `B1CF00E8F64CCF54C0168037E8703B081C28B2933BF2EC3FA3F43C3B429567A0` |
| `01_原始数据/外部数据/am1c24715_si_002.xlsx` | 219,154 | `858201B64185804336CCE7AEB45498371DA573B588505E6B3F984C3A0C4DE95D` |
| `01_原始数据/外部数据/ma4c02559_si_001.pdf` | 1,663,615 | `6D1A99C377409C246D525ABB2DD64469B2D509C22CE54367F1035792B7C07A82` |
| `01_原始数据/外部数据/PUE_StressStrain_2026_ESI.pdf` | 7,781,386 | `729BC51155959D07695F2B0253390AAC8ADFF834F074033863421D2E9371B4BD` |
| `01_原始数据/外部数据/WPU_DCR_2025_Source_Data.xlsx` | 20,278,510 | `D12B1724A94013F4476177526AC7A29F57AFD2F2079A8759137A98BAB7023CF3` |
| `01_原始数据/外部数据/WPU_DCR_2025_Supplementary_Information.pdf` | 17,181,670 | `FE1DBCFB8D78AD8FFD59897A9A75051CB34E377A4D03A3BA07D82AFD2FF53F50` |
| `01_原始数据/外部数据/TPU_HBond_2021_Source_Main.xlsx` | 1,636,536 | `A71E665B656D471A59EA814D44C12D73A7F7E9C2D1E23044FFB6DC4B572BF8C4` |
| `01_原始数据/外部数据/TPU_HBond_2021_Source_Supplementary.xlsx` | 3,564,847 | `946CB14E4221F28E10C44C154D4FED23B6A66BC34C706A9BB18BA49650A1BF7D` |
| `01_原始数据/外部数据/TPU_HBond_2021_Supplementary_Information.pdf` | 2,928,152 | `2AE0E789E5833FA81AD0E9522540F36B7F604640CCE5D43D8C0E871B3B5876CF` |
| `01_原始数据/仅供参考/Wiley补充材料/app51269-sup-0001-supinfo.docx` | 2,290,729 | `1B5EB0EEF7154848B5E2ADA25B655B1BF68990C210CF43A53EDE9D14EF8A0A86` |
| `01_原始数据/外部数据/PolyOmics_general.csv` | 190,382,682 | `E230BD86499559B68B3FD20E7D7FDB538558CCF62463386F981C544953D0C853` |
| `01_原始数据/外部数据/PolyOmics_PURT.csv` | 6,850,996 | `AE004EB5FF3415DB7D262824008957628CEA115B398873EBD38D2C6EABCC8047` |
| `01_原始数据/外部数据/PolyOmics_README.md` | 1,267 | `81D6962E1462EDCDFF16AF0580B32C9DD3A43BFEC84B286C99B88405F2C92DAE` |
| `01_原始数据/外部数据/am1c24715_si_001.pdf` | 205,845 | `19B9386D570EF1543118EEA5C0D337FCA54C1D13B645FB1EDE6FAA48B1DC4DA8` |
| `01_原始数据/外部数据/力学曲线/SelfHealingTPU_4TU/source_data.zip` | 546,535 | `9D563B8389686530A1A73E62A0244C57A1C19B8A039B60EC63F0753B2FF034A8` |
| `01_原始数据/外部数据/力学曲线/Jiang2021_SHE/source_data.xlsx` | 1,528,785 | `5A48AE982E2280AEDF4DF50106C1AC270A84434B1549E823DB1E0DC5DF342F97` |
| `01_原始数据/外部数据/力学曲线/Li2026_Mechanophore/source_data.zip` | 8,324,133 | `6CC024A891D3FB46CCFD7D72E5971D38BE76DA5252AB7255005BDE70A5350EB2` |
| `01_原始数据/外部数据/力学曲线/Schwarz2022_EPU40/Raw_Data.xlsx` | 2,145,753 | `2E782DD443B5F8B09EAB3D5A4EBC78E7071D02B474F90048C1683C2EEB01C9F9` |
| `01_原始数据/外部数据/力学曲线/Zenodo1098206/Supronics_Porous-TPU-Nanocomposites Dataset.xlsx` | 750,592 | `11967ACF1DEEC0CE05AD2D1E63B70738C4357345FAE9DD708952A75630304BC8` |
| `01_原始数据/外部数据/力学曲线/Zenodo15490464/TPU_BC_CFF_raw.zip` | 9,289,211 | `B5A4DE515170F580962BBEA08438D1C893E67039A2A7854542D56D6AFCA120B0` |
| `01_原始数据/外部数据/力学曲线/TPU_literature_ftntxg4zdz/TPU_literature_Fig18_Fig19.xlsx` | 16,585 | `7BBB8F5BF02C3F1C37FF0F1CDDEE8423AE65EE54F13D2D8255D19AAD3A7B271B` |
| `01_原始数据/仅供参考/受限来源/DiMPU2025/source_data.xlsx` | 55,175,262 | `5052109A943F6E2F9873A88B2A95FAC06A4BCE8F41A0EAA81822082EECED0FE6` |
| `01_原始数据/仅供参考/受限来源/PUN2026/source_data.xlsx` | 18,398,993 | `AC912FFF7DCC5EC4259B0ADF1E314E043F6BDD9046B6C8DE484AF62E0CB0D37A` |

`Zenodo4156000` 已按官方 API 获取全部 15 个文件（合计 9,872,045 字节），`TPU95A_2026` 含 12 个 CSV（合计 3,824,891 字节）；单文件哈希可从本地重新计算，正式发布前将生成独立机器可读 checksum manifest。

## 4. 尚未获得或尚不完整的高价值来源

| 优先级 | 数据来源 | 价值 | 当前障碍 | 下一步 |
|---|---|---|---|---|
| A | 生物基 PUE 数据集：超过 1,500 个样品、26 个特征、6 个输出（YM、TS、EB、Tg、Td5、tanδ） | 最适合构建可持续高性能 TPU 的多目标模型 | Wiley 附件 `marc70252-sup-0002-DataFile.zip` 被站点下载策略阻止，尚未落地 | 需要人工浏览器下载一次，随后做字段、重复、来源和许可证审计 | [9] |
| A | 生物基含量数据：506 条 BPUE 样条 | 含 YM、TS、EB、Tg 与生物基质量分数，可做 BBC% 回归/分层；论文报告 BBC% 预测 R² = 0.89 | 两个官方 XLSX 附件均被 Wiley Cloudflare 阻止自动下载；非 CC，不能公开再分发 | 需要人工下载 Dataset S6/S7；与 >1,500 样本集做来源重叠与泄漏审计 | [19] |
| A | PUE-643 完整原始数据与 386 条应力–应变曲线 | 与本课题目标最直接；可训练曲线级模型和韧性标签 | 官方 ESI 只给出说明，没有可读取的完整表；公开 GitHub 目前只有 326 条变换后子集 | 搜索作者数据仓库、补充附件镜像；必要时给通讯作者发送数据请求 | [1] |
| A | 3D-Weighted-Matrix 多模态 PU 数据与 1.5 亿组合筛选结果 | 结构与合成工艺融合，论文报告 YM/TS/EB 平均 R² > 0.86；与本课题直接竞争 | 官方 Data Availability 明确写“合理请求后由通讯作者提供”；公开补充材料只有约 2.3 MB 的方法 DOCX，没有训练表/筛选候选/代码 | 需要正式向通讯作者申请；即使未获数据，也必须分析其设计空间以避免路线重复 | [14] |
| A | 2026 PUE 应力–应变 Transformer 训练数据 | 最新曲线级建模，论文报告 R² = 0.79、RMSE = 5.82，并做实验确认 | 官方 ESI 已取得，含完整模型代码但没有训练曲线；搜索结果显示的“CSV”没有可核验的实际下载链接 | 联系作者索取训练曲线；现有 ESI 用作模型基准和新颖性对照 | [16] |
| A | ScienceDB 2026 PU 应力–应变机器学习数据 `datasets.csv` | 撤回前文件元数据显示 3,044,630 字节，含化学结构描述符、硬段含量 HSC 与整条应力–应变曲线点；直接对应 [16,35] | ScienceDB 于 2026-07-02 撤回该 DOI 的全部版本，页面已无下载入口；公开理由为确保数据正确使用及知识产权合规，现仅接受邮件逐案授权 | 向 `zhoul0213@126.com` 提交申请，说明个人/机构信息和预期用途；获批后核对大小及 MD5 `dc28ea5ce05566288cf7c0d97903f30e` 再入库 | [16,35] |
| A | 2021 PUE 529 条应力–应变曲线及 25 个本构模型评估 | 是 PUE-643/386 数据链的重要前身；补充材料可复用本构模型、特征字典、聚类检验和拟合统计 | 2.29 MB 官方 DOCX 已取得并校验，但包内没有 CSV/XLSX/OLE 附件，也没有 529 条原始曲线点；仅含 5 张 TIFF 图、5 个统计/方法表及文字说明 | 已归档到 `01_原始数据/仅供参考/Wiley补充材料/`；联系作者索取机器可读曲线，再做 529→643→386→326 数据谱系审计 | [31] |
| B | PolyOmics PURT MD 快照 | 5,084,441,903 字节的 PURT 结构轨迹，可用于 MLIP/形貌表征与复算 | 约 5.08 GB，尚未下载；当前还未确认是否需要全量快照 | 先用 3,384 行属性子集做基线；只有在选定 MLIP/轨迹模型后再下载 | [20] |
| B | OPoly26 聚合物量子计算数据 | 超过 6.57 百万次 B97M-V/def2-SVP DFT 计算、总计超过 12 亿个原子，覆盖链长、架构、共聚、溶剂与反应环境 | Hugging Face 训练分片约 6.11 百万行且体量巨大；不是 TPU 终性能标签，数据卡当前显示 `other` 许可而非明确 CC BY | 先使用发布模型/小规模子集评估 TPU 片段域内误差；确有收益后再下载训练分片或做 TPU 定向微调 | [32] |
| B | 1,402 个水性聚氨酯涂层数据向量、6 个性能 | 可补充配方—工艺—机械/疏水/光学关系 | 尚未确认公开数据附件；出版商页面对文本与数据挖掘有权利限制 | 仅做路线参考，优先寻找作者明确公开的数据文件 | [10] |

### 4.1 需要人工下载的官方附件入口

- BPUE >1,500 样本：`marc70252-sup-0002-DataFile.zip`  
  https://onlinelibrary.wiley.com/action/downloadSupplement?doi=10.1002%2Fmarc.202500963&file=marc70252-sup-0002-DataFile.zip
- BPUE 506 样本 Dataset S6：`marc202500054-sup-0002-DatasetS6.xlsx`  
  https://onlinelibrary.wiley.com/action/downloadSupplement?doi=10.1002%2Fmarc.202500054&file=marc202500054-sup-0002-DatasetS6.xlsx
- BPUE 506 样本 Dataset S7：`marc202500054-sup-0003-DatasetS7.xlsx`  
  https://onlinelibrary.wiley.com/action/downloadSupplement?doi=10.1002%2Fmarc.202500054&file=marc202500054-sup-0003-DatasetS7.xlsx
上述三个 URL 均是出版商官方附件。自动下载收到 HTTP 403，必须由用户在正常浏览器中手动点击一次；不得通过绕过访问控制的方式获取。

### 4.2 已获取但不含原始曲线的 Wiley 补充材料

- Ding 等 [31] 的官方附件：`app51269-sup-0001-supinfo.docx`  
  出版商入口：https://onlinelibrary.wiley.com/action/downloadSupplement?doi=10.1002%2Fapp.51269&file=app51269-sup-0001-Supinfo.docx  
  本地归档：`01_原始数据/仅供参考/Wiley补充材料/app51269-sup-0001-supinfo.docx`
- 文档包审计结果：164 个段落、5 个表格、5 张 TIFF 图；无嵌入式 CSV、XLSX、OLE 对象或图表工作簿。
- 有价值内容：25 个本构模型及参数表、材料/工艺特征字典、聚类显著性检验、529 条曲线的拟合耗时与整体拟合统计。文档报告 500 条曲线拟合良好（94.5%）、29 条拟合较差（5.5%）。
- 不含内容：529 条曲线的逐点应力—应变数据、样品级完整配方表或可直接训练的机器可读数据。因此该文件只能作方法、数据谱系和统计证据，不能当作曲线训练集。

### 4.3 ScienceDB 撤回状态与申请入口

- 数据页：https://www.scidb.cn/detail?dataSetId=3d57444e27944678a99879205a20f595
- 数据 DOI：https://doi.org/10.57760/sciencedb.j00189.00062
- ScienceDB 撤回声明（页面核验日期：2026-07-18）：平台于 2026-07-02 撤回全部版本，以进一步确保数据的正确使用及知识产权法规合规；下载按钮已被撤回声明替代。
- 当前唯一公开获取路径：邮件联系 `zhoul0213@126.com`，申请中提供个人与机构信息、数据集预期用途和目的；作者将按知识产权保护要求逐案评估。
- 撤回前记录的目标文件为 `datasets.csv`（3,044,630 字节；MD5 `dc28ea5ce05566288cf7c0d97903f30e`）。旧直链仅作数据谱系记录，不再视为有效下载入口：  
  https://download.scidb.cn/download?fileId=2d895b01c4545e91048684e6b56d3f1b&path=/V1/datasets.csv&fileName=datasets.csv

## 5. 数据质量判定

### 5.1 证据等级

| 等级 | 定义 | 是否进入最终主模型 |
|---|---|---|
| A | 作者公开原始表格/曲线，具有 DOI、样品身份、配方、过程和测试条件 | 是，作为高保真数据 |
| B | 作者公开的机器可读表格，但缺少部分条件或仅有摘要特征 | 可以，降低权重并标注缺失 |
| C | 从 PDF 表格人工/OCR 提取，能追溯到页码、表号和单位 | 可以，需双人或双流程复核 |
| D | 从图中数字化曲线或散点 | 仅作辅助，保留像素误差与提取工具版本 |
| E | DFT/MD/基团贡献或 ML 预测值 | 作为低保真监督或特征，不与实验真值混同 |
| X | 来源不明、无法追溯、许可证不允许或关键上下文缺失 | 否 |

### 5.2 当前资产的结论

- 现有文件足以开始搭建“通用聚合物预训练 + TPU 小样本校准 + 机理计算”的原型，但不足以直接训练一个能可靠外推到新 TPU 配方的终模型。
- 主要短板不是 SMILES 数量，而是 TPU 样品级的**配比、官能度、分子量分布、NCO/OH、催化剂、含水量、反应温度/时间、退火条件、试样制备和测试速率**。
- 韧性必须优先由完整应力–应变曲线积分得到：

  \[
  U_T=\int_0^{\varepsilon_b}\sigma(\varepsilon)\,\mathrm d\varepsilon
  \]

  当应力以 MPa、应变为无量纲时，积分结果数值单位为 MJ·m\(^{-3}\)。只用拉伸强度或断裂伸长率不能代表韧性。
- `PI1M_v2.csv` 和 SMiPoly 适合扩展候选空间，不能作为“实验性能数据库”；PolyGraphMT 适合通用低保真先验，不能替代 TPU 相分离、氢键与加工历史的专用建模。

## 6. 建议的数据库对象结构

不建议继续把所有信息塞在一张宽表中。建议至少拆分为以下对象，并用稳定 ID 关联：

1. `source`：论文、数据 DOI、URL、许可证、下载日期、文件哈希。
2. `chemical`：规范名称、CAS、canonical/isomeric SMILES、InChIKey、官能团、官能度、EHS、供应商。
3. `material_lot`：批号、纯度、含水量、Mn/Mw/PDI、羟值/NCO 含量。
4. `formulation`：各组分质量/摩尔/当量、硬段含量、NCO/OH、催化剂和添加剂。
5. `synthesis_batch`：一步法/预聚体法、加料顺序、温度、时间、气氛、脱泡、转化率。
6. `specimen`：成型、厚度、退火、调湿、老化历史。
7. `test`：标准、仪器、温度、湿度、应变率、重复编号、原始数据文件。
8. `property_value`：数值、单位、误差、统计量、fidelity、是否从曲线派生。
9. `raw_curve`：应力–应变、DMA、黏度–温度、循环拉伸等长表数据。
10. `computation`：结构模型、DFT/MD 软件、版本、方法、力场、边界条件、收敛与不确定度。
11. `prediction`：模型版本、训练数据快照、外推域、均值、不确定度、筛选排名。

## 7. 路线比较与当前主路线

### 7.1 先排除已经“撞题”的宽泛创新表述

以下内容可以作为技术组件，但已经不足以单独支撑一区论文的新颖性：

- “DFT 小分子相互作用 + 聚合物力学预测”已经由 [3] 做出完整闭环；
- “全原子 MD 氢键/密度/Rg/扩散 + 可解释 ML”已经由 [15] 系统研究；
- “结构 + 工艺多模态编码 + 超大虚拟空间筛选”已经由 [14] 覆盖；
- “20 个左右实验 + 主动学习 + 多目标自修复 PU 优化”已经由 [33] 发表；
- “生物基 PU + 常规 ML 多性能预测”已有 [9], [19]，仅换算法或扩大候选数量不够。

因此，本文的核心不能写成泛泛的“DFT + MD + ML 高通量筛选”。真正需要建立的是：**分段 TPU 配方图表示 → 动态竞争氢键/相分离与应变诱导有序化 → 加工窗口和循环疲劳约束 → 校准不确定度的多保真决策 → 少量真实合成验证**。

### 7.2 路线 A：真正线性分段 TPU 的“时序耗散—延迟有序化”筛选（当前首选）

目标不是复制已发表的高强度交联 PUE，而是在可熔融/可溶液加工、双官能、近线性的 TPU 平台上，筛选具备以下时序机制的扩链剂/硬段组合：低应变时可逆竞争氢键和链段滑移耗散能量，高应变时硬段连通或应变诱导有序化接管承载，卸载后又能部分恢复。

建议工作包：

1. **候选定义**：固定 1–2 种可购软段与 1–2 种二异氰酸酯，先在双官能扩链剂/少量共扩链剂空间中搜索；每个候选明确 NCO/OH、硬段含量、理论 Mn、原料水分、催化剂和合成路线。
2. **DFT 层**：不只算单一最稳二聚体，而是枚举竞争氢键构型、构象简并、结合能分布、偶极/静电势、扭转势垒和温度相关自由能近似；对合成可行且未被充分报道的前列片段进行更高精度复算。
3. **MD 层**：使用多链、多重复单元和多初始构型，提取氢键寿命/交换率、硬段团簇连通度、结构因子/域尺寸、链段取向、自由体积、内聚能密度、Tg、扩散和拉伸下的结构演化；必要时以 OPoly26/PolyOmics 预训练势能或表征，但必须在 TPU 片段上验证误差。
4. **ML 层**：采用“二异氰酸酯—软段—扩链剂—比例—工艺”的分层配方图，而不是把最终重复单元压成一个 SMILES；分别学习终点性能和整条曲线的潜在表示，使用分组/留化学体系外验证与 conformal/ensemble 不确定度。
5. **Pareto 目标**：韧性、强度、断裂伸长、循环残余应变/滞回恢复、DMA 储能/损耗、Tg、熔体/预聚体黏度、热稳定性、EHS、价格和供应可得性；禁止只优化拉伸强度。
6. **实验闭环**：第一阶段不做“大规模实验建库”，而是选 8–12 个最大信息增益样品，至少包含高分候选、机制消融对照和模型认为不确定的样品；保留失败合成、凝胶、不可加工和低性能结果。第二阶段再根据不确定度选择 3–6 个增量样品。

这条路线的潜在一区贡献是：在**真正 TPU 的线性、可加工边界**内，把“动态非共价相互作用的时序切换”量化为可计算、可学习、可用原位/离线表征验证的中间机制，并证明它同时改善韧性、循环恢复和加工性。高结合能本身不是成功判据；SAXS/WAXS、变温/变形 FTIR、DMA、循环拉伸和流变必须验证中间机制。

### 7.3 路线 B：水性延迟结晶响应 + 生物基替换（高潜力备选，但需明确范围）

以 [17] 的 DCR 水性 PU 为机制标定，用 [9], [19] 的生物基数据建立可持续性先验，筛选生物基软段/扩链剂替换后仍保留延迟结晶响应的体系。该路线绿色制程、强韧性和应用叙事都很强，但研究对象更接近水性 PUE/WPU，而不是狭义熔融加工 TPU；若论文标题坚持 TPU，应将它作为对照或后续分支，而不是悄悄扩大材料定义。

### 7.4 路线 C：加工与疲劳不是独立路线，而是 A/B 的硬门槛

用预聚体温度–黏度 [4]、4TU 打印/流变 [21]、商业 TPU 速率/松弛 [25], [26] 和公开循环曲线建立辅助模型。它们不能提供新化学配方身份，但可以让筛选器拒绝“计算性能很高、实际无法混合/浇注/熔融加工”或“首圈很强、循环迅速失效”的候选。

### 7.5 当前决策

1. **主线：路线 A，线性分段 TPU 的时序耗散—延迟有序化**。
2. **绿色备线：路线 B；只有在确认课题允许 WPU/PUE 范围后升级为主线**。
3. **路线 C 作为所有候选的强制门槛**。
4. “未有人研究过”不能在候选生成阶段口头保证；进入最终合成前，必须对具体结构、配比和用途做论文、专利、供应商技术资料和结构相似性检索，形成逐候选 novelty dossier。

## 8. 下一阶段数据工作清单

- [x] 建立 `01_原始数据/`、`02_暂存数据/`、`03_规范数据/`、`04_派生数据/`、`05_数据库快照/`、`06_审核导出/` 中文分层，并在 `01_原始数据/仅供参考/` 隔离限制性材料、商业牌号和复合材料曲线。
- [x] 获取 CC BY 4.0 的真实 TPU/PUE 曲线、循环、温度、湿态、流变和打印工艺数据，作为曲线/过程辅助层。
- [ ] 取得并审计 Wiley 生物基 PUE 数据附件。
- [x] 人工下载并审计 Ding 2021 官方 DOCX；确认其只有方法/统计结果，不含 529 条机器可读原始曲线。
- [ ] 向 Ding 2021/PUE-643 作者追索 529/643 条原始数据和 386 条清洗曲线，并做 529→643→386→326 数据谱系审计。
- [ ] 向 ScienceDB 通讯作者提交 `datasets.csv` 学术用途访问申请，获批后进行哈希、许可证和样品级泄漏审计。
- [ ] 从 Eom 2021、Nature 2025、DCR 2025、4TU SH-TPU、Jiang 2021 和 Li 2026 生成规范化 `formulation`、`test`、`raw_curve`、`derived_property` 表。
- [ ] 将预聚体黏度和 4TU 熔体流变转成长表，拟合 Andrade/WLF/Carreau–Yasuda 等候选模型并明确外推边界。
- [ ] 为 1,083 个 SMiPoly 单体补齐 TPU 角色、官能度、EHS、供应可得性和价格等级。
- [ ] 对 OpenPoly、PI1M、PolyGraphMT、PolyOmics PURT 和 OPoly26 做结构标准化、重复/泄漏检查和 TPU 化学域覆盖分析；先做小规模效益试验，再决定是否下载 5.08 GB/大分片。
- [ ] 建立文献数据提取模板，要求每个数值绑定 DOI、页码/表图号、单位、样品和条件。
- [ ] 定义第一版线性 TPU 平台和硬约束：双官能、凝胶风险、NCO/OH、硬段范围、Mn、黏度窗口、EHS、成本和原料交期。
- [ ] 对首批扩链剂/硬段候选做论文+专利新颖性检索，禁止在检索前宣称“无人研究”。
- [ ] 预注册首批实验验证目标、对照、成功/失败判据和最少重复数，避免只报告成功候选。

## 9. 参考文献

[1] Ding, F.; Liu, L.-Y.; Liu, T.-L.; Li, Y.-Q.; Li, J.-P.; Sun, Z.-Y. Predicting the Mechanical Properties of Polyurethane Elastomers Using Machine Learning. *Chinese Journal of Polymer Science* **2023**, *41*, 422–431. https://doi.org/10.1007/s10118-022-2838-6.

[2] Menon, A.; Thompson-Colón, J. A.; Washburn, N. R. Hierarchical Machine Learning Model for Mechanical Property Predictions of Polyurethane Elastomers From Small Datasets. *Frontiers in Materials* **2019**, *6*, 87. https://doi.org/10.3389/fmats.2019.00087.

[3] Wang, L.; Zhang, K.; Hou, K.; Xia, Y.; Wang, X. Bridging Small Molecule Calculations and Predictable Polymer Mechanical Properties. *Nature Communications* **2025**, *16*, 6957. https://doi.org/10.1038/s41467-025-62449-8.

[4] Pugar, J. A.; Gang, C.; Millan, I.; Haider, K.; Washburn, N. R. Machine Learning of Polyurethane Prepolymer Viscosity: A Comparison of Chemical and Physicochemical Approaches. *Digital Discovery* **2025**, *4*, 3652–3661. https://doi.org/10.1039/D5DD00287G. Data and code: https://github.com/joepugar/viscosity-modeling.

[5] Wang, J.-F.; Sun, Y.-B.; Chen, Q.-T.; Ji, F.-F.; Song, Y.-Y.; Ruan, M.-Y.; Wang, Y. OpenPoly: A Polymer Database Empowering Benchmarking and Multi-property Predictions. *Chinese Journal of Polymer Science* **2025**, *43*, 1749–1760. https://doi.org/10.1007/s10118-025-3402-y.

[6] Ma, R.; Luo, T. PI1M: A Benchmark Database for Polymer Informatics. *Journal of Chemical Information and Modeling* **2020**, *60*, 4684–4690. https://doi.org/10.1021/acs.jcim.0c00726.

[7] Ohno, M.; Hayashi, Y.; Zhang, Q.; Kaneko, Y.; Yoshida, R. SMiPoly: Generation of a Synthesizable Polymer Virtual Library Using Rule-Based Polymerization Reactions. *Journal of Chemical Information and Modeling* **2023**, *63*, 5539–5548. https://doi.org/10.1021/acs.jcim.3c00329. Code: https://github.com/PEJpOhno/SMiPoly.

[8] Alosious, S.; Liu, Y.; Xu, J.; Liu, G.; Zhang, R.; Jiang, M.; Luo, T. ADEPT-PolyGraphMT: Automated Molecular Simulation and Multi-Task Multi-Fidelity Machine Learning for Polymer Property Generation and Prediction. *Digital Discovery* **2026**, advance article. https://doi.org/10.1039/D6DD00206D. Code: https://github.com/sobinalosious/ADEPT and https://github.com/sobinalosious/PolyGraphMT. Archived data/software: https://doi.org/10.5281/zenodo.20631234 and https://doi.org/10.5281/zenodo.20631261.

[9] Li, R.; Lv, Y.; Xie, C.; Liu, L.; Ao, Q.; Li, Z.; Li, C.; Li, Y. Explore Thermal and Mechanical Properties of Biobased Polyurethane Elastomers Through Machine Learning Models. *Macromolecular Rapid Communications* **2026**, *47*. https://doi.org/10.1002/marc.202500963.

[10] Liu, L.; Li, R.; Xie, C.; You, Y.; Chen, Q.; Xie, H.; Qin, M.; Li, Y. A Big Data Approach to Explore Core Properties of Waterborne Polyurethane Coatings. *Progress in Organic Coatings* **2026**, *211*, 109739. https://doi.org/10.1016/j.porgcoat.2025.109739.

[11] Pugar, J. A.; Gang, C.; Huang, C.; Haider, K. W.; Washburn, N. R. Predicting Young's Modulus of Linear Polyurethane and Polyurethane-Polyurea Elastomers: Bridging Length Scales with Physicochemical Modeling and Machine Learning. *ACS Applied Materials & Interfaces* **2022**, *14*, 16568–16581. https://doi.org/10.1021/acsami.1c24715.

[12] Xie, C.; Li, R.; Li, Y.; Xie, H.; Liu, Q. Imputation of Missing Data in Materials Science through Nearest Neighbors and Iterative Predictions. *Journal of Chemical Theory and Computation* **2025**, *21*, 70–78. https://doi.org/10.1021/acs.jctc.4c01237.

[13] Pugar, J. A.; Childs, C. M.; Huang, C.; Haider, K. W.; Washburn, N. R. Elucidating the Physicochemical Basis of the Glass Transition Temperature in Linear Polyurethane Elastomers with Machine Learning. *The Journal of Physical Chemistry B* **2020**, *124*, 9722–9733. https://doi.org/10.1021/acs.jpcb.0c06439. Data files: https://doi.org/10.1021/acs.jpcb.0c06439.s002 and https://doi.org/10.1021/acs.jpcb.0c06439.s003.

[14] Zhou, S.; Zhao, W.; Wan, Z.; Qiu, H.; Huang, X.; Sun, Z.-Y. Multimodal Machine Learning with 3D-Weighted-Matrix Encoding for High-Throughput Design of High-Performance Polyurethanes. *Macromolecular Rapid Communications* **2026**, *47*, e00471 (published online 2025). https://doi.org/10.1002/marc.202500471.

[15] Meng, Y.; Lin, Y.; Zhang, A. Prediction and Explanation of Properties in Multicomponent Polyurethane Elastomers: Integrating Molecular Dynamics and Machine Learning. *Macromolecules* **2024**, *57*, 10912–10925. https://doi.org/10.1021/acs.macromol.4c02559. Supporting information: https://doi.org/10.1021/acs.macromol.4c02559.s001.

[16] Zhou, L.; Wang, M.-F.; Huang, C.-K.; Song, M.; Wang, X.-J. Prediction of Stress-Strain Behavior for Polyurethane Elastomers Based on Machine Learning. *Chinese Journal of Polymer Science* **2026**, *44*, 1562–1573. https://doi.org/10.1007/s10118-026-3606-9. Data: https://doi.org/10.57760/sciencedb.j00189.00062.

[17] Huyan, C.; Liu, D.; Han, X.; Liu, D.; Li, H.; Tsui, O. K. C.; Su, L.; Qin, X.; Pan, C.; Chen, F.; Zhang, L. Delayed Crystallization Response-Inspired Waterborne Polyurethane with High Performance. *Nature Communications* **2025**, *16*, 9546. https://doi.org/10.1038/s41467-025-64573-x.

[18] Eom, Y.; Kim, S.-M.; Lee, M.; Jeon, H.; Park, J.; Lee, E. S.; Hwang, S. Y.; Park, J.; Oh, D. X. Mechano-Responsive Hydrogen-Bonding Array of Thermoplastic Polyurethane Elastomer Captures Both Strength and Self-Healing. *Nature Communications* **2021**, *12*, 621. https://doi.org/10.1038/s41467-021-20931-z. Source data: https://doi.org/10.6084/m9.figshare.12936989.v1.

[19] Li, R.; Xie, C.; Liu, L.; You, Y.; Chen, Q.; Xie, H.; Li, Y. Enclose Biobased Content into Polyurethane Elastomers: A Summary of Synthetic Routes and an Inverse Prediction of Their Percentages. *Macromolecular Rapid Communications* **2026**, *47*, e2500054 (published online 2025). https://doi.org/10.1002/marc.202500054.

[20] Yoshida, R.; Hayashi, Y.; Furuya, H.; Hosoya, R.; Kaneko, K.; Sugisawa, H.; Kaneko, Y.; Takahashi, A.; Noguchi, Y.; Nanjo, S.; et al. Omics-Scale Polymer Computational Database Transferable to Real-World Artificial Intelligence Applications. *arXiv* **2025**, arXiv:2511.11626. https://doi.org/10.48550/arXiv.2511.11626. Dataset: https://doi.org/10.57967/hf/7475.

[21] Ritzen, L.; Montano, V.; Garcia, S. J. 3D Printing of a Self-Healing Thermo-Plastic Polyurethane through FDM: From Polymer Slab to Mechanical Assessment. *Polymers* **2021**, *13*, 305. https://doi.org/10.3390/polym13020305. Data: https://doi.org/10.4121/13603775.v1.

[22] Jiang, C.; Zhang, L.; Yang, Q.; et al. Self-Healing Polyurethane-Elastomer with Mechanical Tunability for Multiple Biomedical Applications in Vivo. *Nature Communications* **2021**, *12*, 4395. https://doi.org/10.1038/s41467-021-24680-x.

[23] Li, X.; Xiao, C.; Izutsu, H.; et al. Toughening Elastomer via Sequentially Activated Multi-Pathway Energy Dissipation. *Nature Communications* **2026**, *17*, 5452. https://doi.org/10.1038/s41467-026-74148-z.

[24] Schwarz, D.; Pagáč, M.; Petruš, J.; Polzer, S. Effect of Water-Induced and Physical Aging on Mechanical Properties of 3D Printed Elastomeric Polyurethane. *Polymers* **2022**, *14*, 5496. https://doi.org/10.3390/polym14245496. Data: https://doi.org/10.17632/wcwtjrkfsm.1.

[25] Xu, C. Strain Rate Dependent Mechanical Performance of 3D-Printed Isotropic TPMS-Based Lattices in Thermoplastic Polyurethane; Mendeley Data, version 2, 2026. https://doi.org/10.17632/mc6zh4cwhf.2.

[26] Georgopoulou, A.; Tutu, S.; Clemens, F. Thermoplastic Elastomer Composite Filaments for Strain Sensing Applications Extruded with a Fused Deposition Modelling 3D Printer. *Flexible and Printed Electronics* **2020**. https://doi.org/10.1088/2058-8585/ab9a22. Data record: https://zenodo.org/records/4156000.

[27] Wu, T.; Chen, B. Facile Fabrication of Porous Conductive Thermoplastic Polyurethane Nanocomposite Films via Solution Casting. *Scientific Reports* **2017**. https://doi.org/10.1038/s41598-017-17647-w. Data: https://zenodo.org/records/1098206.

[28] Rahmani, K. Raw Test Data for TPU/BC/CFF Composites; Zenodo, version 1, 2025. https://doi.org/10.5281/zenodo.15490464.

[29] Huang, L.; Xia, J.; Jin, Z.; et al. Entropy-Driven Toughening and Closed-Loop Recycling of Polymers via Divergent Metal-Pyrazole Interactions. *Nature Communications* **2025**, *16*, 10673. https://doi.org/10.1038/s41467-025-65700-4.

[30] Kong, Q.; Tan, Y.; Zhang, K.; et al. Dynamic Decrosslinking Enables Self-Healing, Reprocessability, and Upcycling in Polyurethane Networks. *Nature Communications* **2026**, *17*, 1543. https://doi.org/10.1038/s41467-025-68263-6.

[31] Ding, F.; Liu, T.; Zhang, H.; Liu, L.; Li, Y. Stress-Strain Curves for Polyurethane Elastomers: A Statistical Assessment of Constitutive Models. *Journal of Applied Polymer Science* **2021**, *138*, 51269. https://doi.org/10.1002/app.51269.

[32] Levine, D. S.; Liesen, N.; Chua, L.; Diffenderfer, J.; Ingolfsson, H. I.; Kroonblawd, M. P.; Kumar, N.; Maiti, A.; Mohottalalage, S. S.; Shuaibi, M.; et al. The Open Polymers 2026 (OPoly26) Dataset and Evaluations. *arXiv* **2025**, arXiv:2512.23117. https://doi.org/10.48550/arXiv.2512.23117. Training data: https://huggingface.co/datasets/colabfit/OPoly26-train.

[33] Liang, K.; Qi, X.; Xiao, X.; Wang, L.; Zhang, J. Chemically-Informed Active Learning Enables Data-Efficient Multi-Objective Optimization of Self-Healing Polyurethanes. *Chemical Science* **2026**, *17*, 3627–3638. https://doi.org/10.1039/D5SC07752D.

[34] Viccica, M.; Galati, M.; Giordano, M. Literature Database in Mechanical Characteristic of Thermoplastic Polyurethane; Mendeley Data, version 1, 2023. https://doi.org/10.17632/ftntxg4zdz.1.

[35] Zhou, L. 机器学习预测聚氨酯应力应变曲线的数据 [Data set]; Science Data Bank, version 1, 2026. https://doi.org/10.57760/sciencedb.j00189.00062. All versions withdrawn 2026-07-02; access is currently by author request.

## 10. 论文写作时的引用约定

- 描述数据集规模、字段与用途时，引用数据集原始论文，例如 PUE-643 引用 [1]，不能只引用承载其副本的 DQ/MatImpute GitHub。
- 使用具体文件或代码复现实验时，在正文/补充信息中同时报告 GitHub URL、固定提交哈希、访问日期和本文件中的 SHA-256。
- 使用 Nature 2025 数据进行再分析时引用 [3]，并明确写明是对作者 Source Data/Supplementary Data 的二次分析。
- 使用生成的假想结构时明确写“virtual/hypothetical”，PI1M 和 SMiPoly 候选不得表述为已合成或已验证。
- 最终公开数据包只包含许可证允许再分发的内容；对于不允许再分发的数据，提供下载脚本、来源链接、哈希和处理代码，不附带原文件。
