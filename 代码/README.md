# 代码入口

当前 v0.2 的核心入口如下；v0.1 管道仅保留历史兼容：

```text
生成数据总账.py     复算当前多来源数据规模、Gold视图和压缩样本清单
run_pipeline.py     历史v0.1四源管道；不要用于重建v0.2权威总账
生成候选配方.py     从冻结 Gold-V 构件构建第二阶段虚拟组合与计量闭合配方
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
```
