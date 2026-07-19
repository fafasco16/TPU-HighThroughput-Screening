# TPU 数据库 v0.2 治理构建验收报告

> 验收日期：2026-07-20
> 状态：`provisional_pass`
> 边界：本报告证明资产与来源治理构建可复现，不代表科学观测已经全部适配、权利动作已经放行、数据库已经冻结或模型可以训练。

## 1. 验收结论

在不修改 `01_原始数据`、不覆盖 v0.1 冻结快照、不写入 `02_暂存数据` 至 `06_审核导出` 的条件下，分别在 `临时构建/治理构建A` 和 `临时构建/治理构建B` 执行了两次真实全量构建。两次构建均生成 11 个声明产物、9 张治理表，逐产物 SHA-256 完全一致，表级与快照级逻辑哈希完全一致。

- 输入资产：1,607
- 已登记：1,588
- 有证据排除：19
- 未分类、歧义、读取失败、未知来源范围、范围不一致、缺失状态：均为 0
- 共同快照逻辑哈希：`6002262b0a1db595798690a86a2458b80666ae73462d9bd8dd1080072bed1af8`
- 结构重叠画像逻辑哈希：`ad6f495c3f07bb85923333f53bfd789404a3c2dc2ea38ce16ec5300c95a605cc`
- 训练/验证拆分：未创建
- 训练权重：未设置
- 已准入计算观测：0

因此，“模拟计算数据可以纳入”已经落实为独立计算来源、保真度、体系候选和数值性质 occurrence 的可复算画像；尚未满足协议、单位、体系映射、质量和权利门的记录不会仅靠降低训练权重进入模型。

## 2. 资产角色对账

| 资产角色 | 文件数 |
|---|---:|
| `code` | 117 |
| `computed_property_output` | 1 |
| `derived_duplicate` | 1,196 |
| `documentation` | 13 |
| `excluded_non_domain` | 19 |
| `mirror_duplicate` | 2 |
| `model_artifact` | 1 |
| `model_output` | 61 |
| `primary_data` | 49 |
| `restricted_reference` | 2 |
| `simulation_input` | 112 |
| `simulation_output` | 20 |
| `subset_view` | 1 |
| `supplementary_information` | 13 |
| **合计** | **1,607** |

资产媒体类型由项目内冻结映射决定，不再依赖 Windows 注册表或系统 `mimetypes`。现有 1,607 条资产的身份、角色和媒体类型联合哈希为 `daf895ecb2694b047c13c64d252567ef5de67f56f7cd059b8245580bafb6c89a`。

## 3. 来源、引用与权利候选

| 治理表 | 行数 |
|---|---:|
| `source` | 84 |
| `source_scope` | 1,742 |
| `source_scope_relation` | 101 |
| `source_locator` | 1,607 |
| `citation` | 52 |
| `citation_assignment` | 151 |
| `rights_action_candidate` | 13,936 |

来源治理逻辑哈希为 `437fee7ed84ff22839c73b18f350d9b3c28ff90bbb05892429f31cf7418d0e2b`。52 条 citation 均有纯文本参考文献、实名作者、题名、年份、DOI/规范标识和 CSL；7 条原台账使用 `et al.` 的作者表保持 `pending`，未伪造缺失作者。`rights_action_candidate` 是 staging 候选，不是正式 `rights_action_decision`；当前没有任何 v0.2 `allow` 权利裁决。

## 4. 计算数据实盘画像

| 来源 | 文件范围 | 来源记录 | exact 结构候选 | 数值性质 occurrence | 关键限定 |
|---|---:|---:|---:|---:|---|
| PI1M | 1 | 995,799 | 995,799 | 0 | 假想结构空间，不是性能标签 [6] |
| SMiPoly | 1 | 1,083 `comID` | 1,071 | 0 | 10 个重复结构组、12 条额外记录；记录身份与化学身份分列 [7] |
| ADEPT | 112 | 13,341 PID | 13,272 | 0 | 111 个模拟输入；当前没有可回连输出的计算观测 [8] |
| PolyGraphMT | 21 | 44,083 | 12,271 | 44,082 | DFT 16,616、MD 15,333、GC 12,133；隔离 1 个 `nan` 身份 [8] |
| PolyOmics | 2 | 95,335 UUID | 78,379 | 1,932,365 | 22 个 QoI、165,005 个缺失单元格；UUID 不是独立计算活动 [20] |
| DQ/MatImpute PUE | 217 | 326 | 不可由变换表恢复 | 0 | 1 个母集、209 个派生容器、6 个 PUE 输出；全仓 61 个模型输出均不是材料观测 [1], [12] |

PolyGraphMT 按“属性目标/文件 + exact SMILES”存在 224 个重复组和 248 条额外记录，其中 144 个冲突组对应 158 条冲突额外记录，另有 90 条冗余额外记录。冲突解释和单位/协议闭合前，不生成静默平均的聚合标签。

PolyOmics 的 95,335 个 UUID 中有 13,016 个重复 exact-structure 组和 16,956 条额外记录，其中 348 个结构组具有不同固定上下文。PURT 的 3,384 个 UUID/3,264 个 exact `smiles_list` 只作为 `class_PURT=True` 逻辑视图；该分类不证明线性 TPU、热塑加工性或可合成性。[20]

## 5. DQ/MatImpute 内容级派生验真

两个 PUE 母表均为 326 × 24，逐字节相同，SHA-256 为 `e5d07b13764089579f90f16fda6a70024d67c683f5bdd41591720f9474308040`。构建时逐文件验证：

- 2 个 DQ 文件是保持行序和原始单元格的纯列投影；
- 207 个缺失变体严格等于 23 个非 ID 列 × 9 个缺失率；
- 每个变体只允许文件名指定的列出现空值，缺失数为 `round(326 × ratio)`；
- 207 个文件共有 33,741 个刻意空值，材料性差异为 0；
- 6 个 PUE 输出包括 2 个 RDF 数组、2 个 15 行误差表、1 个 1,449 行聚合指标网格和 1 个 180 行分层 RMSE 工作簿；它们不是填补后的逐材料预测；
- MatImpute 全仓 61 个 `model_output` 已逐内容分为 6 个 NPY、20 个 benchmark CSV、8 个 RMSE 工作簿、1 个根 PNG、11 个聚合指标 CSV 和 15 个图件，新增材料观测为 0。[1], [12]

## 6. exact-string 泄漏下界

| 来源对 | 大小写敏感 exact-string 交集 |
|---|---:|
| PI1M ↔ ADEPT | 324 |
| PI1M ↔ PolyOmics | 5,203 |
| PI1M ↔ PolyGraphMT | 307 |
| ADEPT ↔ PolyOmics | 532 |
| ADEPT ↔ PolyGraphMT | 12,271 |
| PolyOmics ↔ PolyGraphMT | 520 |

该口径只去除首尾空白并保留大小写，是未经化学标准化的重叠下界。ADEPT 的 13,272 个 exact SMILES 中有 63 个多 PID 结构组和 69 条额外 PID 连接；PolyGraphMT 的 12,271 个有效结构全部包含于 ADEPT。后续拆分必须同时按研究/来源族与规范化结构分组，禁止按文件行随机拆分。[6], [8], [20]

## 7. 双构建产物哈希

| 产物 | A/B 共同 SHA-256 |
|---|---|
| `TPU数据库_v0.2_计算数据准入报告.md` | `a32c3f3fcc030db690215b2f680d1a9b5e61bfa74926a97ca2b9a32ecdd5fade` |
| `TPU数据库_v0.2_资产登记审计.json` | `1fd4df2041df5c040841a11344728ffb555907fcd422a40bc9fbbb8035194f2c` |
| `v0.2全量资产登记.csv` | `d8f5bfc8b6b8d6c36ac376f1433323c7c68fc9357bfd161d495969ef82497384` |
| `v0.2引用.csv` | `3caed9820d6d8963e9d185a2f91fd971165e87d0ab02a6bf826442d45d580fde` |
| `v0.2引用分配.csv` | `f8cc56984ff8b77d825a568ac3bb16eabe31461191b7aa33a21d52bb19579eb2` |
| `v0.2权利动作候选.csv` | `d8f211aa676891edad77d45f7d2f89a1da7f8ec5b8660d82ff3d7df92a765865` |
| `v0.2来源.csv` | `e49df33ab9dd9068d5f2dfbc06b01f5e855136a4fa4784b1ed2585b339f934c4` |
| `v0.2来源定位.csv` | `99bf06200b56e3fd421d8e10cf320c8bbf8110d17c279e3448280e1668a199ba` |
| `v0.2来源范围.csv` | `ff4d583d08b41bd32e68405138327141ab8850c48c949609e8a732f6c0c7ad97` |
| `v0.2来源范围关系.csv` | `87e3670d8b5f76261df429b7e5f60c6b2d5daf4b7926d96b322693` |
| `v0.2精确重复组.csv` | `5486e4d661fade9a8e1f3d770296652bb5f0c3d5fb0781fb2516e030423338eb` |

合同语义哈希：schema `63bb1bcea8e5791e86368279956c1b0c30cc09da918a745da92fc1b31836433d`；enums `35c236c8a45decf26e61cd79a2e97bd53b543141d96619a836f05a11b8508610`；rules `3e3859b683a14b8ff5d548f45b4cc067e08e906339ca2808c71d513c347db3d1`。

## 8. 测试与尚未通过的门

全仓测试为 625 项全部通过；statement + branch 综合覆盖率为 92.11%，高于 90% 门槛。冻结前仍必须完成：

1. 将当前 9 张构建表扩展为合同要求的 31 表完整物化，并在 DuckDB 中验证全部 PK、UK、FK、CHECK 和条件规则；
2. 将 `rights_action_candidate` 解析为带证据闭包的正式动作决定；未知许可继续 fail closed；
3. 编写实验/计算科学实体合同和逐来源适配器，逐记录绑定体系、协议、单位、条件、质量和不确定度；
4. 对结构进行化学标准化、图同构/近等价和 TPU 化学域覆盖审计；
5. 完成独立复核与快照批准者分离后，才可冻结 v0.2；训练权重和模型方案属于冻结之后的单独阶段。

## 参考文献

[1] Ding, F.; Liu, L.-Y.; Liu, T.-L.; Li, Y.-Q.; Li, J.-P.; Sun, Z.-Y. Predicting the Mechanical Properties of Polyurethane Elastomers Using Machine Learning. *Chinese Journal of Polymer Science* **2023**, *41*, 422–431. https://doi.org/10.1007/s10118-022-2838-6.

[6] Ma, R.; Luo, T. PI1M: A Benchmark Database for Polymer Informatics. *Journal of Chemical Information and Modeling* **2020**, *60*, 4684–4690. https://doi.org/10.1021/acs.jcim.0c00726.

[7] Ohno, M.; Hayashi, Y.; Zhang, Q.; Kaneko, Y.; Yoshida, R. SMiPoly: Generation of a Synthesizable Polymer Virtual Library Using Rule-Based Polymerization Reactions. *Journal of Chemical Information and Modeling* **2023**, *63*, 5539–5548. https://doi.org/10.1021/acs.jcim.3c00329.

[8] Alosious, S.; Liu, Y.; Xu, J.; Liu, G.; Zhang, R.; Jiang, M.; Luo, T. ADEPT-PolyGraphMT: Automated Molecular Simulation and Multi-Task Multi-Fidelity Machine Learning for Polymer Property Generation and Prediction. *Digital Discovery* **2026**, advance article. https://doi.org/10.1039/D6DD00206D.

[12] Xie, C.; Li, R.; Li, Y.; Xie, H.; Liu, Q. Imputation of Missing Data in Materials Science through Nearest Neighbors and Iterative Predictions. *Journal of Chemical Theory and Computation* **2025**, *21*, 70–78. https://doi.org/10.1021/acs.jctc.4c01237.

[20] Yoshida, R.; Hayashi, Y.; Furuya, H.; Hosoya, R.; Kaneko, K.; Sugisawa, H.; Kaneko, Y.; Takahashi, A.; Noguchi, Y.; Nanjo, S.; et al. Omics-Scale Polymer Computational Database Transferable to Real-World Artificial Intelligence Applications. *arXiv* **2025**, arXiv:2511.11626. https://doi.org/10.48550/arXiv.2511.11626. Dataset: https://doi.org/10.57967/hf/7475.
