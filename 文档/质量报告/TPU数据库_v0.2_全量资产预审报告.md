# TPU 数据库 v0.2 全量资产预审报告

> 初次资产审计：2026-07-19；科学语义复算：2026-07-20
> 状态：`provisional_governance_build`；两次真实隔离构建全产物字节一致，但不是冻结科学数据库或训练集
> 扫描范围：01_原始数据全部物理文件；科学发现集合按明确规则排除嵌套 .git/**
> 引用规则：编号与[数据来源与研究路线主台账](../TPU_数据来源与研究路线台账.md)一致

## 1. 结论

当前“数据量少”的根因不是磁盘上缺少文件，而是来源记录、独立体系、观测和可训练标签不能用文件行数互相替代。全盘共有 1,790 个文件；其中 183 个是五个仓库的嵌套 Git 元数据，排除后科学发现集合为 1,607 个文件。v0.1 清单中仅 4 个文件具有正式 source_id，另 1,602 行是历史 `raw_vault_unregistered`；v0.2 当前 1,607 个发现域资产已进入角色登记/证据排除审计，但角色登记不等于科学准入、可训练或可发布。

可纳入的计算数据显著多于 v0.1：PolyOmics 来源记录、PolyGraphMT 的 DFT/MD/GC 性质候选、ADEPT 候选与模拟工作流、PI1M 和 SMiPoly 均有价值；但它们分别属于待适配的计算性质、模拟输入、虚拟候选或反应规则，不能通过调低权重就冒充实验标签。合理路线是先把这些数据作为独立保真度和独立实体登记，再在同一 QoI、体系映射、协议、校准和不确定度成立时建立多保真关系。[6]–[8], [20]

## 2. 扫描口径与可复算边界

| 口径 | 文件数 | 字节数 | 解释 |
|---|---:|---:|---|
| 01_原始数据全部物理文件 | 1,790 | 722,184,970 | 包含隐藏的嵌套 .git/** |
| 嵌套 .git/** | 183 | 79,921,870 | 仅保留仓库 origin/commit 证据，不逐文件登记为数据资产 |
| 排除 .git/** 后的发现集合 | 1,607 | 642,263,100 | v0.2 资产登记的当前动态输入 |
| v0.1 清单/来源清单.csv | 1,606 | 642,262,263 | 排除了根 README，且其中两个项目 README 后续合计增长 17 字节 |

当前 1,607 与 v0.1 1,606/642,262,263 的差异可完全解释：

1. 根 01_原始数据/README.md 当前为 820 字节，v0.1 明确未登记；
2. 仅供参考/受限来源/README.md 比 v0.1 清单记录增加 10 字节；
3. 外部数据/力学曲线/README.md 比 v0.1 清单记录增加 7 字节。

这两个 README 是项目内说明，不是第三方测量数据。v0.1 清单作为冻结证据不回写；v0.2 用新的 occurrence/revision 身份记录当前内容。

| 一级路径 | 文件数 | 字节数 |
|---|---:|---:|
| 代码仓库镜像 | 1,719 | 265,715,105 |
| 外部数据 | 62 | 314,455,919 |
| 基础数据 | 4 | 66,147,698 |
| 仅供参考 | 4 | 75,865,428 |
| 根 README | 1 | 820 |

代码仓库镜像数字包含 .git/**；扣除 Git 元数据后的仓库文件数为 1,536。

## 3. 文件很多为什么不等于科学样本很多

全部 1,790 个文件按路径和扩展名可复算为：

| 机器分类 | 文件数 | 字节数 | 文件占比 | 字节占比 |
|---|---:|---:|---:|---:|
| CSV/XLSX/ZIP 数据容器候选 | 1,336 | 491,854,241 | 74.64% | 68.11% |
| 代码或模拟输入 | 216 | 84,318,327 | 12.07% | 11.68% |
| Git 元数据/缓存 | 183 | 79,921,870 | 10.22% | 11.07% |
| 文档、SI 和图件 | 36 | 65,499,334 | 2.01% | 9.07% |
| 模型或派生数组 | 7 | 578,483 | 0.39% | 0.08% |
| 环境与配置 | 12 | 12,715 | 0.67% | <0.01% |

1,336 个数据容器进一步分解为：

| 家族 | 文件数 | 独立样本风险 |
|---|---:|---|
| MatImpute 人工缺失变体（全材料域） | 1,188 | 多个 benchmark 母表的缺失方案，不增加科学样本；其中 PUE 为 207 个 |
| MatImpute 填补/指标结果（全材料域） | 12 | 模型派生或聚合指标，不是观测 |
| MatImpute 其他 benchmark 输出（全材料域） | 27 | 评估、图件或派生；不得解释为 PUE 数量 |
| MatImpute 源 benchmark 表 | 13 | 多数非 TPU，且与 DQ 有精确重复 |
| DQ 源表 | 10 | PUE 母表有价值，其余按材料域筛选 |
| DQ processed 投影 | 7 | 母表列投影或派生，不增加样本 |
| PolyGraphMT 计算数据 | 21 | 计算观测候选，需保留计算条件 |
| ADEPT 候选表 | 1 | 模拟输入，不是已有性能观测 |
| viscosity-modeling 实验表 | 2 | 39 个配方及温度—黏度曲线 [4] |
| 基础数据 | 4 | 含虚拟候选、单体和内部方案 |
| 外部数据 | 49 | 实验、计算和文献附件混合 |
| 受限数据 | 2 | 仅限受控参考 |

旧设计中的“93 个非明显派生文件”现可机器解释：

1336 - 1188 - 12 - 27 - 7 - 7 - 1 - 1 = 93

其中第二个 7 是 DQ/MatImpute 跨仓相同源表，两个 1 分别是内部方案工作簿和只含代码的 PU18 ZIP。因此 93 是文件级候选口径，不是 93 个数据集，更不是 93 个独立实验样本。继续扣除 PolyOmics PURT 子集、archive/extracted 镜像、同哈希错名曲线和单目标投影后，文件级非镜像候选还会下降。

## 4. 精确重复与必须冻结的血缘

全量 SHA-256 得到 33 个重复组、130 个文件实例和 97 个冗余实例；排除 .git/** 后为 8 个重复组、17 个实例和 9 个冗余实例。

| 组 | 实例 | 决策 |
|---|---|---|
| Zenodo4156000 Empa0.3/Empa0.5 两个不同名 CSV | SHA-256 2D7202E81B690055CECF7B726AB4DFAE72FF42056DEF2B16F9C8330138DCC97B | 不按文件名当两次实验；核对仓库元数据和条件后裁决 [26] |
| DQ datasets/processed 与 MatImpute bandgap.csv 三份 | SHA-256 7924BB17D9DC69ACE7D15229754C2588C041ED7DAB0C83B1F68232F06532C3E7 | 非 TPU 方法基准；保留镜像血缘，不计主域 |
| DQ/MatImpute 的 BMDS、PUE、Crystal、Bala-cls、glass、Bala-reg | 6 个双份哈希组 | 共享 content blob 和 parent dataset；PUE 只保留一个规范母表 [1], [12] |

ZIP 内部还存在两组文件级 SHA 无法直接看出的镜像：

- PU18_Menon2019_figshare.zip 内 4 个 Python 文件与已解压目录逐个相同；该包没有论文所需训练表。[2]
- Zenodo15490464/TPU_BC_CFF_raw.zip 内 4 个 XLSX 与旁边解压文件逐个相同；ZIP 和解压文件不能计作两个数据来源。[28]

此外，PolyOmics_PURT.csv 的 3,384 个 UUID 全部属于 general 母表，必须登记 `subset_of`。PUE 家族经内容核验为 326 行母表、2 个 DQ 纯列投影、207 个缺失变体和 6 个模型/基准输出；即 209 个派生容器，而不是 MatImpute 全材料域的 1,188 个缺失文件。两个母表 occurrence 字节相同，SHA-256 为 `e5d07b13764089579f90f16fda6a70024d67c683f5bdd41591720f9474308040`；207 个变体正好构成 23 列 × 9 缺失率并产生 33,741 个刻意空值，材料性差异为 0。[1], [12], [20]

## 5. 计算与模拟数据的高价值路线

### 5.1 PolyOmics：第一优先计算母库

| 文件 | 来源记录 | 唯一结构口径 | 关系 |
|---|---:|---:|---|
| general | 95,335 UUID | 78,379 个不同 smiles_list | 计算母表 |
| PURT | 3,384 UUID | 3,264 个不同 smiles_list | general 的完整 UUID 子集 |

general 含 95,335 个 UUID 来源记录、78,379 个大小写敏感 exact `smiles_list` 和 22 个 QoI；22 个 QoI 共得到 1,932,365 个有限数值与 165,005 个缺失单元格。存在 13,016 个重复 exact-structure 组和 16,956 条额外 UUID，其中 348 个结构组具有不同固定上下文。UUID 是来源记录身份，不是 95,335 个独立计算活动；在方法、输入输出、协议和质量门闭合前，`computational_activity_id` 保持空值。PURT 的 3,384 行均可回连 general，但其中 32 行在 `abbe_number_sos` 或 `efdp_permittivity_imaginary` 上只有末位十进制格式差异，全部在相对容差 `1e-12` 内数值等价；因此它是逻辑子集而不是字节级子集，必须保留 32 条格式化 lineage。`class_PURT=True` 也不证明线性 TPU、热塑加工性、配方身份或可合成性。[20]

### 5.2 PolyGraphMT：DFT/MD/GC 多任务属性层

21 个 CSV 共 44,083 个来源行；`RG_MD.csv` 中 1 行的 `SMILES` 是字面身份哨兵 `nan`，不得作为结构或计算观测候选。隔离该行后为 44,082 个属性候选、12,271 个按原始大小写精确去重的有效 SMILES：

| 方法标签 | 文件数 | 来源行 | 有效身份属性候选 |
|---|---:|---:|---:|
| DFT | 6 | 16,616 | 16,616 |
| MD | 14 | 15,334 | 15,333 |
| GC | 1 | 12,133 | 12,133 |

按“属性目标/文件 + 大小写敏感 exact SMILES”统计，存在 224 个重复键组和 248 个额外行；其中 144 个冲突组对应 158 条冲突额外记录，另有 90 条数值冗余额外记录。若错误地对 SMILES 执行 `casefold`，会把表示芳香原子的 `c` 与脂肪族碳 `C` 等化学上不同的字符串合并；该口径禁止进入数据库。12,271 个有效 PolyGraphMT SMILES 全部包含在 ADEPT 的 13,272 个有效 SMILES 中，因此两者先登记同论文伴生关系和 exact-containment 证据；只有明确生成/输出血缘后才能声明派生方向。不得按 SMILES 静默平均或保留第一行；必须先保存来源行、属性、方法、条件和 lineage，再判定重复计算、不同条件、冲突或独立观测。[8]

### 5.3 ADEPT：模拟生成器，不是已完成模拟结果

`SMILES.csv` 有 13,341 个 PID 记录、13,272 个 exact SMILES，其中 63 个结构对应多个 PID，共 69 条额外 PID 连接。仓库还含 Psi4、LAMMPS、高通量无定形结构和模量工作流。当前主要是候选、代码和模拟输入，101 个 `.elastic`、7 个 `.in` 等应登记 `simulation_input`，不能成为性能观测。固定提交为 `5bbf4bbd29f545ca9bca8841efbea31a65219d34`。[8]

### 5.4 PI1M 与 SMiPoly：候选空间和反应约束

PI1M 有 995,799 个唯一 SMILES；SMiPoly 有 1,083 个唯一 `comID` 来源记录，但只有 1,071 个大小写敏感的精确 SMILES，包含 10 个重复 SMILES 组和 12 条额外记录。`comID` 与化学体系身份必须分列，不能用记录 ID 虚增候选结构数。二者用于虚拟候选空间、结构表征、官能度和聚合反应规则，不提供已合成 TPU 的强度、韧性或 Tg 标签。[6], [7]

### 5.5 跨库 exact-string 重叠：最低限度泄漏保护

| 大小写敏感原始结构字符串交集 | 数量 |
|---|---:|
| PI1M ↔ ADEPT | 324 |
| PI1M ↔ PolyOmics | 5,203 |
| PI1M ↔ PolyGraphMT | 307 |
| ADEPT ↔ PolyOmics | 532 |
| ADEPT ↔ PolyGraphMT | 12,271 |
| PolyOmics ↔ PolyGraphMT | 520 |

上述数字是去除首尾空白、保留大小写、未经化学标准化的 exact-string 交集下界，不等同于标准化后的化学等价。它们已经足以证明按文件行随机拆分会泄漏；后续必须按研究/来源族与规范化结构联合分组，并继续做标准化、图同构和近等价审计。[6], [8], [20]

## 6. 实验数据优先适配顺序

1. Eom 2021 主/补充 Source Data：线性 TPU 氢键、温度、拉伸、循环和 DMA。
2. WPU-DCR、Nature 2025 主/补充 Source Data：配方、动态作用和性能。
3. PUE 326 母表：保留一个规范来源，只作为变换后小样本基准；不能恢复原始配方。[1]
4. viscosity-modeling：39 条温度—黏度曲线、4,619 个曲线数据行及两个 39 配方特征空间。[4]
5. Pugar 模量/Tg 的结构化 SI：先冻结材料范围、样品身份和来源关系。
6. 力学曲线 deposits：Li2026、4TU、TPU95A、Zenodo4156000、Zenodo15490464、Jiang、Schwarz 和 Zenodo1098206；适合曲线表征、速率/环境迁移，配方未知时不能冒充化学—性能记录。[21]–[28]
7. OpenPoly：通用聚合物外部先验；来源聚合、缺失和测试条件完成治理后再用。[5]

## 7. 受限和非领域资产

- DiMPU2025/source_data.xlsx、PUN2026/source_data.xlsx：restricted_reference，不进入公开训练或公开派生视图。
- Wiley DOCX：supplementary_information，只有方法、统计表和图像，没有 529 条原始曲线点；数据请求与许可完成前保持人工复核。[31]
- MatImpute/DQ 中的 bandgap、glass、MXene、concrete 等：可用于测试缺失治理算法，但为 excluded_non_domain，不得提高 TPU 主域规模。[12]

## 8. 冻结前必须通过的门

1. 动态 discovery 以规则排除 .git/**，发现集合与登记/证据排除集合双向差集为零；不得把 1,607 写进代码常量。
2. 根 README 获得 documentation 角色，消除 v0.1 的隐式缺口。
3. 8 个非 Git 字节重复组、两组 archive/extracted 镜像、PolyOmics 子集及 PUE 的 `326 + 2 + 207 + 6` lineage 全部闭环；MatImpute 全仓 61 个 `model_output` 与 PUE 6 个输出分别对账。
4. 每个文件保存稳定 occurrence/source-file/content 身份、大小、SHA-256、source scope、资产角色、六组生命周期状态和决定规则。
5. ZIP entry、XLSX sheet 和 CSV source row 具有结构化 locator；规范/派生记录经无环 lineage 可达文件。
6. 权利未知可以在授权环境内部解析，但必须阻断相应 train/release/publish/deploy 动作；私人仓库不改变许可。
7. 分别报告来源文件、来源记录、唯一结构、精确配方、批次、观测、曲线、曲线点、计算活动和计算观测；任何一项不得用其他基数替代。
8. PolyGraphMT 的 224 个重复组全部获得分类；其中 144 个冲突组/158 条冲突额外记录未解释前不得聚合，90 条冗余额外记录必须保留去重血缘；SMILES 去重与排序均不得使用 `casefold`。

## 9. 方法说明与局限

本报告使用递归文件枚举（含隐藏项）、明确 `.git/**` 路径过滤、流式 SHA-256、CSV/Excel/NPY/PNG 内容检查、ZIP entry 枚举和来源主键集合比较。2026-07-20 的两次真实隔离治理构建分别生成 11 个产物和 9 张治理表，逐文件字节一致、逻辑一致，共同快照逻辑哈希为 `6002262b0a1db595798690a86a2458b80666ae73462d9bd8dd1080072bed1af8`；1,607 个资产中 1,588 个登记、19 个证据排除，未知/歧义/读取失败均为 0。该结果仍为 `provisional_pass`：31 表完整外键物化、逐记录科学观测适配、动作级权利放行和独立快照批准尚未完成，因此不得写成最终论文训练集规模。

## 参考文献

[1] Ding, F.; Liu, L.-Y.; Liu, T.-L.; Li, Y.-Q.; Li, J.-P.; Sun, Z.-Y. Predicting the Mechanical Properties of Polyurethane Elastomers Using Machine Learning. *Chinese Journal of Polymer Science* **2023**, *41*, 422–431. https://doi.org/10.1007/s10118-022-2838-6.

[2] Menon, A.; Thompson-Colón, J. A.; Washburn, N. R. Hierarchical Machine Learning Model for Mechanical Property Predictions of Polyurethane Elastomers From Small Datasets. *Frontiers in Materials* **2019**, *6*, 87. https://doi.org/10.3389/fmats.2019.00087.

[4] Pugar, J. A.; Gang, C.; Millan, I.; Haider, K.; Washburn, N. R. Machine Learning of Polyurethane Prepolymer Viscosity: A Comparison of Chemical and Physicochemical Approaches. *Digital Discovery* **2025**, *4*, 3652–3661. https://doi.org/10.1039/D5DD00287G.

[5] Wang, J.-F.; Sun, Y.-B.; Chen, Q.-T.; Ji, F.-F.; Song, Y.-Y.; Ruan, M.-Y.; Wang, Y. OpenPoly: A Polymer Database Empowering Benchmarking and Multi-property Predictions. *Chinese Journal of Polymer Science* **2025**, *43*, 1749–1760. https://doi.org/10.1007/s10118-025-3402-y.

[6] Ma, R.; Luo, T. PI1M: A Benchmark Database for Polymer Informatics. *Journal of Chemical Information and Modeling* **2020**, *60*, 4684–4690. https://doi.org/10.1021/acs.jcim.0c00726.

[7] Ohno, M.; Hayashi, Y.; Zhang, Q.; Kaneko, Y.; Yoshida, R. SMiPoly: Generation of a Synthesizable Polymer Virtual Library Using Rule-Based Polymerization Reactions. *Journal of Chemical Information and Modeling* **2023**, *63*, 5539–5548. https://doi.org/10.1021/acs.jcim.3c00329.

[8] Alosious, S.; Liu, Y.; Xu, J.; Liu, G.; Zhang, R.; Jiang, M.; Luo, T. ADEPT-PolyGraphMT: Automated Molecular Simulation and Multi-Task Multi-Fidelity Machine Learning for Polymer Property Generation and Prediction. *Digital Discovery* **2026**, advance article. https://doi.org/10.1039/D6DD00206D.

[12] Xie, C.; Li, R.; Li, Y.; Xie, H.; Liu, Q. Imputation of Missing Data in Materials Science through Nearest Neighbors and Iterative Predictions. *Journal of Chemical Theory and Computation* **2025**, *21*, 70–78. https://doi.org/10.1021/acs.jctc.4c01237.

[20] Yoshida, R.; Hayashi, Y.; Furuya, H.; Hosoya, R.; Kaneko, K.; Sugisawa, H.; Kaneko, Y.; Takahashi, A.; Noguchi, Y.; Nanjo, S.; et al. Omics-Scale Polymer Computational Database Transferable to Real-World Artificial Intelligence Applications. *arXiv* **2025**, arXiv:2511.11626. https://doi.org/10.48550/arXiv.2511.11626.

[21] Ritzen, L.; Montano, V.; Garcia, S. J. 3D Printing of a Self-Healing Thermo-Plastic Polyurethane through FDM: From Polymer Slab to Mechanical Assessment. *Polymers* **2021**, *13*, 305. https://doi.org/10.3390/polym13020305.

[22] Jiang, C.; Zhang, L.; Yang, Q.; et al. Self-Healing Polyurethane-Elastomer with Mechanical Tunability for Multiple Biomedical Applications in Vivo. *Nature Communications* **2021**, *12*, 4395. https://doi.org/10.1038/s41467-021-24680-x.

[23] Li, X.; Xiao, C.; Izutsu, H.; et al. Toughening Elastomer via Sequentially Activated Multi-Pathway Energy Dissipation. *Nature Communications* **2026**, *17*, 5452. https://doi.org/10.1038/s41467-026-74148-z.

[24] Schwarz, D.; Pagáč, M.; Petruš, J.; Polzer, S. Effect of Water-Induced and Physical Aging on Mechanical Properties of 3D Printed Elastomeric Polyurethane. *Polymers* **2022**, *14*, 5496. https://doi.org/10.3390/polym14245496.

[25] Xu, C. Strain Rate Dependent Mechanical Performance of 3D-Printed Isotropic TPMS-Based Lattices in Thermoplastic Polyurethane; Mendeley Data, version 2, 2026. https://doi.org/10.17632/mc6zh4cwhf.2.

[26] Georgopoulou, A.; Tutu, S.; Clemens, F. Thermoplastic Elastomer Composite Filaments for Strain Sensing Applications Extruded with a Fused Deposition Modelling 3D Printer. *Flexible and Printed Electronics* **2020**. https://doi.org/10.1088/2058-8585/ab9a22.

[27] Wu, T.; Chen, B. Facile Fabrication of Porous Conductive Thermoplastic Polyurethane Nanocomposite Films via Solution Casting. *Scientific Reports* **2017**. https://doi.org/10.1038/s41598-017-17647-w.

[28] Rahmani, K. Raw Test Data for TPU/BC/CFF Composites; Zenodo, version 1, 2025. https://doi.org/10.5281/zenodo.15490464.

[31] Ding, F.; Liu, T.; Zhang, H.; Liu, L.; Li, Y. Stress-Strain Curves for Polyurethane Elastomers: A Statistical Assessment of Constitutive Models. *Journal of Applied Polymer Science* **2021**, *138*, 51269. https://doi.org/10.1002/app.51269.
