# 来源级可复现审计

本目录保存新增开放数据的来源级审计程序。它们用于复算文件哈希、容器完整性、
可解析记录、科学计数单位和准入边界，不创建训练集，也不把曲线点、模拟帧或重复
导出解释为独立材料样本。

当前集中归档的脚本覆盖新增开放数据层 23 个来源中的 14 个。其余 9 个来源已有的
审计结果、来源目录内工具或人工提取过程不能因此被表述为已统一“一键复算”；冻结
科学数据库前，必须把仍会影响论文数字的程序归档到本目录，或补齐等价的版本、环境、
输入哈希和确定性运行证据。

## 安全约束

- 项目根目录必须由脚本自身位置推导，不能依赖操作者的当前目录或本机绝对路径。
- 原始科学文件只读；允许覆盖的文件必须是来源目录内显式白名单中的审计
  `JSON/TSV`。既有解包副本只做逐字节验证，不重新解包或改写。
- 审计输出拒绝符号链接/重解析目录，并通过同目录临时普通文件、落盘同步和原子替换
  逐文件提交，避免异常中断留下被截断的 JSON/TSV。
- 同一输入连续运行必须产生字节一致的审计文件。审计基准日属于协议版本，不写入
  当前时间。
- 审计结果位于 `01_原始数据/`，受 `.gitignore` 保护；GitHub 只保存本目录脚本、
  机器治理配置和论文式来源台账。

## 脚本与覆盖来源

| 脚本 | 覆盖来源 | 主要检查 |
|---|---|---|
| `DRUM_TPUU.py` | DRUM 机械回收 TPUU、低天花板 TPUU | ZIP/解包一致性、批次—试样—曲线分层、拉伸/滞回/DMTA 计数与重复 |
| `读取低天花板DMTA.ps1` | DRUM 低天花板 TPUU | 使用 Excel COM 只读解析四个旧版 XLS；由 `DRUM_TPUU.py` 调用 |
| `新增开放数据六源.py` | Jagiellonian 硬段计算、TPU/SWCNT、动态 PU 泡沫、SND 导电轨迹、ScienceDB TPU/ANF、AGH 硬质泡沫 | 计算体系、工作簿、曲线、图像、访问限制和独立条件计数 |
| `Figshare_化学辅助源.mjs` | SHPU、蓖麻油脂肪族 PU | 工作簿语义/公式/数值审计与 0 字节文件硬阻断 |
| `共轭氨基甲酸酯玻璃体.py` | Zenodo 生物基共轭氨基甲酸酯玻璃体 | ZIP/解包一致性、逐试样汇总、松弛与热分析曲线、单位冲突隔离 |
| `历史审计策略对齐.py` | DFT 解封剂、植物基泡沫老化、可打印 PU/PEDOT:PSS | 校验早期摘要哈希，只对齐统一权重上限、拆分键和策略权威性，不改写科学测量与计数 |

## 运行

从项目根目录执行：

```powershell
$审计环境 = Join-Path ([System.IO.Path]::GetTempPath()) "TPU-来源审计-Python312"
$审计Python = Join-Path $审计环境 "Scripts\python.exe"
uv venv $审计环境 --python 3.12 --clear --no-project
uv pip sync 代码\审计\requirements.lock --python $审计Python --require-hashes
& $审计Python 代码\审计\DRUM_TPUU.py
& $审计Python 代码\审计\新增开放数据六源.py
& $审计Python 代码\审计\共轭氨基甲酸酯玻璃体.py
& $审计Python 代码\审计\历史审计策略对齐.py
```

上述命令在系统临时目录重建审计专用环境；`uv venv --no-project` 不解析根项目，
`uv pip sync --require-hashes` 则强制每个安装分发包与锁文件哈希匹配。因此，它不会修改
根目录的 `.venv`、`pyproject.toml` 或 `uv.lock`。更新审计依赖时，使用 Python 3.12
和当前 Windows 目标平台重新生成带分发包哈希的独立锁文件：

```powershell
uv pip compile 代码\审计\requirements.in --output-file 代码\审计\requirements.lock --python-version 3.12 --python-platform x86_64-pc-windows-msvc --generate-hashes --custom-compile-command "uv pip compile 代码\审计\requirements.in --output-file 代码\审计\requirements.lock --python-version 3.12 --python-platform x86_64-pc-windows-msvc --generate-hashes"
```

每次更新 `requirements.in` 或锁文件后，必须重新执行来源审计、连续双运行和全部输出
SHA-256 比对；不能只凭依赖安装成功就宣称结果可复现。

DRUM 旧版 `.xls` 与六源审计中的旧版 Excel 文件需要本机 Microsoft Excel；工作簿
始终以只读模式打开且不保存。Figshare 脚本需要 Codex 工作区提供的
`@oai/artifact-tool`；确认 Node 能直接解析该包后运行
`node 代码\审计\Figshare_化学辅助源.mjs`。若常规模块解析找不到它，先把
`ARTIFACT_TOOL_NODE_MODULES` 指向工作区依赖加载器返回的 `node_modules`，再运行
同一命令；该内部运行时依赖不复制进仓库。当前实测环境为 Node `24.15.0`、
`@oai/artifact-tool 2.8.24`，机器记录见[`审计环境.json`](审计环境.json)。由于该包
来自 Codex 工作区而非本仓库锁文件，版本不同或迁移到非 Codex 环境时必须重新执行
双运行哈希门禁，不能直接宣称跨环境字节复现。Python 审计依赖已由
`代码/审计/requirements.lock` 独立固定，不进入根项目冻结依赖。

最终科学准入、证据等级、泄漏组和未来权重上限以
[`配置/v0.2多保真准入与权重策略.yaml`](../../配置/v0.2多保真准入与权重策略.yaml)
及[`文档/质量报告/TPU数据库_v0.2_新增开放数据准入报告.md`](../../文档/质量报告/TPU数据库_v0.2_新增开放数据准入报告.md)
为准；审计文件数量本身不是训练样本量。
