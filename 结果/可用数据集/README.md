# TPU 可用数据集 v1

- 版本：`tpu-usable-2026-07-21-v1`
- 用途：现有 Gold-V/C/E 的任务化训练、计算复筛和候选排序
- 状态：数据视图已经可用；**尚未训练或选择最终模型**

这一目录只有一层。原始 Gold 表保持只读、权重保持为空；这里单独物化任务、硬分组、固定划分和推荐权重，避免破坏来源事实。

## 1. 现在可以直接使用什么

| 文件 | 行数 | 用途 |
|---|---:|---|
| `候选结构.csv.gz` | 117,629 | 构件、虚拟重复单元和迁移候选；其中110,807条通过结构级排序门 |
| `计算观测.csv.gz` | 1,435,243 | DFT、GFN2-xTB、TDDFT、MD/NEMD、CFD/PBE、FEA等计算长表 |
| `实验观测.csv.gz` | 305,108 | 实验标量、曲线点、配方/工艺上下文和表征数据 |
| `曲线索引.csv` | 201 | 一条曲线一行的索引、范围、划分和整条曲线权重 |
| `任务清单.csv` | 6 | 各任务的就绪行数、独立单元、性质数、来源数及限制 |
| `来源与引用.csv` | 27 | 当前视图实际使用的来源ID、来源族、DOI/URL、许可证和引用键 |
| `字段字典.csv` | — | 每个发布文件的字段、类型、可空性和中文定义 |
| `发布清单.json` | — | 输入/输出SHA-256、版本、数量和权重策略 |

关键的可训练规模不是“总行数”：

- 计算任务：1,378,201条任务级可用记录，其中132,813条为主监督、其余为低权重计算/迁移辅助；对应55,760个硬分组。
- 实验曲线：211,969个可用点，但只有181条可用独立曲线；一条曲线的所有点必须同折。
- 实验标量：2,735条任务级可用记录、457个独立单元、114种性质。很多性质仍是小样本任务，不能训练一个无条件“大一统”回归器。
- 线性TPU主路线的干净构件池：8,237个二异氰酸酯、78个二醇扩链剂、50个宏二醇，共8,365个构件。理论三组分组合为32,124,300种，因此下一步应筛选，不再扩库。

“任务级可用”表示该行可在它标明的任务中作为主监督或辅助监督，不表示它是新的独立材料，也不表示模拟值等同于实验真值。

## 2. 六个任务怎么分

| `task_id` | 正确用途 | 不允许的解释 |
|---|---|---|
| `候选_结构排序` | 规则过滤、聚类、多目标排序、主动学习 | 候选优先级不是性能标签或合成证明 |
| `计算_结构多任务预训练` | 学习聚合物结构表示和多性质低保真映射 | 通用聚合物计算不能冒充TPU实验强度 |
| `计算_过程代理模型` | 在已知体系内学习工况到响应 | 同一配方的数千工况不是数千种新材料 |
| `实验_曲线建模` | 序列模型、函数回归、端点提取 | 不得对曲线点逐行随机切分 |
| `实验_标量校准` | 最终实验校准、多保真残差学习 | 未闭合配方不能用于“仅凭SMILES预测性能” |
| `上下文_仅作输入` | 配方、工艺、几何和模拟条件 | 目标损失权重恒为0 |

`target_role`进一步区分直接标量、曲线、文献汇总、变换目标、表征辅助和输入特征。

## 3. 使用状态和权重

`usage_mode`只有四种：

- `primary_train`：正式参考且通过具体任务门，可作主监督；
- `auxiliary_train`：来源可靠、值和单位可用，但属于模拟、迁移域或条件参考，只作低权重辅助；
- `context_only`：配方、几何、工艺或模拟输入，只能作特征；
- `reference_only`：仍保留溯源，但当前任务权重为0。

损失权重为：

```text
recommended_loss_weight =
potential_weight_ceiling
× quality_factor
× role_factor
× independence_weight
```

其中同一曲线的点、同一模拟同一性质的重复值、同一样品同一性质的重复值共享一个总预算。`source_balanced_sampling_probability`再使每个任务中的有效来源具有相同总抽样概率，避免OMG、PolyOmics或高密度曲线仅凭行数主导训练。

模型训练时二选一即可：

1. 普通批采样，损失函数使用`recommended_loss_weight`；
2. 按`source_balanced_sampling_probability`抽样，损失仍使用`recommended_loss_weight`，但不要再额外做一次来源逆频率加权。

## 4. 防泄漏划分

生成器先把以下关系做传递闭包：现有`split_group`、规范结构族、`simulation_key`、`curve_id`、`sample_id`、`formulation_id`和来源内材料族。闭包结果写入`leakage_group`。

- `development_split`：按`leakage_group`固定哈希为80%/10%/10%的`train/validation/test`，适合开发阶段；
- `source_holdout_fold`：按`source_family_id`固定为0–4，适合论文中的严格新来源/新材料族外推评估。

任何筛选都应先采用已发布划分，再按性质过滤。不要为每个性质重新逐行随机切分。某些过程模拟只有一个材料体系，所以开发划分会故意不均衡；这反映真实外推边界，不应通过打散工况来“修正”。

## 5. 最短使用方法

### 5.1 读取可用实验标量

```python
import pandas as pd

path = "结果/可用数据集/实验观测.csv.gz"
columns = [
    "observation_id", "source_id", "sample_id", "formulation_id",
    "property_name", "value", "unit", "development_split",
    "usage_mode", "recommended_loss_weight",
]

parts = []
for chunk in pd.read_csv(path, usecols=columns, chunksize=100_000):
    selected = chunk[
        (chunk["development_split"] == "train")
        & chunk["usage_mode"].isin(["primary_train", "auxiliary_train"])
    ]
    parts.append(selected)
train_long = pd.concat(parts, ignore_index=True)

# 建议一次只做一个性质或一个明确的多任务集合
tensile = train_long[train_long["property_name"] == "tensile_strength"]
y = tensile["value"]
sample_weight = tensile["recommended_loss_weight"]
```

实验表目前没有为每一行闭合单体SMILES。要训练“结构→宏观性能”，必须先通过`formulation_id/sample_id`连接组分结构、配方和工艺；不能把空结构补成假SMILES。

### 5.2 读取计算多任务预训练集

```python
import duckdb

computed = duckdb.sql("""
    SELECT canonical_structure, property_name, value, unit,
           method_family, temp, press, development_split,
           usage_mode, recommended_loss_weight,
           source_balanced_sampling_probability
    FROM read_csv_auto('结果/可用数据集/计算观测.csv.gz')
    WHERE task_id = '计算_结构多任务预训练'
      AND model_ready = true
      AND development_split = 'train'
""").df()
```

建议把`property_name`作为任务头，把`method_family/temp/press`作为条件；实验微调时保留`target_origin`和保真度，采用残差校准或多保真模型，不直接合并同名但协议不同的值。

### 5.3 获取线性TPU构件池

```python
import pandas as pd

candidates = pd.read_csv("结果/可用数据集/候选结构.csv.gz")
linear = candidates[candidates["linear_tpu_building_block_ready"]]

diisocyanates = linear[linear["linear_component_class"] == "diisocyanate"]
chain_extenders = linear[linear["linear_component_class"] == "chain_extender_diol"]
macrodiols = linear[linear["linear_component_class"] == "macrodiol"]
```

这里的“干净”只表示中性、单组分、双官能且未检测到竞争反应官能团。下一步仍要加入商业可得性、EHS、反应活性、相容性、分子量和合成路线门，再由DFT/MD/代理模型逐级缩小候选，不能直接把三组分做笛卡尔积送实验。

### 5.4 按整条曲线读取

```python
import pandas as pd

curve_index = pd.read_csv("结果/可用数据集/曲线索引.csv")
train_ids = set(
    curve_index.loc[
        curve_index["development_split"].eq("train")
        & curve_index["model_ready"],
        "curve_id",
    ]
)

curves = pd.read_csv("结果/可用数据集/实验观测.csv.gz")
curves = curves[curves["curve_id"].isin(train_ids)]
for curve_id, points in curves.groupby("curve_id", sort=False):
    points = points.sort_values("point_index")
    # 一整个points才是一条序列样本
```

## 6. 引用和许可

论文引用按实际使用子集生成，不应笼统引用整个数据库：

```python
used_source_ids = set(train_long["source_id"])
references = pd.read_csv("结果/可用数据集/来源与引用.csv")
used_references = references[references["source_id"].isin(used_source_ids)]
print(used_references[["canonical_identifier", "citation_keys", "license_status"]])
```

`citation_keys`连接到项目的[数据来源与参考文献](../../文档/数据来源与参考文献.md)，其中按正常论文参考文献格式记录作者、题名、期刊/仓库、年份、DOI和数据版本。方法、数据集与论文应分别引用；同一来源族的正文、SI和处理视图只算一个独立来源贡献。

私人GitHub仓库不改变许可证。`CC BY-NC`、`manual_review`、许可缺失或禁止再分发的来源必须按`license_status`隔离；公开论文或模型前，应再次按实际训练子集审核许可。

## 7. 重建和核验

```powershell
uv run python 代码\生成可用数据集.py
uv run python 代码\生成可用数据集.py --检查
```

`发布清单.json`记录四个冻结输入和全部输出的SHA-256。相同输入、脚本和版本应生成相同字节；单文件设有95 MiB门禁，避免触发GitHub 100 MB限制。

最终建模前请先阅读[Gold数据集定义](../../文档/Gold数据集定义.md)和[当前数据状态](../../文档/当前数据状态.md)。核心警示只有三句：**行数不等于材料数；模拟不等于实验；虚拟候选不等于已可合成。**
