# TPU 多保真 Gold 数据集定义

## 1. Gold 到底是什么

本项目的 Gold 不是只含实验数据的一张表，而是用于高通量筛选的多保真数据集合：

| 子集 | 内容 | 主要用途 |
|---|---|---|
| `Gold-E` | 真实合成、制样和测试得到的 TPU/TPUU 数据 | 性能标定、最终验证 |
| `Gold-C` | 可靠且可复现的 DFT、AIMD、MD、CGMD 数据 | 机理特征、预训练、低保真标签、复筛 |
| `Gold-V` | 规则生成或模型生成的可合成候选 | 扩大化学空间、主动学习、候选排序 |

三类数据都可以进入参考范围。准入不是“一缺字段就全部删除”，而是根据任务建立 `completeness_score`、`fidelity_score` 和任务适用标志。真正不能接受的是来源不明、体系身份不明、方法不明，或把模型预测伪装成实验真值。

这里把“进入 Gold 参考范围”和“作为某个任务的高权重真值”分开判断：

- 可复现、协议完整且收敛可信的计算，即使暂时没有一一对应的实验标定，也可以进入 `Gold-C`，用于机理描述、表示预训练、低保真标签和候选复筛；只有在把它宣称为宏观实验真值时才必须做跨尺度实验校准。
- 规则枚举、生成模型或已有模型给出的虚拟候选，不要求先有实验标签即可进入 `Gold-V`；它们可用于化学空间、排序和主动学习，但预测值不回灌成实验真值。
- 缺少某些非本任务字段不会整条删除。系统保留空值、适用域和不确定度，并在任务视图中降权或仅作参考。

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
gold_admission_status    admitted_reference / conditional_reference / blocked
```

`gold_admission_status` 与训练权重是两个维度：一条可追溯的虚拟候选可以正式进入 `Gold-V`，同时其直接实验性质监督权重仍为 0。当前机器总账已经在来源级和逐记录级写出这两个字段。

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

可靠计算数据可以进入 `Gold-C`。准入字段按“通用硬字段 + 方法条件字段 + 渐进补全字段”管理，不要求确定性 DFT 虚构随机种子，也不因尚无实验配对而删除协议完整的 MD。

通用硬字段是：计算体系身份、计算/运行 ID、软件与版本、方法或力场、输入—输出血缘、主要协议、质量或收敛状态，以及与候选/组分的映射类型。

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
- 直接有限元求解记录求解器、网格、材料参数、边界/载荷条件和收敛证据，归 `Gold-C`；机器学习代理输出归 `Gold-V`，不得当作直接数值求解或实验真值。
- 不确定度、实验映射和跨尺度校准是渐进补全字段；缺少时降低适用范围和上限，不自动取消计算参考准入。

可用计算指标包括：反应/解封能垒、结合能、氢键能、原子电荷、HOMO/LUMO、偶极矩、密度、内聚能密度、氢键数量与寿命、RDF、回转半径、相分离描述符、模拟 Tg 和模拟力学响应。

计算值可以作为低保真标签，但必须保留 `target_origin=dft/md` 和协议。高应变率 MD 强度、MD Tg 等不能在无标记情况下与准静态实验强度、DSC/DMA Tg 合并成同一真值。

### 2.8 虚拟候选字段

`Gold-V` 的硬准入字段是候选身份、输入组分结构、生成/枚举方法与版本、约束规则、来源血缘和适用域。派生候选必须指向父候选；根候选记录生成种子或输入集合。以下字段可渐进补全，暂缺时保留空值并降低排序置信度：

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

虚拟候选不必已有实验标签。它们用于生成候选空间和主动学习；经 DFT/MD 复筛或实验验证后，通过同一个 `candidate_id` 升级到 `Gold-C` 或 `Gold-E`。

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

最终权重在建模前通过来源留出和实验校准确定，现在只保存权重上限，不物化逐条训练权重。`Gold-C/Gold-V` 的参考准入不等于与 `Gold-E` 等权。

## 4. 最终交付的四个视图

为了让数据既清楚又能直接训练，最终只物化四个用户视图：

1. `Gold_配方.csv/parquet`：组分结构、SMILES、计量、工艺和材料状态。
2. `Gold_性能.csv/parquet`：实验、计算或预测性能及测试/计算条件。
3. `Gold_曲线.parquet`：每条曲线的元数据；点存长表，不把点数当样本数。
4. `Gold_候选.csv/parquet`：可合成虚拟候选、计算复筛结果和排序状态。

底层数据库可以保留更多规范表，但论文分析和日常筛选只面向这四个视图。
