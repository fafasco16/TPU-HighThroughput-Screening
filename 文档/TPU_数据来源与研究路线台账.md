# TPU 高通量筛选：数据来源、证据等级与研究路线台账

> 项目状态：起步期；本文件为持续更新的来源台账，不是最终论文稿。  
> 首次建立：2026-07-18  
> 研究目标：通过公开实验数据、DFT、MD、多保真机器学习与少量定向合成验证，筛选具有高性能、可合成性、应用潜力和论文新颖性的 TPU/PUE 体系。

## 1. 台账使用规则

1. 将“论文声称的数据规模”“实际获得的文件”“可机器读取的有效样本数”分别记录，不能混用。
2. 每条原始记录必须保留来源 DOI/URL、表格或图号、实验条件、单位、数据版本和提取方式；无法追溯到具体出处的数据不得进入最终高置信度训练集。
3. GitHub 仓库只作为代码或数据载体；论文写作优先引用原始论文和正式数据 DOI，同时记录仓库提交哈希以保证可复现。
4. 许可证未知、仓库无 LICENSE 或 TDM/AI 条款不清的数据默认只登记元数据和参考关系；内部分析、建模、原始再分发与派生发布分别人工复核，不能把“学术用途”自动等同于已获授权。
5. 文献提取、作者原始表、图像数字化、DFT、MD、GC 和 ML 预测不得压入一个混合 `fidelity` 字段；观测分别保存 `origin_kind`、`reduction_level`、`acquisition_method`、`evidence_quality`、`scientific_use_class` 与 `rights_evidence_state`，候选身份另存。具体动作能否执行只由全血缘 `rights_action_decision` 决定。
6. 任何性能值都必须绑定样品配方、合成/加工过程和测试条件。只含单体 SMILES、但不含配比与工艺的数据，不能直接训练可靠的 TPU 终性能模型。

## 2. 当前数据资产总览

2026-07-19 完成扩充前资产盘点，2026-07-20 完成计算资产科学语义复算与多批新增开放数据审计。扩充前基线为 1,790 个文件、722,184,970 字节；其中嵌套 `.git/**` 为 183 个文件、79,921,870 字节，排除后为 1,607 个文件、642,263,100 字节。早期 v0.2 扩充快照为 8,394 个文件、2,636,609,911 字节；其中嵌套 `.git/**` 为 212 个文件、84,032,925 字节，按发现规则排除后为 8,182 个文件、2,552,576,986 字节；当时新增开放数据层为 23 个来源目录、6,604 个文件、1,914,424,941 字节。这些数字现在只作为历史验收口径，不能再标作“当前实时盘点”。第四批落地后，新增开放数据层机械盘点为 **46 个一级目录**；`PCL_GitLFS轨迹补采` 与 `Zenodo_PCL软段构象粗粒化MD` 属同一 DOI/固定仓库树，因此对应 **45 个独立来源身份**。本轮动态审计输出和同源载荷补采结束后，必须重新冻结全量文件数、字节数和逻辑哈希。v0.1 清单仍冻结为 1,606 行、642,262,263 字节：它是历史快照，不能被当前动态盘点覆盖。所有数字必须连同扫描范围和时间报告；文件资产量不等于独立 TPU 配方数，真正可用于化学—性能主模型的高置信样品远少于文件数。扩充前口径见[全量资产预审报告](质量报告/TPU数据库_v0.2_全量资产预审报告.md)，新增层逐来源复算见[新增开放数据准入报告](质量报告/TPU数据库_v0.2_新增开放数据准入报告.md)、[PCL Git LFS 十轨迹补采质量报告](质量报告/TPU数据库_v0.2_PCL_GitLFS十轨迹补采质量报告.md)和[第四批九源质量报告](质量报告/TPU数据库_v0.2_第四批九源质量报告.md)。

### 2.1 工作区原有文件

| 数据资产 | 实际规模 | 当前价值 | 关键限制 | 建议用途 | 主要引用 |
|---|---:|---|---|---|---|
| `01_原始数据/基础数据/openpoly.csv` | 741 行，32 列 | 含 Tg、模量、强度、断裂伸长率等通用聚合物实验属性 | 性能字段高度稀疏；缺少 TPU 配方、合成工艺、测试条件和原始曲线 | 通用聚合物表征预训练、迁移学习和描述符筛选；不能单独作为 TPU 模型 | [5] |
| `01_原始数据/基础数据/PI1M_v2.csv` | 995,799 行，2 列（SMILES、SA Score） | 大规模假想聚合物化学空间 | 无 TPU 标签、无实验性能、含部分不适合 TPU 的结构；不是“可直接合成的 TPU 候选库” | 自监督表征预训练、生成模型化学空间先验 | [6] |
| `01_原始数据/基础数据/smipoly_monomers.csv` | 1,083 个 `comID` 来源记录、1,071 个大小写敏感 exact SMILES；10 个重复 exact-SMILES 组、12 条额外记录 | 有规则驱动的可聚合单体种子 | `comID` 是来源记录身份而非化学体系身份；缺少 TPU 角色、官能度、纯度、供应商、EHS、价格等字段 | 候选单体种子库；需二次分类为二异氰酸酯/多元醇/扩链剂等 | [7] |
| `01_原始数据/基础数据/TPU_开源数据库与建库方案.xlsx` | 6 个工作表 | 已包含数据库比较、任务映射、字段与实施路线雏形 | 当前偏“单张宽表”思路，无法完整表达批次、重复、曲线、多保真计算与来源关系 | 作为需求草案；后续重构为规范化关系数据库 | 本项目内部文件 |

### 2.2 已核验并下载的外部来源

| 来源/本地位置 | 论文或页面声称的规模 | 实际获得并核验的内容 | 许可证/使用边界 | 对本项目的价值 | 主要引用 |
|---|---:|---|---|---|---|
| `01_原始数据/外部数据/PUE643_2023_ESI.pdf` | 原始集 643 个 PUE；其中 386 条完整应力–应变曲线；基准集 326 条 | 官方 ESI PDF；说明 32 种多元醇、117 种硬段组合、20 个输入特征，但 PDF 内没有完整可机器读取的 643 行数据表 | 出版商版权；当前仅作学术核验与字段依据 | TPU/PUE 机械性能字段体系和基准设计的核心依据 | [1] |
| `01_原始数据/代码仓库镜像/DQ/experiment/datasets/PUE.csv` | 对应 PUE 基准数据 | 326 行 × 24 列，无缺失；SSID 326/326 唯一，其余 23 列均为有限数值；输入多为 Z-score/对数变换，输出为 `logEB`、`logYM`、`logTS` | 仓库未发现 LICENSE；当前证据状态 `scope_unresolved`，train/redistribute/publish 均保持人工复核或阻断 | 可复现 326 条变换后基准记录；不能恢复原始配方身份，也不能直接生成新化学体系 | [1], [12] |
| `01_原始数据/代码仓库镜像/MatImpute/experiment/dataset/PUE.csv` | 同一 PUE 基准数据 | 与 DQ 母表逐字节相同；PUE 子族另含 2 个纯投影、207 个缺失变体（23 列 × 9 比率、33,741 个刻意空值）和 6 个聚合/RDF 模型输出；209 个派生容器、0 个新增材料观测 | 仓库未发现 LICENSE；当前证据状态 `scope_unresolved`，不得外推关联论文许可 | 用于缺失值处理/鲁棒性审计；不是新的独立实验数据。MatImpute 全仓 61 个 `model_output` 均为数组、指标表或图件 | [12] |
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
| `01_原始数据/外部数据/PolyOmics_general.csv` | PolyOmics 通用计算性质来源记录 | 95,335 个 UUID、78,379 个大小写敏感 exact `smiles_list`、22 个 QoI；1,932,365 个有限数值和 165,005 个缺失单元格；13,016 个重复 exact-structure 组/16,956 条额外 UUID，其中 348 组具有不同固定上下文 | 仓库 README 声明 CC BY 4.0，但 Hugging Face card 的结构化 license 字段为空；当前不存在动作级 v0.2 `allow` 裁决 | UUID 是来源记录身份而不是独立计算活动；方法、协议、输入输出闭合前只进入计算辅助/审计层 | [20] |
| `01_原始数据/外部数据/PolyOmics_PURT.csv` | 从上述固定快照筛出的 PURT 逻辑子集 | 3,384 个 UUID、3,264 个 exact `smiles_list`；由 `class_PURT=True` 确定，32 行仅有末位数值格式差异 | 只生成带 lineage 的内部版本化视图；公开动作继续复核数据卡许可差异 | 大型 PU 分类计算子集；`class_PURT=True` 不证明线性 TPU、热塑加工性、配方身份或可合成性 | [20] |
| `01_原始数据/外部数据/PolyOmics_README.md` | PolyOmics 数据卡快照 | 对应 Hugging Face revision `43c8c74cac5bef00e7c3a6cca95a9fab9ba1979c` | README 声明 CC BY 4.0 | 固定许可与引用上下文 | [20] |
| `01_原始数据/代码仓库镜像/PolyGraphMT/data/raw/*.csv` | 论文整合的聚合物多任务、多保真性质 | 21 个 CSV、44,083 个来源行；隔离 1 个 `nan` 身份后为 44,082 个有效候选：DFT 16,616、MD 15,333、GC 12,133，12,271 个 exact SMILES；224 个重复组/248 条额外记录，其中 144 个冲突组/158 条冲突额外记录和 90 条冗余额外记录 | 出版物页面报告 CC BY-NC 3.0；仓库/原始 CSV 未发现独立许可证，当前 `scope_unresolved` | 通用聚合物多任务/多保真辅助层；冲突、单位和协议未闭合前不生成聚合标签，更不冒充 TPU 实验数据 | [8] |
| `01_原始数据/代码仓库镜像/ADEPT` | 自动构建聚合物并进行 MD/DFT | `SMILES.csv` 含 13,341 个 PID、13,272 个 exact SMILES、63 个多 PID 结构组和 69 条额外连接；PolyGraphMT 的 12,271 个有效 exact SMILES 全部包含于其中；另有 111 个已识别的模拟输入文件 | 出版物页面报告 CC BY-NC 3.0；代码仓库未发现独立许可证，当前 `scope_unresolved` | 与 PolyGraphMT 登记同论文伴生与 exact-containment；只有明确输出血缘后才声明派生方向，当前输入/流程不是性能观测 | [8] |
| `01_原始数据/外部数据/PU18_Menon2019_figshare.zip` | 论文使用 18 个 PU 样品 | Figshare 压缩包仅 3,985 字节，解压后只有 4 个 Python 脚本；脚本引用的 `PU training dataset.xlsx` 并未包含在压缩包中 | Figshare/论文标为 CC BY 4.0 | 可审查算法结构，但当前不能据此复现 18 样本模型；需联系作者或从补充材料另行追索 | [2] |

PI1M、ADEPT、PolyOmics 和 PolyGraphMT 的六组 exact-string 交集及计算口径集中维护在[全量资产预审报告第 5.5 节](质量报告/TPU数据库_v0.2_全量资产预审报告.md#55-跨库-exact-string-重叠最低限度泄漏保护)；这些计数是未经化学标准化的泄漏下界，不是化学等价结论。

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
| `01_原始数据/仅供参考/受限来源/PUN2026/source_data.xlsx` | 实测 22 个工作表；动态解交联 PUN 的拉伸、循环、DMA、应力松弛及再加工曲线 | CC BY-NC-ND 4.0 | 仅作可修复、再加工和升级回收机制参照 | 热固性动态网络而非 TPU，且 ND 许可；不得并入公开派生数据集 | [30] |

### 2.3.1 v0.2 新增开放数据审计

2026-07-20 完成的新一轮扩充不再用“文件行数”代表样本数，而是分别登记研究、配方、合成批次、物理试样、曲线/采集、曲线点及计算体系—协议—种子。已落地的高价值增量包括：两套 DRUM 可回收 TPUU 数据（合计 30 个规范化材料代码、186 个试样键和 186 条力学/DMTA 曲线）、QUB 生物基自修复 TPU（4 个配方、41 个本体单调拉伸试样）、SLS 商业 TPU（75 个试验序列、350 个试样）、DFT 封端剂（24 个唯一科学体系、158 个正常终止输出）、硬段量化结构、TPU/SWCNT 热电复合、泡沫/纤维/器件及循环力学迁移数据。新增目录中的镜像、压缩包—解包副本、AppleDouble、公共仪器模板、模拟帧和同一试样循环均不增加独立化学样本数。

本轮正式允许 DFT、AIMD、经典 MD、计算描述符和虚拟候选进入多保真数据库，但要求输出、方法、体系、协议、收敛和实验映射闭合。未来权重按证据质量、目标一致性、映射强度和独立性乘法衰减；核心 TPU/TPUU 实验为主标定，计算数据只作机理描述符或校准辅助，泡沫、热固 PU、复合材料、打印件和器件按任务迁移或外部验证。完整逐来源计数、限制、硬零清单和推荐路线见[《TPU 数据库 v0.2 新增开放数据准入报告》](质量报告/TPU数据库_v0.2_新增开放数据准入报告.md)，机器规则见[`v0.2多保真准入与权重策略.yaml`](../配置/v0.2多保真准入与权重策略.yaml)。

### 2.3.2 第四批九源：打印压缩候选监督与 ACS 证据层

第四批固定落地 1 个 Mendeley 实验数据集和 8 个 ACS Figshare 支持信息，共 9 个第三方科学原件、17,664,466 字节；官方元数据、官方文件清单和本地审计输出另行登记，不重复计作观测。详细哈希、计数、异常和人工抽取顺序见[《TPU 数据库 v0.2 第四批九源质量报告》](质量报告/TPU数据库_v0.2_第四批九源质量报告.md)。

| 本地来源 | 固定身份 | 已核验内容 | 当前科学用途 | 准入边界 | 引用 |
|---|---|---|---|---|---|
| `Mendeley_TPU压缩打印DOE` | Mendeley v1，DOI `10.17632/7zcd9bmmg5.1` | 184 个物理试样、46 个相关试样家族；1,372 个完整直接响应值、1,292 个有效内部派生值；4 个直接缺失及其 4 个缓存伪零已隔离 | 打印工艺/几何—压缩响应候选监督 | 商品牌号化学与硬度缺失；必须按 `specimen_family_id` 组级拆分，训练权重尚未物化，MPX不反序列化 | [103] |
| `ACS_Figshare_TPU退火硬段聚集` | Figshare 28906446 v1；SI/论文 DOI `10.1021/acs.macromol.5c00142.s001` / `10.1021/acs.macromol.5c00142` | 3 个退火热学、SAXS、AFM证据组 | 加工后处理—硬段形貌—性能机制 | PDF人工双录前仅证据层 | [104], [105] |
| `ACS_Figshare_双相演化聚氨酯` | Figshare 29074233 v1；`10.1021/acsmaterialslett.5c00732.s001` / `10.1021/acsmaterialslett.5c00732` | 4 个配方、分子量、氢键和机械图证据组 | 双相演化与强韧机制 | 无原始曲线，图线需坐标化和双人复核 | [106], [107] |
| `ACS_Figshare_PLA立构复合TPU` | Figshare 31333274 v1；`10.1021/acs.macromol.5c03502.s001` / `10.1021/acs.macromol.5c03502` | 5 个PCL/三嵌段/分子量/热学/机械证据组 | 高模量—弹性与生物基路线 | L/D身份、跨页表头和机械图未规范化 | [108], [109] |
| `ACS_Figshare_呋喃高强聚氨酯` | Figshare 31429142 v1；`10.1021/acs.macromol.5c03627.s001` / `10.1021/acs.macromol.5c03627` | 5 个配方、耗散、恢复、残余应变和拉伸图证据组 | 后动态交联、强韧与功能升级 | PDF合并单元格会造成文本移位；摘要端点不能替代重复曲线 | [110], [111] |
| `ACS_Figshare_聚酰亚胺回收链扩剂PU` | Figshare 31614502 v1；`10.1021/acsapm.5c04872.s001` / `10.1021/acsapm.5c04872` | 5 个链扩剂工艺、配方、力学和图线证据组 | 回收链扩剂与循环利用候选路线 | Figshare自动年份1753错误；正式引用按Crossref 2026；二手文献表不作样本 | [112], [113] |
| `ACS_Figshare_二氧化碳共聚酯聚氨酯` | Figshare 31989433 v1；`10.1021/acsmacrolett.6c00123.s001` / `10.1021/acsmacrolett.6c00123` | 3 个共聚酯、拉伸图和合成协议证据组 | CO2基聚酯—热塑性PUU—回收路线 | 图线不是原始数据；人工双录前不形成标签 | [114], [115] |
| `ACS_Figshare_聚碳酸酯大分子二醇TPU` | Figshare 32256977 v1；`10.1021/acsapm.6c00646.s001` / `10.1021/acsapm.6c00646` | 4 个进料筛选、重复合成、羟基和TPU性能图证据组 | 聚碳酸酯大分子二醇—TPU组成性能路线 | 多级表头、NMR/GPC脚注和重复编号需人工复核 | [116], [117] |
| `ACS_Figshare_氢键纳米结构TPU` | Figshare 32567339 v1；`10.1021/acs.macromol.6c00352.s001` / `10.1021/acs.macromol.6c00352` | 4 个配方、分子量、力学和氢键证据组 | 氢键纳米结构—强度/韧性机制 | 破折号必须保留为缺失；多级表头未规范化 | [118], [119] |

上述 8 个 ACS 来源合计 33 个证据组，全部为 `evidence_only_not_materialized`；没有任何材料级训练记录、训练拆分或训练权重。本轮另保留但未入选 4 个候选：31333277 仅为已入选 PLA 论文的伴随 MP4；31879167 无表且 TPU 只作吸湿复合体系中的一相；28445288 仅 5 页图像型 PDF、无直接表格且边际价值较低；32061510 为无表格、有效文本很少的 DOCX。它们只是“本轮未入选”，获得机器可读表、原始曲线或新任务需求后可重新评估。

### 2.3.3 PCL Git LFS 十轨迹：同源模拟载荷补采

`PCL_GitLFS轨迹补采` 固定到 GitHub 提交 `446ebadb9ba937d393b6cd7d727256c90e15f24e`、树 `51894a12d912275f37a23853a76dbc2f36e09584`，取得 Zenodo v1.0_2 归档内十个 Git LFS 指针对应的真实载荷。[99], [100] 目录共有 35 个文件、2,313,383,883 字节，其中 10 个 `trr.bz2` 为 2,313,207,356 字节；全部满足 OID、声明字节、本地字节和本地 SHA-256 一致，并通过 BZip2/TRR 全帧检查。解压后共 2,578,712,040 字节、10,569 帧，科学独立单位上限仍是 10 个模拟运行家族，不能把帧数解释为材料或配方数。详细身份、终止状态和 SHA-256 见[《PCL Git LFS 十轨迹补采质量报告》](质量报告/TPU数据库_v0.2_PCL_GitLFS十轨迹补采质量报告.md)。

七条运行正常完成；三条分别在声明步数前终止、续跑超过声明步数后终止和第二次中断信号终止。当前只允许用于来源复现、协议核对和未来运行级聚合描述符设计，不进入训练、不建立拆分、不赋权。Zenodo 包记录 CC BY 4.0，但 GitHub 仓库元数据 `license = null` 且固定树无 `LICENSE/COPYING`；该许可证不得自动传递给从 Git LFS 端点取得的载荷，训练与再分发继续阻断。它与 `Zenodo_PCL软段构象粗粒化MD` 是同一 DOI、同一固定仓库树和同一模型家族的补充层，不增加独立来源身份。

### 2.4 TPU 数据库 v0.1 首次可复现构建

首轮正式构建快照为 `snapshot_3195c290d7dc2d44`，管道标识为 `pipeline_94e0bb066d425128`（`tpu-db/0.1.1`）。它用于验证“候选结构—变换特征—实验曲线—加工曲线”四条数据路径，不代表现有数据已经足以训练可外推的新 TPU 配方终模型。

| 垂直切片 | 暂存结果 | 规范/派生结果 | 公开再分发门控 |
|---|---:|---:|---|
| SMiPoly 候选单体 [7] | 1,083 个 `comID` 来源记录 | 按原始大小写 exact SMILES 得到 1,071 个候选；`comID` 与化学体系身份分列，10 个重复结构组/12 条额外记录保留来源血缘 | 仓库观察到 BSD-3-Clause；v0.2 公开动作仍需完成证据包与动作级裁决，不得表述为 TPU 实验标签 |
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

| 优先级 | 数据来源 | 价值 | 当前障碍 | 下一步 | 主要引用 |
|---|---|---|---|---|---|
| A | 生物基 PUE 数据集：超过 1,500 个样品、26 个特征、6 个输出（YM、TS、EB、Tg、Td5、tanδ） | 最适合构建可持续高性能 TPU 的多目标模型 | Wiley 附件 `marc70252-sup-0002-DataFile.zip` 被站点下载策略阻止，尚未落地 | 需要人工浏览器下载一次，随后做字段、重复、来源和许可证审计 | [9] |
| A | 生物基含量数据：506 条 BPUE 样条 | 含 YM、TS、EB、Tg 与生物基质量分数，可做 BBC% 回归/分层；论文报告 BBC% 预测 R² = 0.89 | 两个官方 XLSX 附件均被 Wiley Cloudflare 阻止自动下载；非 CC，不能公开再分发 | 需要人工下载 Dataset S6/S7；与 >1,500 样本集做来源重叠与泄漏审计 | [19] |
| A | PUE-643 完整原始数据与 386 条应力–应变曲线 | 与本课题目标最直接；可训练曲线级模型和韧性标签 | 官方 ESI 只给出说明，没有可读取的完整表；公开 GitHub 目前只有 326 条变换后子集 | 搜索作者数据仓库、补充附件镜像；必要时给通讯作者发送数据请求 | [1] |
| A | 3D-Weighted-Matrix 多模态 PU 数据与 1.5 亿组合筛选结果 | 结构与合成工艺融合，论文报告 YM/TS/EB 平均 R² > 0.86；与本课题直接竞争 | 官方 Data Availability 明确写“合理请求后由通讯作者提供”；公开补充材料只有约 2.3 MB 的方法 DOCX，没有训练表/筛选候选/代码 | 需要正式向通讯作者申请；即使未获数据，也必须分析其设计空间以避免路线重复 | [14] |
| A | 2026 PUE 应力–应变 Transformer 训练数据 | 最新曲线级建模，论文报告 R² = 0.79、RMSE = 5.82，并做实验确认 | 官方 ESI 已取得，含完整模型代码但没有训练曲线；搜索结果显示的“CSV”没有可核验的实际下载链接 | 联系作者索取训练曲线；现有 ESI 用作模型基准和新颖性对照 | [16] |
| A | ScienceDB 2026 PU 应力–应变机器学习数据 `datasets.csv` | 撤回前文件元数据显示 3,044,630 字节，含化学结构描述符、硬段含量 HSC 与整条应力–应变曲线点；直接对应 [16], [35] | ScienceDB 于 2026-07-02 撤回该 DOI 的全部版本，页面已无下载入口；公开理由为确保数据正确使用及知识产权合规，现仅接受邮件逐案授权 | 向 `zhoul0213@126.com` 提交申请，说明个人/机构信息和预期用途；获批后核对大小及 MD5 `dc28ea5ce05566288cf7c0d97903f30e` 再入库 | [16], [35] |
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

以下撤回与申请边界对应论文 [16] 和数据记录 [35]：

- 数据页：https://www.scidb.cn/detail?dataSetId=3d57444e27944678a99879205a20f595
- 数据 DOI：https://doi.org/10.57760/sciencedb.j00189.00062
- ScienceDB 撤回声明（页面核验日期：2026-07-18）：平台于 2026-07-02 撤回全部版本，以进一步确保数据的正确使用及知识产权法规合规；下载按钮已被撤回声明替代。
- 当前唯一公开获取路径：邮件联系 `zhoul0213@126.com`，申请中提供个人与机构信息、数据集预期用途和目的；作者将按知识产权保护要求逐案评估。
- 撤回前记录的目标文件为 `datasets.csv`（3,044,630 字节；MD5 `dc28ea5ce05566288cf7c0d97903f30e`）。旧直链仅作数据谱系记录，不再视为有效下载入口：  
  https://download.scidb.cn/download?fileId=2d895b01c4545e91048684e6b56d3f1b&path=/V1/datasets.csv&fileName=datasets.csv

### 4.4 v0.2 在线核验、已落地来源与后续队列

下列来源包含已落地深审和仍在排队的候选。2026-07-20 落地的四源均已保存官方 API 元数据、固定文件清单、原始文件哈希和只读复算输出；详细审计见[第二批四源深审报告](质量报告/TPU数据库_v0.2_第二批四源深审报告.md)。其余候选的端点、核验时间、会话响应指纹和动作级保守结论见[新增来源在线核验记录](来源证据/2026-07-19-v0.2新增来源在线核验记录.md)。表中规模必须带 `count_evidence_type`：`publisher_claim`、`repository_metadata`、`temporary_file_audit` 与 `ingested_file_recount` 不得混写；只有最后一种可作为本地冻结事实。

| 优先级 | 来源与已核验规模 | 官方文件 | 权利与证据状态 | 准入定位 | 引用 |
|---|---|---|---|---|---|
| A | PU Tg 扩展集：临时文件审计报告 83 条（73 + 10）；旧 43 条可能是其完整子集，若逐行匹配成立则净新增 40 | 仓库元数据列出 `ap5c04524_si_002.xlsx`，33,447 字节 | 当前为 `temporary_file_audit`；Figshare 数据记录报告 CC BY-NC 4.0，Crossref SI 组件没有 license 字段。权利证据登记 `scope_unresolved`，在文件落地、逐行复算和条款证据固化前关闭公开派生并人工复核内部建模 | 计划以扩展集取代旧 43 条主表；`subset_of/supersedes` 仅在机器匹配证明后生效 | [36], [37] |
| A | CIAL 自愈 PU：SI 表结构预审得到 40 个汇总设计样 + 3 个跟进实验，尚待双流程复算 | RSC Supporting Information PDF，Tables S12–S15 | 当前为 `publisher_claim/temporary_file_audit`；论文元数据报告 CC BY-NC 3.0，SI 文件覆盖范围仍须取证；PDF 表格须双程序/双人核验 | 配方—强度—伸长—韧性—三类修复效率；PG15C 作为待核验的化学计量不合理/预测失败负结果保留 | [33] |
| A | **已落地，`ingested_file_recount`。** EOS TPU 1301：排除 PA12 后 85 个采集/曲线单元；80 个身份闭合直接实验运行、1 个身份冲突运行隔离、4 条物理试样 ID 不明的手工数字化拉伸曲线；实验候选 5,818,564 个有限点。标定为 7 case/20 子运行/7,792 同步点；验证为 16 个目录、15 个唯一模拟运行、92 CSV/162,764 行/112,358,792 个有限单元 | 固定 ZIP 450,879,687 字节，614 条目/549 文件，SHA-256 `988c4d2f...7ee`；pickle 只用 opcode 静态审计，不执行 | Zenodo CC BY 4.0；实验、数字化曲线、标定内拟合和有限元验证分别登记 | 单一商业牌号的曲线、本构和模拟映射；网格、分片、时间步及重复文件不增加独立权重，不能当成多个化学配方 | [38], [39] |
| A | **已落地，`ingested_file_recount`。** 标准化弹性体数据实际出现 11 个材料标签；NinjaFlex 90A 的热流列全空，隔离。目标 Cheetah 与 Filaflex 60A 共 26 条 CSV 曲线；Filaflex 旧版 XLS 经只读 Excel COM 复算出 16 条多变量流变曲线、2,094 个同步点。两牌号合计 42 条曲线、1,341,840 个可用行/同步点 | 7 个 ZIP 合计 88,262,468 字节；139 个文件；旧版 XLS 成员 SHA-256 `3fc855fb...295` | Zenodo CC BY 4.0；曲线编号不等于可跨模态确认的物理试样，`physical_specimen_count=null` | Filaflex 60A/Cheetah 的本构、松弛、热学和流变迁移；商业牌号配方未知，不进入结构—性能主任务 | [40], [41] |
| B | **已落地，`ingested_file_recount`。** 6 个微球体积分数 × 2 个物理试样；12 条 Machine、24 条 DIC，共 23,922 个通道索引行，9 个 YZ 尾端缺失点保留掩码；6 条条件均值/3,000 点为派生视图。`MinMax_Jp.xlsx` 两表重复且含 `98,998,646` 数量级异常，隔离 | `Data_csv.zip` 780,946 字节；最小充分下载排除 12 个合计约 26.9 GB、不会增加试样身份的原始图像包 | Zenodo CC BY 4.0；Machine/DIC 必须按同一试样绑定，Post/MinMax 不新增样本 | 加载—卸载、体积响应和滞回辅助层；是 PU 微球复合体系，配方化学不完整 | [42], [43] |
| B | 热可逆超分子 PU 宽速率数据；一个化学体系，覆盖 40–1220 s⁻¹；`byjbmymyhh.5` 的 34/34 个资产完成校验，固定解析器可靠读取 4 个实验资产、19 个直接数值采集和 35,919 个同步点行 | `tby33jd48k.1` 为 Supplementary Information & Data；`byjbmymyhh.5` 为独立 Raw/Processed deposit；当前集中摘要只闭合后者，GPC 逐表复算为 6,446 对，修正旧摘要的 6,395 对 | 两个 Mendeley 记录均为 CC BY-NC 3.0；建立 `companion_to` 后再按文件哈希去重，不能当成两个独立化学数据集，也不能把后者的文件级闭合外推给前者；24 OPJ、3 OPJU 与 1 MNOVA 在固定解析器缺失时保持未解析和当前权重 0 | NMR/GPC/SAXS/DSC/流变/DMA/循环与高速压缩辅助层 | [44]–[46] |
| B | **已落地，`ingested_file_recount`。** 两种商用反应固化 PU（TASK 3/11），可恢复 38 个材料—工况单元和 108 条机械实验/重复曲线实例，但无 sample ID，物理试样数保持 `null`。51 CSV + 2 XLSX 共 153,375 行、1,459,510 个完整坐标对；91.97% 集中在四个高密度或重复视图。Figure 10 CSV/XLSX 精确重复，Figure 4b 列错位已纠正，Figure 31 协议冲突隔离 | `rspa20220830_si_002.zip` 8,831,991 字节，53 个成员 | Royal Society Figshare CC BY 4.0 | 只作 PU 黏弹/温度—速率辅助；跨 Figure 的归一化、模型、坐标变换和精确重复视图权重为 0，不得标为已知结构 TPU | [47], [48] |
| B | 四种 PTMEG 分子量线性 PU 及复合体系的氢键—强韧—导热数据 | Nature Communications Source Data | CC BY-NC-ND 4.0；原样、非商业、署名分享与规范化/转换后的派生发布必须分别判定；派生长表默认不公开 | 内部机制标定；公开 Source Data 只覆盖指定主/补图，其余实验数据部分需作者申请 | [49] |
| C | 形状记忆 PU + EMIM-TFSI 计算构型，一个体系 | 2 个 PDB + Initial/Final LAMMPS data，共约 17.3 MB | Figshare 记录元数据报告 CC BY 4.0；先登记待审候选关系，核验论文 Data Availability 后才能建立 `supplement_to` | 机制复现/计算输入；无轨迹和性质标签，不计实验样本 | [50], [51] |
| C | 高密度氢键 WPU 的强韧、自修复和 DMA 原始包候选 | ACS SI `s002`–`s010`，9 个包，约 80 MB（尚未解包复核） | 出版商条款；许可、文件关系和实际样本数尚未形成证据 | 下载队列，不在解包审计前声明样本规模或模型就绪 | [52] |

## 5. 数据质量判定

### 5.1 证据质量等级

证据质量不再与实验/DFT/MD 等物理来源或许可混用：

| 等级 | 定义 |
|---|---|
| Q1 | 作者原始机器可读记录，定位、身份、单位、条件和统计语义完整 |
| Q2 | 作者机器可读记录，但部分配方、工艺、条件或统计语义缺失 |
| Q3 | PDF/DOCX 表格经两个独立流程核验并完成差异裁决 |
| Q4 | 图像数字化并保存像素、轴、校准和误差证据 |
| Q5 | 来源、定位、单位、身份或语义存在未解决冲突，仅用于审计 |

每条观测另行保存科学来源、汇总层级、获取方式、科学就绪状态和动作级权利。计算数据可以是 Q1，实验记录也可能是 Q5；证据等级本身不决定训练权重或发布权。

### 5.2 当前资产的结论

- 这些资产在 v0.2 科学准入、权利裁决和快照冻结后，可用于评估“通用聚合物表示 + TPU 小样本校准 + 机理计算”原型；当前阶段不启动训练，更不足以宣称可可靠外推到新 TPU 配方。
- 主要短板不是 SMILES 数量，而是 TPU 样品级的**配比、官能度、分子量分布、NCO/OH、催化剂、含水量、反应温度/时间、退火条件、试样制备和测试速率**。
- 韧性必须优先由完整应力–应变曲线积分得到：

  \[
  U_T=\int_0^{\varepsilon_b}\sigma(\varepsilon)\,\mathrm d\varepsilon
  \]

  当应力以 MPa、应变为无量纲时，积分结果数值单位为 MJ·m\(^{-3}\)。只用拉伸强度或断裂伸长率不能代表韧性。
- `PI1M_v2.csv` 和 SMiPoly 适合扩展候选空间，不能作为“实验性能数据库”；PolyGraphMT 适合通用低保真先验，不能替代 TPU 相分离、氢键与加工历史的专用建模。

## 6. 数据库对象结构

当前冻结候选以 [TPU 数据库 v0.2 多保真设计规范](设计规范/2026-07-19-TPU数据库v0.2多保真设计规范.md)为唯一架构说明，不再在台账中维护一套易漂移的宽表清单。核心对象分为：

1. 来源/source scope、文件、定位、count assertion、citation assignment、rights evidence package/fact/action decision、转换与记录血缘；
2. chemical/material lot/formulation/synthesis batch/synthesis outcome/polymer material/material state/processing event；
3. event/failure、observation subject、measurement run、replicate group、aggregate observation、sequence/channel/point/value、measured curve、property/unit definition；
4. computational system、method model、computational activity、computed observation、computed curve 与 artifact；
5. 等价/泄漏组、质量规则/问题、快照/环境/报告与发布裁决；
6. EHS、价格/供应、加工可行性、失败结果、论文/专利 novelty dossier 和候选决策。

所有对象使用版本化稳定 ID、明确 PK/FK/唯一约束和无环血缘；历史冻结记录只通过修订与 supersession 演进，不静默覆盖。

## 7. 路线比较与当前主路线

### 7.1 先排除已经“撞题”的宽泛创新表述

以下内容可以作为技术组件，但已经不足以单独支撑一区论文的新颖性：

- “DFT 小分子相互作用 + 聚合物力学预测”已经由 [3] 做出完整闭环；
- “全原子 MD 氢键/密度/Rg/扩散 + 可解释 ML”已经由 [15] 系统研究；
- “结构 + 工艺多模态编码 + 超大虚拟空间筛选”已经由 [14] 覆盖；
- “20 个左右实验 + 主动学习 + 多目标自修复 PU 优化”已经由 [33] 发表；
- “生物基 PU + 常规 ML 多性能预测”已有 [9], [19]，仅换算法或扩大候选数量不够。

因此，本文的核心不能写成泛泛的“DFT + MD + ML 高通量筛选”。真正需要建立的是：**分段 TPU 配方图表示 → 动态竞争氢键/相分离与应变诱导有序化 → 加工窗口和循环疲劳约束 → 校准不确定度的多源/多模态/跨尺度决策 → 少量真实合成验证**。只有同一或明确映射的 QoI 具有分层精度、成本和校准证据时才称为“多保真”；DFT 氢键能与宏观韧性的组合属于跨尺度机制特征，不是天然高低保真标签。

### 7.2 路线 A：真正线性分段 TPU 的“时序耗散—延迟有序化”筛选（当前首选）

目标不是复制已发表的高强度交联 PUE，而是在可熔融/可溶液加工、双官能、近线性的 TPU 平台上，筛选具备以下时序机制的扩链剂/硬段组合：低应变时可逆竞争氢键和链段滑移耗散能量，高应变时硬段连通或应变诱导有序化接管承载，卸载后又能部分恢复。

建议工作包：

1. **候选定义**：固定 1–2 种可购软段与 1–2 种二异氰酸酯，先在双官能扩链剂/少量共扩链剂空间中搜索；每个候选明确 NCO/OH、硬段含量、理论 Mn、原料水分、催化剂和合成路线。
2. **DFT 层**：不只算单一最稳二聚体，而是枚举竞争氢键构型、构象简并、结合能分布、偶极/静电势、扭转势垒和温度相关自由能近似；对合成可行且未被充分报道的前列片段进行更高精度复算。
3. **MD 层**：使用多链、多重复单元和多初始构型，提取氢键寿命/交换率、硬段团簇连通度、结构因子/域尺寸、链段取向、自由体积、内聚能密度、Tg、扩散和拉伸下的结构演化；必要时以 OPoly26/PolyOmics 预训练势能或表征，但必须在 TPU 片段上验证误差。
4. **ML 层**：采用“二异氰酸酯—软段—扩链剂—比例—工艺”的分层配方图，而不是把最终重复单元压成一个 SMILES；分别学习终点性能和整条曲线的潜在表示，使用分组/留化学体系外验证与 conformal/ensemble 不确定度。
5. **Pareto 目标**：韧性、强度、断裂伸长、循环残余应变/滞回恢复、DMA 储能/损耗、Tg、熔体/预聚体黏度、热稳定性、EHS、价格和供应可得性；禁止只优化拉伸强度。
6. **实验闭环**：第一阶段不做“大规模实验建库”，而是由预注册的效应量、重复数、功效/精度目标和停止规则决定样品数；8–12 个首批样品与 3–6 个增量样品仅作为当前资源情景，不是固定科学阈值。首批至少包含高分候选、机制消融对照和高不确定样品，并保留失败合成、凝胶、不可加工和低性能结果。

这条路线的潜在一区贡献是：在按操作定义裁决的**线性、可加工 TPU 边界**内，把“动态非共价相互作用的时序切换”量化为可计算、可学习、可用原位/离线表征验证的中间机制，并在严格对照、重复和不确定度下检验它是否同时改善韧性、循环恢复和加工性。高结合能本身不是成功判据；SAXS/WAXS、变温/变形 FTIR、DMA、循环拉伸和流变必须验证中间机制。

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
- [ ] 获取并审计 ACS PU Tg 扩展集；机器复算 83、73 + 10、旧 43 子集关系和净新增量；完成数据记录许可页面固化和动作级裁决前只登记元数据，不生成公开派生表。
- [ ] 从 RSC CIAL Tables S12–S15 双流程抽取并复算暂报的 40 个设计样和 3 个跟进实验，保留 PG15C 负结果与不确定度。
- [x] 获取 Zenodo 15370425、14983287、6390478 和 Royal Society 23635998 的最小必要文件；完成固定哈希、容器安全、材料—试样—通道—曲线—点、跨 Figure 血缘及实验—模拟映射审计。
- [ ] 将 Mendeley 两个独立 deposit 建立 companion 血缘并做哈希去重；不得把宽速率曲线点当独立配方。
- [x] 人工下载并审计 Ding 2021 官方 DOCX；确认其只有方法/统计结果，不含 529 条机器可读原始曲线。
- [ ] 向 Ding 2021/PUE-643 作者追索 529/643 条原始数据和 386 条清洗曲线，并做 529→643→386→326 数据谱系审计。
- [ ] 向 ScienceDB 通讯作者提交 `datasets.csv` 学术用途访问申请，获批后进行哈希、许可证和样品级泄漏审计。
- [ ] 从 Eom 2021、Nature 2025、DCR 2025、4TU SH-TPU、Jiang 2021 和 Li 2026 生成规范化 `formulation/synthesis_batch/material_state/measurement_run/observation/sequence/measured_curve`，派生指标作为有父序列和算法证据的 observation，不再使用旧宽表简称。
- [ ] 将预聚体黏度和 4TU 熔体流变转成长表，拟合 Andrade/WLF/Carreau–Yasuda 等候选模型并明确外推边界。
- [ ] 为 1,083 个 `comID` 来源记录/1,071 个 exact SMILES 补齐 TPU 角色、官能度、EHS、供应可得性和价格等级。
- [x] 完成 PI1M、ADEPT、PolyOmics、PolyGraphMT 的 exact-string 重叠下界审计，并冻结六组交集计数。
- [ ] 完成 OpenPoly、PI1M、ADEPT、PolyGraphMT、PolyOmics PURT 和 OPoly26 的化学标准化、图同构/近等价、最终防泄漏及 TPU 化学域覆盖审计；先做小规模效益试验，再决定是否下载 5.08 GB/大分片。
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

[36] Qin, Y.; Ma, Z.; Li, X.; Shen, J.; Liu, J. Machine Learning-Driven Prediction and Interpretation of Glass Transition Temperature in Polyurethanes. *ACS Applied Polymer Materials* **2026**, *8* (8), 5471–5484. https://doi.org/10.1021/acsapm.5c04524.

[37] Qin, Y.; Ma, Z.; Li, X.; Shen, J.; Liu, J. Complete Data Set Utilized for Analysis [XLSX], Supporting Information to *Machine Learning-Driven Prediction and Interpretation of Glass Transition Temperature in Polyurethanes*; American Chemical Society, 2026. https://doi.org/10.1021/acsapm.5c04524.s002.

[38] Jinaga, U. K.; Zulueta, K.; Burgoa, A.; Cobian, L.; Freitas, U.; Lackner, M.; Major, Z.; Noels, L. A Consistent Finite-Strain Thermomechanical Quasi-Nonlinear-Viscoelastic Viscoplastic Constitutive Model for Thermoplastic Polymers. *International Journal of Solids and Structures* **2025**, *321*, 113517. https://doi.org/10.1016/j.ijsolstr.2025.113517.

[39] Noels, L.; Jinaga, U. K. Data of “A Consistent Finite-Strain Thermomechanical Quasi-Nonlinear-Viscoelastic Viscoplastic Constitutive Model for Thermoplastic Polymers” [Data set], version 1; Zenodo, 2025. https://doi.org/10.5281/zenodo.15370425.

[40] Roels, E.; Costa Cornellà, A.; Brancart, J. A Standardized Framework for Elastomer Characterization in Soft Robotics. *Advanced Intelligent Systems* **2026**, *8* (3), e202500699. https://doi.org/10.1002/aisy.202500699.

[41] Roels, E.; Costa Cornellà, A.; Brancart, J. A Standardized Elastomer Characterization Framework for Soft Robotics—Accompanying Dataset [Data set], version 1; Vrije Universiteit Brussel/Zenodo, 2025. https://doi.org/10.5281/zenodo.14983287.

[42] Coret, M.; Verron, E.; Rublon, P.; Leblé, B. Remarkable Response of Hollow Thermoplastic Microspheres–Elastomer Matrix Composites in Uniaxial Tension. *Mechanics of Soft Materials* **2022**, *4*, 8. https://doi.org/10.1007/s42558-022-00046-1.

[43] Coret, M.; Verron, E.; Rublon, P. Images and Data Accompanying Article: Remarkable Response of Hollow Thermoplastic Microspheres–Elastomer Matrix Composites in Uniaxial Tension [Data set], version 1; Zenodo, 2022. https://doi.org/10.5281/zenodo.6390478.

[44] Chen, H.; Hart, L. R.; Hayes, W.; Siviour, C. R. Mechanical Characterisation and Modelling of a Thermoreversible Superamolecular Polyurethane over a Wide Range of Rates. *Polymer* **2021**, *221*, 123607. https://doi.org/10.1016/j.polymer.2021.123607.

[45] Chen, H.; Hart, L. R.; Hayes, W.; Siviour, C. R. Supplementary Information & Data for Mechanical Characterisation and Modelling of a Thermoreversible Superamolecular Polyurethane over a Wide Range of Rates [Data set], version 1; Mendeley Data, 2021. https://doi.org/10.17632/tby33jd48k.1.

[46] Chen, H.; Hart, L. R.; Hayes, W.; Siviour, C. R. Mechanical Characterisation and Modelling of a Thermoreversible Superamolecular Polyurethane over a Wide Range of Rates [Data set], version 5; Mendeley Data, 2021. https://doi.org/10.17632/byjbmymyhh.5.

[47] Commins, T.; Siviour, C. R. Stress Relaxation after Low- and High-Rate Deformation of Polyurethanes. *Proceedings of the Royal Society A* **2023**, *479* (2275), 20220830. https://doi.org/10.1098/rspa.2022.0830.

[48] Commins, T.; Siviour, C. R. Data from Stress Relaxation after Low- and High-Rate Deformation of Polyurethanes [Data set], version 1; The Royal Society/Figshare, 2023. https://doi.org/10.6084/m9.figshare.23635998.v1.

[49] Liu, X.; Wen, J.; Xu, R.; et al. Flexible Rubber with Metal-Like Thermal Conductivity Achieved via Hydrogen Bonding Engineering. *Nature Communications* **2026**, *17*, 4480. https://doi.org/10.1038/s41467-026-71056-0.

[50] Chen, T.; Xu, J.; Wang, C.; Zhang, X.; Pei, X.; Wang, T.; Wang, Q. Shape-Memory Polyurethanes for Polar Wearables with Ultrasensitive Multi-Monitoring. *Nature Communications* **2025**, *16*, 11329. https://doi.org/10.1038/s41467-025-66422-3.

[51] Chen, T.; Xu, J.; Wang, C.; Zhang, X.; Pei, X.; Wang, T.; Wang, Q. Shape-Memory Polyurethanes for Polar Wearables with Ultrasensitive Multi-Monitoring [Data set], version 2; Figshare, 2025. https://doi.org/10.6084/m9.figshare.30484481.v2.

[52] Wu, C.-Q.; Chen, J.; Long, Q.-Y.; Sun, D.-X.; Qi, X.-D.; Yang, J.-H.; Wang, Y. Healable, Recyclable, and Ultra-Tough Waterborne Polyurethane Elastomer Achieved through High-Density Hydrogen Bonding Cross-Linking Strategy. *ACS Applied Materials & Interfaces* **2024**, *16* (46), 64333–64344. https://doi.org/10.1021/acsami.4c15188.

[53] Pfau-Cloud, M. R.; Batiste, D. C.; Kim, H. J.; Ellison, C. J.; Hillmyer, M. A. Data for Alkyl Substituted Polycaprolactone Poly(Urethane-Urea)s as Mechanically-Competitive and Chemically-Recyclable Materials [Data set]; Data Repository for the University of Minnesota, 2024. https://doi.org/10.13020/05ek-6k60.

[54] Batiste, D. C.; Pfau-Cloud, M. R.; Kim, H. J.; Ellison, C. J.; Hillmyer, M. A. Alkyl-Substituted Polycaprolactone Poly(urethane-urea)s as Mechanically Competitive and Chemically Recyclable Materials. *ACS Macro Letters* **2024**, *13* (11), 1449–1455. https://doi.org/10.1021/acsmacrolett.4c00474.

[55] Meyersohn, M. S.; Block, A.; Bates, F. S.; Hillmyer, M. A. Supporting Information for Tackling the Thermodynamic Stability of Low-Ceiling Temperature Polymers in the Preparation of Tough and Chemically Recyclable Thermoplastic Polyurethane-Urea Elastomers [Data set]; Data Repository for the University of Minnesota, 2024. https://doi.org/10.13020/zf53-w893.

[56] Meyersohn, M. S.; Block, A.; Bates, F. S.; Hillmyer, M. A. Tackling the Thermodynamic Stability of Low-Ceiling Temperature Polymers in the Preparation of Tough and Chemically Recyclable Thermoplastic Polyurethane-Urea Elastomers. *Macromolecules* **2024**, *57* (19), 9230–9240. https://doi.org/10.1021/acs.macromol.4c01431.

[57] Jiang, H. Research on the Dynamic Compressibility of Polyurethane Microcellular Elastomer and its Application for Impact Resistance [Data set], version 1; Science Data Bank, 2024. https://doi.org/10.57760/sciencedb.j00189.00045.

[58] Zhao, Z.-Y.; Jiang, H.; Li, X.-D.; Zhang, X.-D.; Su, X.; Zou, M.-S. Dynamic Compressibility of Polyurethane Microcellular Elastomer and Its Application for Impact Resistance. *Chinese Journal of Polymer Science* **2024**, *42* (8), 1185–1197. https://doi.org/10.1007/s10118-024-3134-4.

[59] Ciobotaru, V. Modelling Mechanical Properties of Thermoplastic Polyurethanes through Laser Sintering Exposure for Replicating Micrometric Aortic Valve Membranes [Data set], version 1; Mendeley Data, 2023. https://doi.org/10.17632/wfsm6f9rbn.1.

[60] Ciobotaru, V.; Batistella, M.; de Oliveira Emmer, E.; Clari, L.; Masson, A.; Decante, B.; Le Bret, E.; Lopez-Cuesta, J.-M.; Hascoët, S. Modelling Mechanical Properties of Thermoplastic Polyurethanes through Laser Sintering Exposure for Replicating Micrometric Aortic Valve Membranes. *Polymers* **2024**, *16* (7), 900. https://doi.org/10.3390/polym16070900.

[61] Didovets, Y. Structure–Property Relationship between Hard Segments of Shape Memory Polyurethane Copolymers and Interchain Hydrogen Bonds: A Comprehensive Theoretical Study - Raw Data [Data set]; Jagiellonian University Repository, 2026. https://doi.org/10.57903/UJ/TYAPFM.

[62] Didovets, Y.; Brela, M. Z. Structure–Property Relationship between Hard Segments of Shape Memory Polyurethane Copolymers and Interchain Hydrogen Bonds: A Comprehensive Theoretical Study. *The Journal of Physical Chemistry B* **2025**, *129* (40), 10504–10520. https://doi.org/10.1021/acs.jpcb.5c03305.

[63] Krause, B.; Zimmerer, C. Raw Data for the Paper Nitrogen Content Governs Thermoelectric Performance in TPU/SWCNT Composites [Data set]; Zenodo, 2026. https://doi.org/10.5281/zenodo.20932248.

[64] Zimmerer, C.; Krause, B. Nitrogen Content Governs Thermoelectric Performance in TPU/SWCNT Composites. *Preprints* **2026**, version 1. https://doi.org/10.20944/preprints202606.1342.v1.

[65] Morrison, D. Temperature Dependent Dynamic Response of High-Density Polyurethane Foams [Data set], version 1; Mendeley Data, 2023. https://doi.org/10.17632/x6b72k59xn.1.

[66] Ahmad, J. An Analysis of Screen-Printed Stretchable Conductive Tracks on Thermoplastic Polyurethane [Data set], version 1; Mid Sweden University/SND, 2019. https://doi.org/10.5878/tc7g-1056.

[67] Zhang, S. 3D-Printed Multiscale Hierarchical Thermoplastic Polyurethane / Aramid Nanofiber Structures with Enhanced Energy Absorption via In-Situ Foaming Technology [Data set], version 1; Science Data Bank, 2025. https://doi.org/10.57760/sciencedb.26393.

[68] Zakrzewska, P. Rigid Polyurethane Foams with Reduced Petrochemical Polyol Content [Data set], version 1; AGH University Dataverse, 2026. https://doi.org/10.58032/AGH/LKHZ6Q.

[69] Zhu, Y.; Huang, Y.; Ye, S.; Deng, Y.; Chen, J.; Liu, Z.; Guo, X.; Zhu, Y. Atom-Economy Upcycling of Commodity Thermoset Polyurethane into Photocuring 3D Printing Resins Based on Selective Cleavage—Crosslink Strategy [Data set], version 1; Figshare, 2026. https://doi.org/10.6084/m9.figshare.31552786.v1.

[70] Huang, Y.; Guo, X.; Deng, Y.; Ye, S.; Zhu, Y.; Liu, Z.; Chen, J.; Zhu, Y. Atom-Economy Upcycling of Commodity Thermoset Polyurethane into Photocuring 3D Printing Resins Based on Selective Cleavage–Crosslink Strategy. *Nature Communications* **2026**, *17*, 4151. https://doi.org/10.1038/s41467-026-70951-w.

[71] Gao, D.; Thangavel, G.; Lee, J.; Lv, J.; Li, Y.; Ciou, J.-H.; Xiong, J.; Park, T.; Lee, P. S. Source Data.xlsx [Data set], version 1; Figshare, 2022. https://doi.org/10.6084/m9.figshare.21716516.v1.

[72] Gao, D.; Thangavel, G.; Lee, J.; Lv, J.; Li, Y.; Ciou, J.-H.; Xiong, J.; Park, T.; Lee, P. S. A Supramolecular Gel-Elastomer System for Soft Iontronic Adhesives. *Nature Communications* **2023**, *14*, 1990. https://doi.org/10.1038/s41467-023-37535-4.

[73] Uscategui, Y. L.; Díaz, L. E.; Valero, M. F. Effect of the Addition of Short Chain Polymers on the Chemical Structure, Mechanical, Thermal and Biological Properties of Polyurethanes Synthesized with Aliphatic Diisocyanates and Castor Oil [Data set], version 1; Figshare, 2021. https://doi.org/10.6084/m9.figshare.14279117.v1.

[74] Uscátegui, Y. L.; Díaz, L. E.; Valero, M. F. Efecto de la Adición de Polímeros de Cadena Corta sobre la Estructura Química, Propiedades Mecánicas, Térmicas y Biológicas de Poliuretanos Sintetizados con Diisocianatos Alifáticos y Aceite de Higuerilla. *Química Nova* **2021**, *44* (1), 48–57. https://doi.org/10.21577/0100-4042.20170643.

[75] Griggs, T. Dataset for A Bio-Based Thermoplastic Polyurethane with Triple Self-Healing Action for Wearable Technology and Smart Textiles [Data set]; Queen’s University Belfast, 2024. https://doi.org/10.17034/83fdb865-0ead-4c8b-81d2-59265a8810f3.

[76] Griggs, T.; Ahmed, J.; Majd, H.; Edirisinghe, M.; Chen, B. A Bio-Based Thermoplastic Polyurethane with Triple Self-Healing Action for Wearable Technology and Smart Textiles. *Materials Advances* **2024**, *5* (15), 6210–6221. https://doi.org/10.1039/D4MA00289J.

[77] Rafiq, R.; Zulueta, B.; Zucco, H.; Suresh, R.; Shoemaker, J. E.; Call, M.; Sheppard, D.; Cormack, G.; Keith, J. A.; Veser, G. Supporting Data: Bond Energy Descriptors Enable Machine Learning with Limited Data: Design of Capping Agents for Thermoplastic Polyurethane Recycling [Data set]; Zenodo, 2026. https://doi.org/10.5281/zenodo.17883052.

[78] Cicoira, F.; Kim, J. Printable, Self-Healing and Recyclable PEDOT:PSS/Polyurethane Composites for Durable Bioelectronics [Data set]; Zenodo, 2026. https://doi.org/10.5281/zenodo.19609901.

[79] Kim, J.; Cicoira, F. Printable, Self-Healing and Recyclable PEDOT:PSS/Polyurethane Composites for Durable Bioelectronics. *Materials Horizons* **2026**, *13* (13), 6517–6531. https://doi.org/10.1039/D6MH00177G.

[80] Xu, C.; Daynes, S.; Das, R.; Kabaliuk, N. Strain Rate Dependent Mechanical Performance of 3D-Printed Elastically Isotropic TPMS-Based Lattices in Thermoplastic Polyurethane. *Virtual and Physical Prototyping* **2026**, *21* (1), e2662048. https://doi.org/10.1080/17452759.2026.2662048.

[81] Rezaei, S.; Machado Junior, J. L.; Bilasse, M.; Othmani, Y.; Berthe, S.; Ehlinger, M. Dataset for Characterization of Fracture and Elastic Properties of Commercially Available Polyurethane Foam and Short Fiber Filled Epoxy for Bone Models [Data set]; Materials Cloud, 2026. https://doi.org/10.24435/materialscloud:VF-RY.

[82] Rezaei, S.; Machado Junior, J. L.; Bilasse, M.; Othmani, Y.; Berthe, S.; Ehlinger, M. Characterization of Fracture and Elastic Properties of Commercially Available Polyurethane Foam and Short Fiber Filled Epoxy for Bone Substitutes. *SSRN* **2026**. https://doi.org/10.2139/ssrn.6755055.

[83] Madariaga, A. Replication Data for: The Nonlinear Mechanics of Single Electrospun Polyurethane Fibers Under Wet and Dry Conditions [Data set]; Texas Data Repository, 2026. https://doi.org/10.18738/T8/ZYQ5Z1.

[84] Pires da Silva, E. H. Aged PUF Compression Tests [Data set], version 3; Mendeley Data, 2023. https://doi.org/10.17632/2sp8fyvhfm.3.

[85] Dams, B. Reprocell 500, Reprocell 300 and LD40 Polyurethane Foam Mechanical and Characterisation Tests October 2016–April 2017 [Data set]; University of Bath Research Data Archive, 2017. https://doi.org/10.15125/BATH-00385.

[86] Beneš, H.; Sedlacek, O.; Kopilec, O.; Hodan, J. Dataset for Rigid Biobased Vinylogous Urethane Vitrimers from d-Isosorbide/Furfural-Derived Monomers [Data set], version 1.0; Zenodo, 2026. https://doi.org/10.5281/zenodo.21096098.

[87] Kopilec, O.; Hodan, J.; Sedlacek, O.; Beneš, H. Rigid Biobased Vinylogous Urethane Vitrimers from d-Isosorbide/Furfural-Derived Monomers. *ACS Polymers Au* **2026**, Article ASAP. https://doi.org/10.1021/acspolymersau.6c00063.

[88] Georgopoulou, A.; Vanderborght, B.; Clemens, F. Fabrication of a Soft Robotic Gripper With Integrated Strain Sensing Elements Using Multi-Material Additive Manufacturing [Data set]; Zenodo, 2021. https://doi.org/10.5281/zenodo.5841610.

[89] Georgopoulou, A.; Vanderborght, B.; Clemens, F. Fabrication of a Soft Robotic Gripper With Integrated Strain Sensing Elements Using Multi-Material Additive Manufacturing. *Frontiers in Robotics and AI* **2021**, *8*, 615991. https://doi.org/10.3389/frobt.2021.615991.

[90] Vazquez-Rodriguez, J. A.; Shaqour, B.; Guarch-Pérez, C.; Choińska, E.; Riool, M.; Verleije, B.; Beyers, K.; Costantini, V. J. A.; Święszkowski, W.; Zaat, S. A. J.; Cos, P.; Felici, A.; Ferrari, L. A Niclosamide-Releasing Hot-Melt Extruded Catheter Prevents Staphylococcus aureus Experimental Biomaterial-Associated Infection [Data set]; Zenodo, 2022. https://doi.org/10.5281/zenodo.6128356.

[91] Vazquez-Rodriguez, J. A.; Shaqour, B.; Guarch-Pérez, C.; Choińska, E.; Riool, M.; Verleije, B.; Beyers, K.; Costantini, V. J. A.; Święszkowski, W.; Zaat, S. A. J.; Cos, P.; Felici, A.; Ferrari, L. A Niclosamide-Releasing Hot-Melt Extruded Catheter Prevents Staphylococcus aureus Experimental Biomaterial-Associated Infection. *Scientific Reports* **2022**, *12*, 12329. https://doi.org/10.1038/s41598-022-16107-4.

[92] Habets, S. Internship_CSM_XSTRMLAB_Sandor_Habets_2024 [Data set], version 1; Mendeley Data, 2024. https://doi.org/10.17632/hc6npzvw3m.1.

[93] Tapia, M. Experimental and Numerical Data for FDM-Printed PLA and TPU Cellular Structures under Compression and Bending [Data set], version 1; Mendeley Data, 2026. https://doi.org/10.17632/dbzdkz95f8.1.

[94] Mohd Azli, D. A. S-S Curve for TPU Experiment [Data set], version 1; Mendeley Data, 2023. https://doi.org/10.17632/kysnxmy7xw.1.

[95] Liu, M. liuminghao0830/cg-polyurea-curing: Published Version of the CG Model [Software], version 1.0; Zenodo, 2023. https://doi.org/10.5281/zenodo.7811383.

[96] Liu, M.; Ye, J.; Oswald, J. Coarse-Grained Molecular Simulation of the Role of Curing Rates on the Structure and Strength of Polyurea. *Computational Materials Science* **2023**, *230*, 112428. https://doi.org/10.1016/j.commatsci.2023.112428.

[97] Červinka, C.; Paušová, Š.; Bouzek, K. Dataset of “Fast Carbon Dioxide–Epoxide Cycloaddition Catalyzed by Metal and Metal-Free Ionic Liquids for Designing Non-Isocyanate Polyurethanes” [Data set], version 1; Zenodo, 2024. https://doi.org/10.5281/zenodo.10817092.

[98] Rebei, M.; Červinka, C.; Mahun, A.; et al. Fast Carbon Dioxide–Epoxide Cycloaddition Catalyzed by Metal and Metal-Free Ionic Liquids for Designing Non-Isocyanate Polyurethanes. *Materials Advances* **2024**, *5*, 4311–4323. https://doi.org/10.1039/D3MA00852E.

[99] Bačová, P. PCL in Vacuum and in Water [Software/Data], version 1.0_2; Zenodo, 2025. https://doi.org/10.5281/zenodo.17790918.

[100] Bačová, P.; González Huarte, G.; Harmandaris, V.; Molina, S. I. Development of a Systematic Coarse-Grained Model for Poly(ε-Caprolactone) in Melt. *Open Research Europe* **2025**, *5*, 296. https://doi.org/10.12688/openreseurope.21354.2.

[101] Dewapriya, N.; Miller, R. LAMMPS Model to Simulate Spallation in Polyurethane [Data set]; Zenodo, 2021. https://doi.org/10.5281/zenodo.5099589.

[102] Dewapriya, M. A. N.; Miller, R. E. Molecular Dynamics Simulations of Shock Propagation and Spallation in Amorphous Polymers. *Journal of Applied Mechanics* **2021**, *88* (10), 101005. https://doi.org/10.1115/1.4051238.

[103] Wentz, J. Impact of Infill and Shell Design Features on Compression Stiffness in Material Extrusion of Thermoplastic Urethane [Data set], version 1; Mendeley Data, 2022. https://doi.org/10.17632/7zcd9bmmg5.1.

[104] Wang, Z.; Wang, C.; Zhao, X.; Yang, X. Manipulating the Mechanical Properties of Thermoplastic Polyurethane via Regulating Hard Segment Aggregation [Supporting information], version 1; ACS Publications/Figshare, 2025. https://doi.org/10.1021/acs.macromol.5c00142.s001.

[105] Wang, Z.; Wang, C.; Zhao, X.; Yang, X. Manipulating the Mechanical Properties of Thermoplastic Polyurethane via Regulating Hard Segment Aggregation. *Macromolecules* **2025**, *58* (9), 4394–4406. https://doi.org/10.1021/acs.macromol.5c00142.

[106] Lu, K.; Chen, H.; Huang, C.; Wang, Z.; Yan, J. Capturing Robust and Tough Thermoplastic Polyurethane Elastomers via Engineering Dual-Phase Evolution Rather than Chain Extenders [Supporting information], version 1; ACS Publications/Figshare, 2025. https://doi.org/10.1021/acsmaterialslett.5c00732.s001.

[107] Lu, K.; Chen, H.; Huang, C.; Wang, Z.; Yan, J. Capturing Robust and Tough Thermoplastic Polyurethane Elastomers via Engineering Dual-Phase Evolution Rather than Chain Extenders. *ACS Materials Letters* **2025**, *7* (6), 2238–2245. https://doi.org/10.1021/acsmaterialslett.5c00732.

[108] Xu, R.; Miao, X.; Yang, S.; et al. Stiff Yet Elastic Thermoplastic Polyurethanes Based on Nanoconfined Stereocomplexation in PLA Interphases [Supporting information], version 1; ACS Publications/Figshare, 2026. https://doi.org/10.1021/acs.macromol.5c03502.s001.

[109] Xu, R.; Miao, X.; Yang, S.; et al. Stiff Yet Elastic Thermoplastic Polyurethanes Based on Nanoconfined Stereocomplexation in PLA Interphases. *Macromolecules* **2026**, *59* (4), 2613–2622. https://doi.org/10.1021/acs.macromol.5c03502.

[110] Yang, T.; Chen, X.; Wei, Z.; et al. High-Strength, Tough, Furan-Based Polyurethane Elastomers Achieving Performance and Functionality Upgrades through Postdynamic Cross-Linking [Supporting information], version 1; ACS Publications/Figshare, 2026. https://doi.org/10.1021/acs.macromol.5c03627.s001.

[111] Yang, T.; Chen, X.; Wei, Z.; et al. High-Strength, Tough, Furan-Based Polyurethane Elastomers Achieving Performance and Functionality Upgrades through Postdynamic Cross-Linking. *Macromolecules* **2026**, *59* (5), 3171–3187. https://doi.org/10.1021/acs.macromol.5c03627.

[112] Guo, H.; Zhang, R.; Li, H.; et al. Upcycling of Polyimide for the Preparation of a High-Performance Polyurethane Chain Extender [Supporting information], version 1; ACS Publications/Figshare, 2026. https://doi.org/10.1021/acsapm.5c04872.s001.

[113] Guo, H.; Zhang, R.; Li, H.; et al. Upcycling of Polyimide for the Preparation of a High-Performance Polyurethane Chain Extender. *ACS Applied Polymer Materials* **2026**, *8* (6), 4305–4314. https://doi.org/10.1021/acsapm.5c04872.

[114] Kong, W.; Dar, U. A.; Ma, Y.; et al. Recyclable Thermoplastic Poly(urethane-urea)s with Enhanced Mechanical and Adhesive Properties Derived from CO2-Based Copolyesters [Supporting information], version 1; ACS Publications/Figshare, 2026. https://doi.org/10.1021/acsmacrolett.6c00123.s001.

[115] Kong, W.; Dar, U. A.; Ma, Y.; et al. Recyclable Thermoplastic Poly(urethane-urea)s with Enhanced Mechanical and Adhesive Properties Derived from CO2-Based Copolyesters. *ACS Macro Letters* **2026**, *15* (4), 647–654. https://doi.org/10.1021/acsmacrolett.6c00123.

[116] Zhong, W.; Zhang, T.; Huang, S.; et al. One-Pot Synthesis of CO2-Based Polycarbonate Macrodiols: (Propylene Carbonate)/(Ethylene Carbonate) Composition Evolution Versus Physicochemical Performance [Supporting information], version 1; ACS Publications/Figshare, 2026. https://doi.org/10.1021/acsapm.6c00646.s001.

[117] Zhong, W.; Zhang, T.; Huang, S.; et al. One-Pot Synthesis of CO2-Based Polycarbonate Macrodiols: (Propylene Carbonate)/(Ethylene Carbonate) Composition Evolution Versus Physicochemical Performance. *ACS Applied Polymer Materials* **2026**, *8* (10), 7438–7450. https://doi.org/10.1021/acsapm.6c00646.

[118] Wei, Z.; Zhang, Y.; Lei, Y.; et al. Design of Sustainable and High Strength-Toughness Thermoplastic Elastomer via a Strong Hydrogen Bond-Reinforced Nanostructure [Supporting information], version 1; ACS Publications/Figshare, 2026. https://doi.org/10.1021/acs.macromol.6c00352.s001.

[119] Wei, Z.; Zhang, Y.; Lei, Y.; et al. Design of Sustainable and High Strength-Toughness Thermoplastic Elastomer via a Strong Hydrogen Bond-Reinforced Nanostructure. *Macromolecules* **2026**, *59* (12), 7171–7182. https://doi.org/10.1021/acs.macromol.6c00352.

## 10. 论文写作时的引用约定

- 描述数据集规模、字段与用途时，引用数据集原始论文，例如 PUE-643 引用 [1]，不能只引用承载其副本的 DQ/MatImpute GitHub。
- 使用具体文件或代码复现实验时，在正文/补充信息中同时报告 GitHub URL、固定提交哈希、访问日期和本文件中的 SHA-256。
- 使用 Nature 2025 数据进行再分析时引用 [3]，并明确写明是对作者 Source Data/Supplementary Data 的二次分析。
- 使用生成的假想结构时明确写“virtual/hypothetical”，PI1M 和 SMiPoly 候选不得表述为已合成或已验证。
- 最终公开数据包只包含许可证允许再分发的内容；对于不允许再分发的数据，提供下载脚本、来源链接、哈希和处理代码，不附带原文件。
- 论文 DOI 与数据 DOI 分开引用；数据记录的作者、版本和许可证不得从关联论文推断。
- 每条引用应维护稳定 `citation_key`，并在实现阶段同步生成 BibTeX/CSL-JSON；Markdown 编号只用于阅读展示。
- 许可证结论绑定官方证据 URL、核验日期和条款哈希；元数据冲突时标记 `license_conflict` 并 fail closed，不能选择最宽松的解释。
- 新增来源的本次在线核验端点、响应指纹、规模证据类型和初始动作裁决见[2026-07-19 v0.2 新增来源在线核验记录](来源证据/2026-07-19-v0.2新增来源在线核验记录.md)；其中会话响应指纹不替代后续归档的原始响应或数据文件 SHA-256。
