# 代码入口

日常只需要关注两个入口：

```text
run_pipeline.py     构建 v0.1 规范数据库与快照
生成数据总账.py     复算当前多来源数据规模和样本清单
```

其余文件分为三类：

- 根目录模块：ID、单位、许可、QC、数据库构建等公共逻辑；
- `审计/` 与 `获取/`：逐来源的可复现接入工具，平时无需逐个运行；
- `测试/`：自动校验路径、结构、计数、许可和确定性构建。

先运行最小测试：

```powershell
.\.venv\Scripts\python.exe -m pytest 代码\测试\test_layout.py 代码\测试\test_trainable_inventory.py -q
```
