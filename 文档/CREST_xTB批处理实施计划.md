# CREST/xTB 批处理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 48 条 DFT 复核配方去重成 86 个唯一构件，在 Slurm 服务器上生成可断点续算的 CREST/GFN2-xTB 构象筛选任务，并输出可回填数据库的状态与结果表。

**Architecture:** 本地仓库负责从已发布队列确定性生成任务清单和三维初始结构；服务器沿用仓库的 `代码/候选/计算` 浅层目录，轨迹与日志放在 `计算/结果` 和 `计算/日志`。集群按整节点而非CPU粒度分配，故只提交一个128 CPU作业：先在同一节点运行2个烟雾任务，通过后32路并行执行构件任务，每路4 CPU。Python包装器记录输入/输出哈希、版本、退出码和完成状态。ORCA/r2SCAN-3c 未安装，因此只启动Tier-1a，Tier-1b保持软件门状态。

**Tech Stack:** Python 3.11、Pandas、RDKit、CREST 3.0.2、xTB 6.7.1、Slurm。

---

### Task 1: 唯一构件清单与初始三维结构

**Files:**
- Create: `代码/DFT任务.py`
- Create: `代码/生成DFT任务.py`
- Create: `代码/测试/test_DFT任务.py`
- Create: `计算/DFT任务清单.csv`
- Create: `计算/初始结构/*.xyz`
- Create: `计算/DFT任务发布清单.json`

- [ ] **Step 1: Write the failing test**

```python
def test_queue_deduplicates_to_unique_components():
    tasks = dft.build_component_tasks(queue)
    assert tasks["candidate_id"].is_unique
    assert set(tasks["component_role"]) == {
        "diisocyanate", "macrodiol_proxy", "chain_extender"
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest 代码\测试\test_DFT任务.py -q`

Expected: FAIL，因为 `DFT任务.py` 尚不存在。

- [ ] **Step 3: Write minimal implementation**

`build_component_tasks` 按三个构件 ID 去重，保存全部关联 `formulation_id`；`generate_initial_xyz` 用 ETKDGv3 生成最多 10 个构象，优先 MMFF94s、回退 UFF，选择最低力场能构象。任务索引按 `component_role,candidate_id` 固定排序，输出 `0000_candidate_id.xyz`。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest 代码\测试\test_DFT任务.py -q`

Expected: PASS；正式队列保留 86 个唯一构件，只有通过三维结构门者生成有效 XYZ，失败者显式阻断。

### Task 2: 单任务包装器与断点续算

**Files:**
- Create: `代码/运行CREST任务.py`
- Modify: `代码/测试/test_DFT任务.py`

- [ ] **Step 1: Write the failing test**

```python
def test_completed_task_is_skipped_when_hash_matches(tmp_path):
    state = runner.completed_state(input_sha256="abc")
    assert runner.should_skip(state, input_sha256="abc") is True
    assert runner.should_skip(state, input_sha256="changed") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest 代码\测试\test_DFT任务.py::test_completed_task_is_skipped_when_hash_matches -q`

Expected: FAIL，因为运行包装器尚不存在。

- [ ] **Step 3: Write minimal implementation**

包装器读取任务索引，复制初始 XYZ 到 `结果/<任务标识>/`，执行 `crest input.xyz --gfn2 --chrg 0 --uhf 0 --T <cpus>`，把 stdout/stderr 写入 `crest.out`，并以原子方式写 `运行状态.json`。只有退出码为 0、`crest_conformers.xyz` 存在且输入哈希一致才可跳过。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest 代码\测试\test_DFT任务.py -q`

Expected: PASS。

### Task 3: Slurm 数组与结果汇总

**Files:**
- Create: `计算/运行CREST数组.sh`
- Create: `代码/汇总CREST结果.py`
- Modify: `代码/测试/test_DFT任务.py`

- [ ] **Step 1: Write the failing test**

```python
def test_summary_distinguishes_completed_failed_and_pending(tmp_path):
    summary = collector.collect_status(tasks, tmp_path)
    assert set(summary["status"]) == {"completed", "failed", "pending"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest 代码\测试\test_DFT任务.py::test_summary_distinguishes_completed_failed_and_pending -q`

Expected: FAIL，因为汇总器尚不存在。

- [ ] **Step 3: Write minimal implementation**

Slurm 固定 `--partition=192c`、`--cpus-per-task=128`、`--time=1-00:00:00`，激活 `/home/zhanhao/software/quantum-cpu`。脚本先并行运行索引0和44，通过后由`xargs -P 32`调度全部索引，每路4线程。集群将节点 `RealMemory` 错配为1 MiB，故不能声明常规 `--mem`，实际内存通过节点 `FreeMem` 监控。汇总器读取每个 `运行状态.json`，输出 `计算/CREST运行汇总.csv`，并保留失败原因、运行时间、版本和哈希。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest 代码\测试\test_DFT任务.py -q`

Expected: PASS，新模块覆盖率至少 90%。

### Task 4: 服务器部署、烟雾测试与全量提交

**Files:**
- Create remote: `/home/zhanhao/TPU高通量筛选/`
- Create remote: `代码/`, `候选/`, `计算/初始结构`, `计算/结果`, `计算/日志`
- Modify: `计算/服务器环境.md`

- [ ] **Step 1: Deploy frozen bundle**

Run: `scp -P 20008 ... zhanhao@172.20.35.201:/home/zhanhao/TPU高通量筛选/`

Expected: 任务清单、86 个 XYZ、Python 包装器和 Slurm 脚本到位，本地/远端 SHA-256 一致。

- [ ] **Step 2: Run two-task smoke array**

Run: `sbatch 计算/运行CREST数组.sh`

Expected: 单节点作业先完成索引0和44；只有二者都生成 `crest_conformers.xyz` 和 `运行状态.json`，脚本才进入全量并发阶段。

- [ ] **Step 3: Submit full resumable array**

Run: 由同一作业自动继续，无需第二次提交。

Expected: 全部索引 `0-85` 以32路并发运行；已完成烟雾任务因哈希一致自动跳过，2个三维结构受阻任务写状态后退出。

- [ ] **Step 4: Record job IDs and environment**

`计算/服务器环境.md` 记录主机名、Slurm 分区、CPU/内存、软件版本、远端根目录、烟雾和全量 Job ID。不得记录密码。

### Task 5: 核验、文档与推送

**Files:**
- Modify: `README.md`
- Modify: `计算/README.md`
- Modify: `文档/当前数据状态.md`

- [ ] **Step 1: Run verification**

Run:

```powershell
uv run python 代码\生成DFT任务.py --检查
uv run pytest 代码\测试\test_DFT任务.py 代码\测试\test_候选预审.py -q
```

Expected: 全部通过；任务数 86，XYZ 数 86，发布哈希一致。

- [ ] **Step 2: Commit and push**

```powershell
git add README.md 代码 计算 文档/当前数据状态.md 文档/CREST_xTB批处理实施计划.md
git commit -m "feat: 部署TPU构件CREST批处理"
git push origin main
```

必须排除用户未跟踪的 `文档/项目总览.md`，并保留现有 stash。

## 自检

- 覆盖了任务生成、三维结构、断点续算、调度、汇总、远端部署、烟雾测试和全量提交。
- 所有路径、字段、资源、并发和完成门均已固定，没有未定义实现。
- 只提交 CREST/GFN2-xTB Tier-1a；ORCA/r2SCAN-3c 不可用的事实记录为软件门，不用其他方法冒充。
