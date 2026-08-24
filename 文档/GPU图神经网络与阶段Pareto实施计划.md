# GPU图神经网络与阶段Pareto Implementation Plan

> 本计划的完成状态以测试、运行清单和输出哈希为准；阶段结果与最终结果严格分名。

**Goal:** 在不等待最后两个CREST构件的情况下，用40条已闭合配方生成阶段性Pareto/聚类结果，并在L40上训练严格防泄漏的图神经网络计算性质基线。

**Architecture:** Pareto分支只消费阶段性82构件系综表，明确标为非最终排名；GNN分支沿用`结果/可用数据集/计算观测.csv.gz`中的`leakage_group`、`development_split`、`usage_mode`和`recommended_loss_weight`，与现有Morgan-Ridge使用同一目标和同一测试集。模型只在训练折拟合，验证折选超参数，测试折一次评估；来源族留出结果必须单独报告。

**Tech Stack:** Python 3.12、Pandas、RDKit、PyTorch CUDA、NVIDIA L40。

## 2026-08-24执行状态

- 阶段Pareto：Ubuntu实算完成；48条配方保留，40条ready、8条closed、25条第一前沿、8个多样性簇，输出已回填`计算/阶段筛选/`。
- 图数据：26,176条观测图、7,558个泄漏硬组；train/validation/test为20,995/2,648/2,533，同组跨折数为0。
- GPU门禁：PyTorch 2.13.0+cu130和NVIDIA L40烟雾测试通过；数据、模型、训练联合测试52项通过。
- 正式训练：`primary_only`与`primary_plus_aux`已在Ubuntu后台启动；结果尚未完成前不得宣称GNN优于Ridge。
- 最终配方版：仍等待Slurm Job 8307最后两个宏二醇代理CREST完成；完整构件到齐后必须重算。

---

### Task 1: 阶段性配方特征、聚类与Pareto

**Files:**
- Create: `代码/生成阶段配方筛选.py`
- Create: `代码/测试/test_生成阶段配方筛选.py`
- Create: `计算/阶段筛选/阶段配方特征.csv`
- Create: `计算/阶段筛选/阶段Pareto候选.csv`
- Create: `计算/阶段筛选/阶段筛选报告.md`

- [ ] **Step 1: Write the failing test**

```python
def test_stage_join_keeps_48_rows_and_closes_missing_components():
    result = build_stage_features(formulations, components)
    assert len(result) == 48
    assert result["formulation_id"].is_unique
    assert set(result["descriptor_join_status"]) <= {"ready", "blocked", "missing_component_descriptor"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest 代码\测试\test_生成阶段配方筛选.py -q`

Expected: FAIL，因为生成器尚不存在。

- [ ] **Step 3: Write minimal implementation**

使用`配方系综特征.aggregate_formulation_features`连接三个角色，标准化数值特征后做确定性距离聚类；Pareto目标方向必须显式配置，不生成总分。输出保留`stage_only_not_final`、缺失/blocked原因和源构件ID。

- [ ] **Step 4: Run test to verify it passes**

Expected: 40条阶段ready、6条等待最后两个宏二醇代理、2条输入阻断；Pareto只在ready子集计算。

### Task 2: GNN数据图与严格划分

**Files:**
- Create: `代码/GNN数据.py`
- Create: `代码/测试/test_GNN数据.py`

- [ ] **Step 1: Write the failing test**

```python
def test_same_leakage_group_never_crosses_split():
    dataset = build_graph_dataset(frame, targets)
    assert dataset.groupby("leakage_group")["development_split"].nunique().max() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest 代码\测试\test_GNN数据.py -q`

Expected: FAIL，因为图数据模块尚不存在。

- [ ] **Step 3: Write minimal implementation**

RDKit图节点包含原子序数、价态、电荷、芳香性、杂化和环信息；边包含键型、共轭和环信息。任务固定为`Rg`、`density`、`bulk_modulus`、`thermal_conductivity`，`Tg`继续仅探索。重复观测沿用发布权重，不逐行随机拆分。

- [ ] **Step 4: Run test to verify it passes**

Expected: 非法结构显式报告；同一`leakage_group`不跨折；训练/验证/测试只使用发布划分。

### Task 3: 轻量消息传递模型与加权训练

**Files:**
- Create: `代码/GNN模型.py`
- Create: `代码/训练GNN基线.py`
- Create: `代码/测试/test_GNN模型.py`

- [ ] **Step 1: Write the failing test**

```python
def test_masked_weighted_multitask_loss_ignores_missing_targets():
    loss = masked_weighted_mse(prediction, target, mask, weight)
    assert torch.isfinite(loss)
    assert loss.item() == pytest.approx(expected)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest 代码\测试\test_GNN模型.py -q`

Expected: FAIL，因为模型尚不存在。

- [ ] **Step 3: Write minimal implementation**

实现不依赖PyG的消息传递网络，使用`index_add_`聚合、全局均值/和池化、四任务独立头。训练仅用train，validation早停与选择，test一次；固定随机种子并保存模型配置、权重SHA和逐样本预测。

- [ ] **Step 4: Run test to verify it passes**

Expected: CPU/GPU前向一致到容差；缺失目标不进损失；单批过拟合夹具通过；模块覆盖率至少90%。

### Task 4: Ubuntu L40训练、严格比较与发布

**Files:**
- Create: `模型/GNN配置.yaml`
- Create: `模型/GNN结果/指标.csv`
- Create: `模型/GNN结果/逐样本预测.csv.gz`
- Create: `模型/GNN结果/GNN报告.md`
- Create: `模型/GNN结果/运行清单.json`

- [ ] **Step 1: Audit GPU environment**

安装项目专用PyTorch CUDA环境，记录wheel来源、版本、CUDA、cuDNN、GPU、驱动及哈希；先执行矩阵和小图烟雾测试。

- [ ] **Step 2: Train development baseline**

训练`primary_only`与`primary_plus_aux`两种模式；验证集早停；同时输出加权逐行和硬组宏平均指标。

- [ ] **Step 3: Evaluate strict holdout**

同现有Ridge测试集比较RMSE、R²和Spearman；可估计时执行完整来源族留出。不得因结果较差改拆分、删除测试点或只报告较优任务。

- [ ] **Step 4: Publish or reject**

只有严格测试与来源留出稳定改善时才作为候选排序辅助；否则报告失败并保留Ridge。运行专项测试、发布哈希核验并推送私人GitHub。

## 自检

- 阶段Pareto与最终Pareto严格分名；没有把40条阶段结果称为最终候选。
- GNN与Ridge使用相同发布拆分、权重和测试目标，可公平比较。
- GPU只用于训练；不把模型预测回灌Gold-E或Gold-C真值。
- MD仍由真实宏二醇身份与Mn/Mw/PDI门控制。
