# 代码入口

当前 v0.2 的核心入口如下；v0.1 管道仅保留历史兼容：

```text
生成数据总账.py     复算当前多来源数据规模、Gold视图和压缩样本清单
run_pipeline.py     历史v0.1四源管道；不要用于重建v0.2权威总账
生成候选配方.py     从冻结 Gold-V 构件构建第二阶段虚拟组合与计量闭合配方
生成候选预审.py     标记人工复核状态并生成48条结构多样性DFT Tier-1队列
生成DFT任务.py      将48条队列去重为构件任务并生成确定性三维初始结构
运行CREST任务.py    服务器端单构件断点续算、哈希和失败状态包装器
汇总CREST结果.py    汇总completed/failed/pending/blocked状态，不填补失败值
CREST系综分析.py    解析多帧构象、Boltzmann权重、构象熵和低能窗口
发布CREST结果.py    终态与哈希核验后生成确定性精简结果包
配方系综特征.py     严格连接三类构件描述符并生成无总分Pareto输入
xTB系综任务.py      拆分CREST构象并生成稳定ID、单帧哈希和任务清单
运行xTB构象任务.py  xTB 6.7.1单点、锁、断点续算和分片结果包
运行xTB批次.py      常驻worker分片调度三万级构象，避免重复加载任务表
xTB输出解析.py      严格解析JSON/WBO/极化率并做完整系综聚合
反应位点描述符.py   计算NCO/OH位点SASA、净间隙与双位点差异
汇总xTB结果包.py    安全流式核验分片包并生成构象/构件/失败三表
运行现实MD多链烟雾.py 以最小现实低聚链验证RadonPy装箱、GAFF2导出、LAMMPS最小化/NVT执行链
审计现实MD生产参数门.py 展开GAFF2替代映射，连接氨基甲酸酯计数并合并RESP运行证据
运行RESP小片段烟雾.py 用Psi4/RESP对含氨基甲酸酯小片段执行可复现两阶段电荷烟雾
汇总RESP片段验证.py 严格核验四类关键片段的逐文件哈希、电荷和与家族覆盖
运行RESP敏感性批次.py 并行运行4家族×3构象种子×3点密度的36项RESP矩阵
汇总RESP敏感性.py 核验原始任务并发布紧凑构象/点密度统计与服务器归档哈希
运行RESP多构象联合.py 对同一片段三个构象等权共同拟合一组RESP电荷
汇总RESP联合多构象.py 比较联合电荷与独立拟合均值并发布四家族紧凑结果
验证RESP核心转移.py 将四家族联合RESP核心严格映射到12条现实TPU低聚链
运行氨基甲酸酯刚性扫描.py 在同一刚性几何比较ωB97M-D3BJ与GAFF2势能曲线
汇总氨基甲酸酯刚性扫描.py 聚合48点曲线、筛查参数失败并选择松弛复算角度
运行氨基甲酸酯受约束松弛.py 用OptKing冻结目标二面角并松弛其余自由度
```

注意：`run_pipeline.py manifest` 会扫描整个本地 `数据/原始`，只用于显式重建历史兼容清单；不要把它当作 v0.2 日常入口。v0.2 的权威产物是 `结果/数据规模总账.csv`、`结果/样本清单.csv.gz`、`结果/Gold_V_候选.csv.gz`、`结果/Gold_C_计算性能.csv.gz` 和 `结果/Gold_E_实验表格.csv.gz`。

其余文件分为三类：

- 根目录模块：ID、单位、许可、QC、数据库构建等公共逻辑；
- `审计/` 与 `获取/`：逐来源的可复现接入工具，平时无需逐个运行；
- `测试/`：自动校验路径、结构、计数、许可和确定性构建。

先运行最小测试：

```powershell
.\.venv\Scripts\python.exe -m pytest 代码\测试\test_layout.py 代码\测试\test_trainable_inventory.py -q
```

第二阶段候选空间不改写 Gold 数据，生成和核验命令为：

```powershell
uv run python 代码\生成候选配方.py
uv run python 代码\生成候选配方.py --检查
uv run python 代码\生成候选预审.py
uv run python 代码\生成候选预审.py --检查
uv run python 代码\生成DFT任务.py
uv run python 代码\生成DFT任务.py --检查
```

CREST全部终态后，在服务器生成最终发布与构件系综表：

```bash
python 代码/发布CREST结果.py \
  --任务清单 计算/DFT任务清单.csv \
  --结果目录 计算/结果 \
  --输出目录 计算/发布

python 代码/CREST系综分析.py \
  --任务清单 计算/DFT任务清单.csv \
  --结果目录 计算/结果 \
  --输出 计算/CREST构件系综汇总.csv \
  --温度 298.15
```

现实MD多链烟雾只在固定的Ubuntu RadonPy/LAMMPS环境执行，输出目录必须为空，且LAMMPS内部文件名保持ASCII：

```bash
source /opt/tpu-md-venv/bin/activate
python 代码/运行现实MD多链烟雾.py \
  --化学图 计算/现实MD/低聚链化学图.csv.gz \
  --输出目录 计算/现实MD/LAMMPS烟雾_多链_尝试3 \
  --链数 2 --初始密度 0.20 \
  --最小化最大迭代 20000 --最小化最大评估 200000 \
  --NVT步数 2000 --时间步长fs 0.5 --温度K 300
```

该命令只验证执行链。`status=completed_multichain_smoke_production_md_blocked`不等于生产MD许可，也不能生成密度、Tg、力学或相分离性能标签。
