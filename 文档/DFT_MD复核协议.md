# TPU 候选 DFT/MD 分层复核协议

## 1. 当前状态与目的

本协议对应 `候选/DFT_MD复核队列.csv` 的 `TPU-DFT-T1-v1`。队列有 48 条虚拟配方，来自固定格点：目标宏二醇 Mn 2000 g mol⁻¹、目标硬段质量分数 0.45、NCO/OH 1.02。它们由三构件联合 Morgan 指纹的 Tanimoto max-min 规则选出，用来覆盖结构空间，不是模型预测的性能前 48 名。

48 条配方包含 48 个不同二异氰酸酯、16 个宏二醇代理和 22 个二醇扩链剂，共 86 个唯一构件。第一层计算按唯一构件去重，每个构件只计算一次，再通过稳定 `candidate_id` 回连到配方。构件的 SMILES 来自 Gold-V；宏二醇仍是小分子双醇结构代理，目标 Mn 是未来真实低聚物的假设，两者不能混为同一分子。

## 2. Tier 0：输入和人工门

任何量化计算开始前，逐构件完成：

1. 由 `canonical_smiles` 生成三维结构，并核对价态、互变异构、立体化学、总电荷和自旋多重度；当前默认中性闭壳层只是需要确认的起始假设。
2. 将 `isocyanate_group_requires_SDS_review`、芳香或卤素结构警示与真实 SDS、供应商规格和本单位 EHS 规则核对。结构警示不是 GHS 分类，也不能替代 SDS。
3. 逐条确认原料或可行合成路线。`procurement_status=not_checked` 时不得把候选移入实验采购表。
4. 检查二异氰酸酯是否属于不稳定、瞬态或仅数据库枚举结构；不能因为 RDKit 可解析就认定可分离、可储存或可购买。

未通过任何一项时，记录失败原因并停止该构件后续计算；不得删除记录或把空值填为有利结果。

## 3. Tier 1：构件构象与反应性代理

### 3.1 推荐计算层级

1. 使用 CREST 进行构象采样，能量模型为 GFN2-xTB；保留搜索版本、命令、温度、能窗、溶剂模型和随机种子。GFN2-xTB 是快速半经验紧束缚方法，CREST 用于低能构象空间自动探索[177,178]。
2. 对能窗内去重后的低能构象进行 `r2SCAN-3c` 几何优化与频率计算；频率没有虚频才标记为局部极小值。`r2SCAN-3c` 是带基组、色散和基组叠加误差修正的复合方法[179]。
3. 当前原型路线以无溶剂/熔融体系为主要假设，气相结果只作统一结构代理。确定真实溶剂或加工环境后，必须用明确的隐式溶剂或凝聚相模型做敏感性复算，不能把气相排序直接解释为反应釜排序。

推荐保存的逐构件输出为：最低构象电子能和热修正、5 kcal mol⁻¹能窗内构象数、偶极矩、各向同性极化率、前线轨道能，以及 NCO 碳或 OH 氧的原子电荷与局部空间可及性。电荷方案、轨道定义和软件版本必须作为字段保存；这些量是方法依赖的反应性/相互作用代理，不是实验反应速率。

### 3.2 结果门禁

每个计算记录至少包含：

```text
calculation_id
candidate_id
formulation_ids
input_smiles
charge
multiplicity
software_and_version
method
basis_or_composite_method
solvation_model
conformer_protocol
geometry_converged
frequency_status
electronic_energy_hartree
thermal_correction_hartree
descriptor_name/value/unit
run_directory_or_archive
input_sha256
output_sha256
warnings
```

优化未收敛、存在非预期虚频、构象搜索失败或电荷/自旋未确认时，该构件只保留失败记录，不能参与数值排名。跨构件比较必须使用完全相同的方法、软件主版本、温度和溶剂约定。

### 3.3 Tier 1 能回答和不能回答的问题

Tier 1 可以比较同一方法下的构象柔性、局部电性、NCO/OH 位点可及性和可能的相互作用倾向，并据此把 48 条队列进一步缩小到成对反应路径计算。它不能直接预测 TPU 的拉伸强度、断裂伸长率、韧性、循环恢复、DMA 模量或宏观相分离尺度。

如果需要比较真实反应活性，下一层应针对二异氰酸酯—醇模型对建立反应物复合物、过渡态和产物路径，报告自由能垒及构象/溶剂敏感性。单独用 HOMO/LUMO 或某一种原子电荷作为反应速率结论是不充分的。

## 4. MD 启动门与建议层级

当前全部队列的 `md_stage` 为 `on_hold_pending_real_macrodiol_identity_Mn_Mw_PDI`。只有同时闭合下列信息才允许启动原子级 MD：

- 宏二醇的真实重复单元、端基、Mn、Mw、PDI 和聚醚/聚酯家族；
- 二异氰酸酯、扩链剂、硬段质量分数、NCO/OH 与目标转化率；
- 链构建规则、链数、链长/分布、残余单体和端基处理；
- 力场来源、未覆盖原子类型的 DFT 参数化与验证；
- 初始密度、退火/压缩过程、温度、压力、时间步长、平衡时间和独立重复；
- 密度、能量、体积和链构象的收敛判据。

启动后建议先做小规模可重复性试验，再计算密度、Tg、回转半径、内聚能密度、氢键统计和软硬段空间关联。RadonPy 提供了全原子聚合物物性自动计算与数据组织的公开参考[145,146]，PolyOmics 提供可迁移的 PU/PURA 计算数据背景[151]；但这些来源不是本候选的实验标定，不能自动赋予 TPU 宏观力学预测可信度。

非平衡拉伸 MD 的应变率通常远高于实验测试；若后续使用，只能在相同协议内作相对排序，并同时报告尺寸、应变率、重复数和不确定度。不得把单条高速 MD 曲线直接写成实验拉伸曲线。

## 5. 从计算到实验的决策门

建议按以下顺序缩小候选：

1. 48 条结构多样性队列完成 Tier 0，留下身份、路线、SDS/EHS 和可得性均可复核者；
2. 对唯一构件完成 Tier 1，按收敛状态、构象复杂度和局部反应性代理排除异常者；
3. 对不超过 12 个配方做成对反应路径和真实宏二醇身份闭合；
4. 对不超过 6 个配方开展多重复 MD；
5. 综合结构新颖性、计算不确定度、原料安全和实验可执行性，形成 5–10 个实验短名单；
6. 合成后用 GPC、FTIR/NCO 转化、DSC/DMA、完整拉伸与循环曲线验证，并把真实批次写入 Gold-E，而不是回写虚拟候选标签。

## 6. 参考文献

[145] Hayashi, Y.; RadonPy Consortium. *RadonPy PI1070 Computational Polymer Dataset*, commit `840dd4a2b5f261fc9370bb6786eff0b71a463d2f`; GitHub, 2022. https://github.com/RadonPy/RadonPy/tree/840dd4a2b5f261fc9370bb6786eff0b71a463d2f/data.

[146] Hayashi, Y.; Shiomi, J.; Morikawa, J.; Yoshida, R. RadonPy: Automated Physical Property Calculation Using All-Atom Classical Molecular Dynamics Simulations for Polymer Informatics. *npj Computational Materials* **2022**, *8*, 222. https://doi.org/10.1038/s41524-022-00906-4.

[151] Hayashi, Y. *PolyOmics* [Data set]; Hugging Face, 2026. https://doi.org/10.57967/hf/7475.

[177] Bannwarth, C.; Ehlert, S.; Grimme, S. GFN2-xTB—An Accurate and Broadly Parametrized Self-Consistent Tight-Binding Quantum Chemical Method with Multipole Electrostatics and Density-Dependent Dispersion Contributions. *Journal of Chemical Theory and Computation* **2019**, *15* (3), 1652–1671. https://doi.org/10.1021/acs.jctc.8b01176.

[178] Pracht, P.; Bohle, F.; Grimme, S. Automated Exploration of the Low-Energy Chemical Space with Fast Quantum Chemical Methods. *Physical Chemistry Chemical Physics* **2020**, *22* (14), 7169–7192. https://doi.org/10.1039/C9CP06869D.

[179] Grimme, S.; Hansen, A.; Ehlert, S.; Mewes, J.-M. r2SCAN-3c: A “Swiss Army Knife” Composite Electronic-Structure Method. *The Journal of Chemical Physics* **2021**, *154* (6), 064103. https://doi.org/10.1063/5.0040021.
