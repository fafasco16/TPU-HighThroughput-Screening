# TPU 多保真 Gold 数据集定义

## 1. Gold 到底是什么

本项目的 Gold 不是只含实验数据的一张表，而是用于高通量筛选的多保真数据集合：

| 子集 | 内容 | 主要用途 |
|---|---|---|
| `Gold-E` | 真实合成、制样和测试得到的 TPU/TPUU 数据 | 性能标定、最终验证 |
| `Gold-C` | 可靠且可复现的 DFT、AIMD、MD、CGMD、CFD/PBE 与有限元数据 | 机理特征、过程窗口、预训练、低保真标签、复筛 |
| `Gold-V` | 来源可靠、结构可解析且生成/预测身份明确的虚拟候选 | 扩大化学空间、主动学习、候选排序 |

`Gold-E+Gold-C` 只用于来源级聚合：表示同一来源同时含实验与计算证据；逐条目标仍按 `target_origin` 拆回 `Gold-E` 或 `Gold-C`，不会把两种真值混成一种标签。

三类数据都可以进入参考范围。准入不是“一缺字段就全部删除”，而是根据任务建立 `completeness_score`、`fidelity_score` 和任务适用标志。真正不能接受的是来源不明、体系身份不明、方法不明，或把模型预测伪装成实验真值。

这里把“进入 Gold 参考范围”和“作为某个任务的高权重真值”分开判断：

- 可复现、协议完整且收敛可信的计算，即使暂时没有一一对应的实验标定，也可以进入 `Gold-C`，用于机理描述、表示预训练、低保真标签和候选复筛；只有在把它宣称为宏观实验真值时才必须做跨尺度实验校准。
- 规则枚举、生成模型或已有模型给出的虚拟候选，不要求先有实验标签即可进入 `Gold-V`；它们可用于化学空间、排序和主动学习，但预测值不回灌成实验真值。
- 缺少某些非本任务字段不会整条删除。系统保留空值、适用域和不确定度，并在任务视图中降权或仅作参考。

### 1.1 可靠来源优先，字段渐进补全

Gold 参考范围采用两级准入，不再把“字段尚未全部补齐”等同于“没有科学价值”：

| 准入状态 | 进入 Gold 参考范围 | 可否直接作为监督真值 | 典型情形 |
|---|---:|---:|---|
| `admitted_reference` | 是 | 通过具体任务门后可以 | 来源、体系、方法、协议、单位和质量证据达到任务要求 |
| `conditional_reference` | 是 | 暂不可以 | 来源可靠、数据身份明确，但协议细节、适用域、不确定度或实验映射仍待补全 |
| `blocked` | 否 | 否 | 来源无法回溯、体系/方法/单位不可恢复、来源已撤回，或预测值被伪装成实验值 |

这里的“来源可靠”包括带 DOI/版本的数据仓库和官方 SI，也包括能够固定 commit、审计输入输出血缘的作者或机构仓库。同行评议状态、许可证和字段完整度都必须记录，但它们分别控制科学可信度、可训练/可再分发权利和任务权重，不能混成一个简单的删除开关。

因此，来源可靠的 DFT、AIMD、经典 MD、粗粒化 MD、反应 CFD、群体平衡、动力学模拟、有限元结果、规则枚举结构以及模型预测都可以进入相应 Gold 子层。协议不完整的数值仍原样保留在条件参考层；只有准备把它用于某一性质的直接监督时，才要求通过该任务的单位、映射、适用域、收敛和泄漏检查。

当前三条实际准入案例把这条原则固定下来：铜配位PU热解来源即使只有一个商业基材且缺SMILES/完整配方，其TGA、热解动力学和DFT路径仍分别进入 `Gold-E` 与 `Gold-C`；SLS-TPU来源的75个Weight值即使单位尚未闭合，也保留为 `conditional_reference`，而其余300个单位闭合坐垫性能端点进入正式参考；非异氰酸酯PHCU来源的拉伸、TGA、XRD、GPC和NMR曲线保留在 `Gold-E`，其中列映射推断、GPC无校准和DSC标签冲突只限制相应任务与权重。缺失字段限制任务和权重，不抹去可靠来源中其他数据的价值。

第九批进一步采用同一口径：PolyUniverse 的二异氰酸酯结构即使没有性能标签，也可按结构门进入 `Gold-V` 候选层；RadonPy 和 PolyOmics 中方法、体系和质量检查可追溯的 DFT/MD/NEMD 结果进入 `Gold-C`。直接聚氨酯类别、平衡检查通过且性质定义完整者正式准入；聚脲相邻体系、非PU通用聚合物或协议/适用域仍待补者保留为条件迁移参考。它们不会因为缺少逐条实验配对而被删除，也不会因此与 `Gold-E` 等权或被计作新的实验材料。

第十批把“宽准入、严标注”落实到主查询表：OMG的47,676个直接计算体系和1,191,900个属性值全部进入`Gold-C`，其中2,086个PU体系为高相关参考，其余45,590个体系作为通用聚合物条件迁移；OpenPoly的3,502个结构全部进入候选审计，跨源去重后净增3,501个`Gold-V`实体，只有4,524个非空MD单元进入`Gold-C`。ScienceDB PUE-643只把三个实验变换目标物化为1,929条`Gold-E`标签，20个标准化输入保留为上下文；无溶剂PU动力学的171个实测`%NCO`点全部保留。可靠计算和虚拟数据因此不会因“不是实验”被删除，但每条仍保留方法、单位、适用域和条件准入身份。

第十一批进一步修复“已审计但数值未进主表”的缺口：五个既有CC BY 4.0来源新增物化2,630条`Gold-E`数值，包括逐试样力学、填料—配方—拉伸、SLS工艺—性能、泡沫配方—反应—形貌/输运和文献汇总端点。2,102条进入正式参考，528条因商业牌号化学未解析、来源汇总冲突、单位或底层协议不完整进入条件参考。36条明确为空的FDM冲突证据不填零、不进入数值表。这说明宽准入并不等于宽松造数：可靠但不完整的真实值可以保留，空值和证据行不能伪装成标签。

第十二批把同一原则扩展到反应发泡过程模拟：Mendeley Data固定version 1的PUFoam归档提供源码、输入和一个完整二维mixing-cup计算结果。主表保留250个官方体积平均值以及8,764个可从OpenFOAM标量场复算的时空统计，共9,014条`Gold-C`过程数值；其中4,293条字段语义闭合的数值进入正式参考，4,721条PBE矩、nodes/weights、Psi等语义未闭合数值进入条件参考，而不是整源删除。全部时间点、网格场和统计量共享一个`simulation_key`，因此计算体系数仍为1；材料与配方身份因具体异氰酸酯和多元醇未闭合而保持未知。论文报告的12组实验只作为模型级密度/温度对比证据，不会被误写成12套归档计算，也不会把所有场变量标成逐字段实验验证。该来源可用于NCO/OH/水—温度—密度—泡群演化和发泡窗口迁移，但不进入精确SMILES监督，也不冒充线性TPU拉伸真值。

第13至16批把“可靠来源即可进入参考范围”落实到更广的实验和模拟数据。日期籽油PU-PIR的光谱、热分析、力学曲线和配方、PU汽车座椅的IFD/松弛曲线及舒适性指标、再生PU泡沫的压缩/黏度/导热与配方均进入`Gold-E`；轴单位、测试协议、商业组分或有效形变区间不完整的观测标为`conditional_reference`，不整源删除。多组分PU多尺度研究的115个表格数值进入`Gold-C`，但按`record_role`明确分成68个组成/运行输入和47个模拟输出；输入用于复现和条件化，不能被统计为性能标签。10个CG体系、13个模拟运行、59条再生泡沫压缩曲线和十几万曲线点分别按各自身份计数，绝不按数值行数扩增成材料数。所有新增记录都有来源定位、文件哈希、引用键和`split_group`，训练权重仍为空。

第19批老化植物基PU泡沫进一步说明模拟数据不需要先有逐条实验配对才有价值。4,200条Abaqus曲线按输入条件归并为3,868个唯一工况，物化19,340条`Gold-C`紧凑标量；这些工况全部属于同一商业组分名义配方和同一来源家族。可靠模拟因此可以进入参考层，但重复运行、曲线点和工况数不能冒充新材料数，且与同族实验数据必须整组拆分。

第17批继续优先消化已审计原件：DRUM低天花板TPUU的20条拉伸和4条循环曲线形成110,281个原始点，并从拉伸曲线复算60个强度、断裂伸长与韧性端点，全部进入`Gold-E`正式参考。4个材料代码仍未解码为完整定量配方，因此这些记录适用于同族材料的曲线、耗散与回收行为研究，不会被误用为“仅凭SMILES预测宏观性能”的闭环标签。4条旧版`.xls` DMTA曲线虽已审计，尚未进入跨平台长表；未接入不等于删除，待解析链和单位检查冻结后再增量物化。

同批糖填充热可逆超分子聚氨酯来源说明了论文曲线数据的记录级分层：审计得到99条 `Gold-E` 真实压缩或实验派生记录、155条 `Gold-C` 已发表连续体/增强模型曲线，以及77条同时叠加实验与模型的 `Gold-E+Gold-C` 记录；来源级可聚合为 `Gold-E+Gold-C`，但逐条监督仍按 `data_origin` 分开。该源只有9个材料条件，不能把28个OPJ、144个工作表、331条图层引用或115,013个点扩大解释为材料数。实验记录潜在上限0.75，已发表模型记录上限0.25，条件或重复记录按质量门进一步降至0.10或0。由于精确聚合物SMILES未闭合，纯SMILES结构任务权重为0；CC BY-NC 3.0则独立控制非商业隔离，不改变其科学参考身份。

## 2. 一条用于筛选的记录包含什么

最终模型使用的宽表以“一个明确材料体系，在一个明确条件下，对一个明确目标的观测或预测”为一行。核心字段如下。

### 2.1 身份与来源

```text
record_id
candidate_id
formulation_id
batch_id
specimen_id
observation_id
source_id
doi_or_url
source_locator
file_sha256
license
data_origin              experimental / dft / md / virtual / prediction
fidelity_level
gold_layer               Gold-E / Gold-C / Gold-V / Gold-E+Gold-C
gold_admission_status    admitted_reference / conditional_reference / blocked / evidence_only
```

`gold_admission_status` 与训练权重是两个维度：一条可追溯的虚拟候选可以正式进入 `Gold-V`，同时其直接实验性质监督权重仍为 0。科学准入与训练、再分发权利也是两个维度：许可证待复核的数据可以保留为本地科学参考，但不会因此自动获得训练或公开再分发许可。当前机器总账已经在来源级和逐记录级写出科学准入、来源许可状态和权重上限。

总账中的 `weight_ceiling` 统一解释为“缺口闭合后的潜在权重上限”，不是原Gold层的当前训练权重。`conditional_reference`不能进入主监督；但在独立的任务化视图中，如果数值、单位、来源和目标语义满足一个明确的迁移/低保真任务，可以标为`auxiliary_train`并按上限再降权。单位或身份仍未闭合、角色只是输入、或任务不匹配者权重仍为0。`admitted_reference`也必须通过具体任务、训练权利和防泄漏门后，才成为`primary_train`。

### 2.2 化学结构

TPU 是多组分、分子量有分布的聚合物，不能只用一个总 SMILES 表示。权威输入是组分结构与反应关系：

```text
isocyanate_name
isocyanate_smiles_canonical
isocyanate_inchikey
isocyanate_functionality

polyol_name
polyol_repeat_unit_smiles
polyol_end_group
polyol_mn_g_mol
polyol_mw_g_mol
polyol_dispersity
polyol_functionality
polyol_hydroxyl_value_mg_koh_g

chain_extender_name
chain_extender_smiles_canonical
chain_extender_inchikey

crosslinker_smiles_canonical
catalyst_smiles_canonical
additive_or_filler_smiles
polymer_repeat_unit_smiles_or_bigsmiles
```

能明确写出重复单元时保存 polymer SMILES/BigSMILES；不能唯一表示时，保留“异氰酸酯 + 多元醇重复单元和端基 + 扩链剂 + 反应图”，不人为编造一个总聚合物 SMILES。

### 2.3 配方与计量

```text
component_mass_fraction
component_mole_fraction
component_equivalent_fraction
nco_oh_ratio
hard_segment_mass_fraction
soft_segment_mass_fraction
catalyst_loading
additive_loading
filler_loading
water_content
```

每个组分均保留原始用量、原始单位和规范单位。只知道材料代码但不知道计量时仍可保存为低完整度记录，待正文/SI 解码后升级。

### 2.4 合成、加工与材料状态

```text
synthesis_method          one_shot / prepolymer / reactive_extrusion / other
addition_order
reaction_temperature
reaction_time
atmosphere
conversion
drying_condition
post_cure_condition
processing_method         casting / extrusion / injection / hot_press / printing
processing_temperature
annealing_condition
material_state            virgin / recycled / healed / aged / composite
```

对 DFT/MD 或纯虚拟候选，不要求虚构实验工艺；这些字段可以为空，但必须有对应的计算或生成协议。

### 2.5 测试条件

```text
test_type
standard
instrument
temperature
relative_humidity
strain_rate
crosshead_speed
frequency
cycle_count
specimen_geometry
thickness
gauge_length
orientation
conditioning
replicate_index
```

没有测试条件的性能值仍可作为低完整度参考，但不能与协议完整的数据等权。

### 2.6 性能指标

每个性能都保存 `property_name、value_raw、unit_raw、value_canonical、unit_canonical、uncertainty、replicate_count、censoring、test_id`。

优先目标包括：

| 类别 | 指标 |
|---|---|
| 拉伸与强韧性 | 初始/割线模量、拉伸强度、断裂伸长率、屈服应力/应变、韧性、完整应力—应变曲线 |
| 循环与弹性 | 回弹率、残余应变、滞后能、循环保持率、疲劳寿命、完整循环曲线 |
| 动态力学 | `E'`、`E''`、`tanδ`、温度/频率扫描曲线 |
| 热性能 | `Tg`、`Tm`、结晶/熔融焓、`Td5/Td10/T50`、TGA/DSC 曲线 |
| 加工与流变 | 黏度、复黏度、`G'`、`G''`、熔融/打印窗口 |
| 耐久 | 吸水率、水解/湿热/UV/热氧老化保持率、磨耗、撕裂、压缩永久变形 |
| 应用任务 | 导电、介电、传感、阻燃、自愈、回收率；单独建立任务头 |

“弹性”和“韧性”不会被单一强度或伸长率代替。韧性优先由完整应力—应变曲线积分得到，并保存算法版本。

### 2.7 DFT/MD 字段

可靠计算数据可以进入 `Gold-C`。准入字段按“最小参考字段 + 方法条件字段 + 渐进补全字段”管理，不要求确定性 DFT 虚构随机种子，也不因尚无实验配对或部分协议字段待补而删除来源可靠、身份明确的计算记录。

条件参考的最小字段是：稳定来源定位、计算体系身份、计算/运行身份、计算来源类别、输出定义、原始值和原始单位。软件版本、精细参数、完整输入—输出血缘、主要协议、质量或收敛状态、适用域及与候选/组分的映射类型用于把记录升级为正式参考或直接低保真监督；缺失时必须留空并记录缺口，不允许猜测。

下列字段只在方法适用时必填：

```text
computation_id
mapped_candidate_or_formulation_id
mapping_type              exact / component / soft_segment_proxy / hard_segment_proxy
software_and_version
method_or_force_field
calculation_parameters
temperature_and_pressure
ensemble
chain_length_and_count
time_step
equilibration_and_production_time
strain_or_cooling_rate
random_seed
converged
uncertainty
```

- DFT/AIMD 记录泛函、基组或截断能、色散/溶剂设置、荷电/自旋以及 SCF/几何收敛。
- 经典 MD/CGMD 记录力场、边界条件、系综、恒温/恒压器、平衡段、生产段、时间步长和随机性证据。
- 反应 CFD/PBE 记录求解器、网格、组分与动力学输入、边界条件、时间步、场变量、守恒/收敛证据以及实验校准范围，归 `Gold-C`；网格单元和时间步只增加分辨率，不增加独立材料数。
- 直接有限元求解记录求解器、网格、材料参数、边界/载荷条件和收敛证据，归 `Gold-C`；机器学习代理输出归 `Gold-V`，不得当作直接数值求解或实验真值。
- 不确定度、实验映射和跨尺度校准是渐进补全字段；缺少时降低适用范围和上限，不自动取消计算参考准入。

可用计算指标包括：反应/解封能垒、结合能、氢键能、原子电荷、HOMO/LUMO、偶极矩、密度、内聚能密度、氢键数量与寿命、RDF、回转半径、相分离描述符、模拟 Tg、模拟力学响应，以及反应转化、发泡密度、气相体积分数、泡群矩、温度和流变/传热过程场。

计算值可以作为低保真标签，但必须保留 `target_origin=dft/md` 和协议。高应变率 MD 强度、MD Tg 等不能在无标记情况下与准静态实验强度、DSC/DMA Tg 合并成同一真值。

### 2.8 虚拟候选字段

`Gold-V` 采用最小可靠性门，不把“已经证明可合成”作为统一硬门。硬条件只有：候选身份稳定、至少一种结构表示可解析、来源/版本/文件位置可追溯、生成或预测身份明确，以及没有把虚拟值冒充实验。反应规则、父候选、适用域和不确定度能取得时应记录；缺失时保留空值、降低排序置信度或限定用途，不机械删除整条候选。以下字段均为推荐字段，其中除身份、结构、来源和数据身份外可渐进补全：

```text
candidate_id
component_smiles
reaction_rule
functional_group_match
synthesizability_score
commercial_availability
ehs_flags
predicted_properties
prediction_uncertainty
applicability_domain
generation_model_or_rule_version
```

虚拟候选不必已有实验标签。它们用于生成候选空间和主动学习；经 DFT/MD 复筛或实验验证后，保留原 `Gold-V` 血缘，并通过同一个 `candidate_id` 关联新增的 `Gold-C` 或 `Gold-E` 观测，而不是覆盖原记录。

## 3. 权重如何处理

不按“实验 1、模拟全删”处理，也不按数据点数量决定权重。初始权重由五项共同决定：

```text
sample_weight = source_reliability
              × fidelity_match
              × system_mapping_confidence
              × protocol_completeness
              × independence_factor
```

- 原始实验和独立重复通常权重最高。
- 经实验校准、体系映射明确的 DFT/MD 可以获得较高的辅助权重。
- 可靠但只代表软段或硬段的模拟数据可以用于预训练或描述符学习，权重较低。
- 虚拟预测可用于排序、半监督或主动学习，但保留预测来源和不确定度。
- 曲线点、轨迹帧、构象和随机种子不能因为数量多而压过独立材料体系。

原Gold-V/C/E只保存权重上限，不回写逐条训练权重。`结果/可用数据集`另外物化建议损失权重：潜在上限乘以质量、角色和独立性因子；同一曲线或同一模拟—性质的记录共享一个预算，并另给出任务内来源平衡抽样概率。它是可复现实验起点，不是经过最终模型和实验校准后的唯一权重。`Gold-C/Gold-V`的参考准入仍不等于与`Gold-E`等权，Gold-V候选监督权重始终为0。

## 4. 当前产物与可用任务视图

当前已经物化三个可直接查询、但仍属于参考阶段的 Gold 视图：

1. `结果/Gold_V_候选.csv.gz`：117,629 条结构级 `Gold-V` 参考，含规范 pSMILES/SMILES、端甲基代理 InChIKey、官能团角色、筛选范围、来源和零监督权重；
2. `结果/Gold_C_计算性能.csv.gz`：1,435,243 条计算输出或明确标注的计算上下文，含结构/过程体系族键、显式 `split_group`、性质、数值、单位状态、方法家族、记录角色、保真度、来源定位和准入状态；
3. `结果/Gold_E_实验表格.csv.gz`：305,108 条原始曲线点、实验目标、逐试样端点、配方/工艺观测或动力学值，含配方/样品身份、曲线与点索引、条件上下文、实验协议、单位状态、来源定位和准入状态。

这三张表仍是冻结参考层，结构参考也还没有全部形成完整TPU配方。为避免用户直接对参考长表逐行随机训练，当前另发布一个单层目录`结果/可用数据集/`：

1. `候选结构.csv.gz`：117,629条结构及候选用途，110,807条通过结构级排序门，其中8,365个构件通过线性TPU双官能、无竞争官能团门；
2. `计算观测.csv.gz`：保留1,435,243条计算记录，显式区分主监督、辅助监督、上下文和仅参考；1,378,201条通过相应任务门；
3. `实验观测.csv.gz`：保留305,108条实验记录，214,704条通过相应标量或曲线任务门；
4. `曲线索引.csv`：201条曲线一条一行，不把299,448个曲线点当作独立样本；
5. `任务清单.csv`、`来源与引用.csv`、`字段字典.csv`和`发布清单.json`：给出六类任务、引用、许可、字段、SHA-256和重建规则。

可用视图先把规范结构、模拟、曲线、样品、配方和既有分组做传递闭包，再生成固定开发划分和来源族五折。它已经适合建立第一版计算预训练、过程代理、实验曲线和实验标量基线；但纯“单体SMILES→宏观性能”仍必须等待结构—配方—工艺映射闭合。详细用法见[可用数据集说明](../结果/可用数据集/README.md)。
