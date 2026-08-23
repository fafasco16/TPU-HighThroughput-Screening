# 第二阶段预审与 DFT/MD 复核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 9,216 条计量闭合 TPU 虚拟配方转换为可审计的人工预审表，并生成 48 条第一层 DFT 复核队列。

**Architecture:** 只读取 `候选/` 的冻结构件、组合和配方发布，另行生成预审视图、DFT/MD 队列与哈希清单。结构规则仅产生“需要 SDS/EHS 复核”的警示；供应状态和文献新颖性保持未核验。第一层只安排小分子构件 DFT，真实宏二醇身份与 Mn/Mw/PDI 闭合前不启动块体 MD。

**Tech Stack:** Python 3.11、Pandas、RDKit、PyYAML、pytest。

---

## 文件职责

- `配置/候选预审.yaml`：固定人工状态、DFT 队列规模和筛选格点。
- `代码/候选预审.py`：结构警示、配方预审、组合指纹和 max-min 队列选择纯函数。
- `代码/生成候选预审.py`：生成发布文件、记录 SHA-256，并提供只读 `--检查`。
- `代码/测试/test_候选预审.py`：测试解释边界、确定性、去重、行数和哈希。
- `候选/候选预审.csv.gz`：9,216 条配方的一行一条预审状态。
- `候选/DFT_MD复核队列.csv`：48 条 DFT Tier-1 队列与明确的 MD 暂停状态。
- `候选/候选预审发布清单.json`：输入、配置、输出哈希和数量。
- `文档/DFT_MD复核协议.md`：计算对象、层级、验收字段、禁止性解释与参考文献。

### Task 1: 固定配置和状态边界

**Files:**
- Create: `配置/候选预审.yaml`
- Create: `代码/测试/test_候选预审.py`

- [ ] **Step 1: Write the failing test**

```python
def test_config_keeps_external_claims_manual():
    config = orchestrator.load_config(ROOT / "配置" / "候选预审.yaml")
    assert config["dft_queue"]["queue_size"] == 48
    assert config["manual_review"]["procurement_status"] == "not_checked"
    assert config["manual_review"]["literature_novelty_status"] == "not_checked"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest 代码\测试\test_候选预审.py::test_config_keeps_external_claims_manual -q`

Expected: FAIL，因为配置和生成模块尚不存在。

- [ ] **Step 3: Write minimal implementation**

```yaml
release_id: tpu-candidate-precheck-2026-08-23-v1
manual_review:
  procurement_status: not_checked
  ehs_status: requires_sds_and_local_EHS_review
  literature_novelty_status: not_checked
dft_queue:
  queue_size: 48
  macrodiol_nominal_mn_g_mol: 2000.0
  hard_segment_mass_fraction_target: 0.45
  nco_oh_ratio_target: 1.02
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest 代码\测试\test_候选预审.py::test_config_keeps_external_claims_manual -q`

Expected: PASS。

### Task 2: 实现结构警示和整表预审

**Files:**
- Create: `代码/候选预审.py`
- Modify: `代码/测试/test_候选预审.py`

- [ ] **Step 1: Write the failing test**

```python
def test_structure_alerts_are_review_flags_not_hazard_classes():
    alerts = precheck.structure_alerts("O=C=Nc1ccccc1N=C=O", "diisocyanate")
    assert "isocyanate_group_requires_SDS_review" in alerts
    assert "aromatic_structure_requires_exposure_review" in alerts
    assert all("hazard_class" not in alert for alert in alerts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest 代码\测试\test_候选预审.py::test_structure_alerts_are_review_flags_not_hazard_classes -q`

Expected: FAIL，因为 `structure_alerts` 尚不存在。

- [ ] **Step 3: Write minimal implementation**

`structure_alerts` 仅检查 NCO、芳香性和卤素结构信号；`annotate_formulations` 为全部 9,216 行写入 `procurement_status=not_checked`、`literature_novelty_status=not_checked`、`ehs_status=requires_sds_and_local_EHS_review`、`experimental_eligibility=blocked_pending_manual_review`，同时连接三个构件的警示，不生成危险等级或供应商信息。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest 代码\测试\test_候选预审.py -q`

Expected: PASS。

### Task 3: 生成 48 条多样性 DFT 队列

**Files:**
- Modify: `代码/候选预审.py`
- Modify: `代码/测试/test_候选预审.py`

- [ ] **Step 1: Write the failing test**

```python
def test_dft_queue_is_unique_and_holds_md():
    queue = precheck.select_dft_queue(frame, queue_size=2)
    assert queue["formulation_id"].is_unique
    assert queue["dft_stage"].eq("tier1_monomer_reactivity_and_conformer_screen").all()
    assert queue["md_stage"].eq("on_hold_pending_real_macrodiol_identity_Mn_Mw_PDI").all()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest 代码\测试\test_候选预审.py::test_dft_queue_is_unique_and_holds_md -q`

Expected: FAIL，因为队列选择器尚不存在。

- [ ] **Step 3: Write minimal implementation**

固定选择 `Mn=2000`、硬段质量分数 `0.45`、`NCO/OH=1.02` 的 1,152 行；把三种构件的 512-bit Morgan 指纹逐位 OR，使用 Tanimoto max-min 选择 48 条，并列按 `formulation_id` 升序。输出固定标记 `dft_protocol_id=TPU-DFT-T1-v1`、`performance_claim_status=no_performance_claim` 和 MD 暂停原因。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest 代码\测试\test_候选预审.py -q`

Expected: PASS，且队列恰有 48 个唯一 `formulation_id`。

### Task 4: 发布、文档、核验与 GitHub 推送

**Files:**
- Create: `代码/生成候选预审.py`
- Create: `候选/候选预审.csv.gz`
- Create: `候选/DFT_MD复核队列.csv`
- Create: `候选/候选预审发布清单.json`
- Create: `文档/DFT_MD复核协议.md`
- Modify: `候选/README.md`
- Modify: `README.md`
- Modify: `代码/README.md`
- Modify: `文档/当前数据状态.md`

- [ ] **Step 1: Write the failing test**

```python
def test_release_has_expected_rows_and_hashes():
    manifest = json.loads((ROOT / "候选" / "候选预审发布清单.json").read_text(encoding="utf-8"))
    assert manifest["counts"] == {"precheck_rows": 9216, "dft_queue_rows": 48}
    orchestrator.verify(ROOT / "候选")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest 代码\测试\test_候选预审.py::test_release_has_expected_rows_and_hashes -q`

Expected: FAIL，因为发布文件尚未生成。

- [ ] **Step 3: Write minimal implementation**

生成器只写上述三个发布文件；清单固定三份候选输入、配置和两份 CSV 的 SHA-256。`--检查` 不写文件，校验哈希、9,216 个唯一配方、48 条唯一队列、未核供应/新颖性状态以及全部 MD 暂停状态。

- [ ] **Step 4: Write method documentation**

协议明确 Tier 1 只做构件构象、几何、频率和反应性描述，不直接预测块体 TPU 强度或韧性；宏二醇真实身份、Mn/Mw/PDI、链长、力场、温压、时间尺度和收敛标准闭合后才进入 MD。引用现有文献表中的 SMiPoly [7]、PolyUniverse [135]–[137]、RadonPy [145]–[146] 和 PolyOmics [151]。

- [ ] **Step 5: Run full verification**

Run:

```powershell
uv run python 代码\生成候选预审.py
uv run python 代码\生成候选预审.py --检查
uv run pytest 代码\测试\test_候选预审.py 代码\测试\test_候选配方.py 代码\测试\test_可用数据集.py -q
```

Expected: 全部通过；预审 9,216 行，DFT 队列 48 行。

- [ ] **Step 6: Commit and push**

```powershell
git add README.md 代码 配置 候选 文档
git commit -m "feat: 发布候选预审与DFT复核队列"
git push origin main
```

提交时必须排除用户未跟踪的 `文档/项目总览.md`。

## 自检

- 需求覆盖：预审、人工核验状态、DFT 队列、MD 暂停门、复现、引用和 GitHub 使用均有明确任务。
- 无占位符：文件、字段、状态、数量、函数和命令均已固定。
- 类型一致：后续任务统一使用 `structure_alerts`、`annotate_formulations`、`select_dft_queue` 与 `verify`。
