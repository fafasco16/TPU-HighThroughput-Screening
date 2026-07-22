# TPU 第二阶段虚拟候选空间

- 发布：`tpu-virtual-formulation-2026-07-22-v1`
- 目的：为下一轮结构代理、DFT/MD 复核和实验短名单提供**可回溯的虚拟配方假设**。
- 不是：性能数据库、已合成材料目录、商业产品目录，或文献新颖性结论。

## 当前候选由什么组成

| 文件 | 当前规模 | 含义 |
|---|---:|---|
| `候选构件库.csv` | 136 | 96 个二异氰酸酯、24 个二醇扩链剂、16 个宏二醇结构代理；均来自冻结 Gold-V 的已审计结构。|
| `构件门禁审计.csv` | 3 行 | 三类构件的输入量、通过原型门数量、入选数量和逐项排除原因。|
| `候选组合库.csv` | 1,152 | 三组分的平衡轮转组合，不是全笛卡尔积。|
| `可合成配方候选.csv.gz` | 9,216 | 组合 × 目标宏二醇 Mn（1000/2000）× 硬段质量分数（0.35/0.45）× NCO/OH（1.00/1.02）的计量闭合配方。|
| `候选发布清单.json` | — | 冻结输入、配置、行数与所有输出文件的 SHA-256。|

这里的“可合成”只表示双官能端基、设定的 NCO/OH 与质量分数在计量上可闭合；它**不**表示已验证反应动力学、相容性、原料可采购性、EHS、纯化、分子量分布、加工窗口或实际性能。因此文件名中的“可合成配方候选”应理解为 *stoichiometrically feasible virtual formulation*，不是实验可直接下单的配方。

## 构件门与组合规则

二异氰酸酯必须是两个分离的 NCO 连接位点、无同位素标记、无额外 C=O、无竞争反应官能团、无 NCO 以外脂肪族不饱和键，且相对分子质量在 140–450 g mol⁻¹。二醇扩链剂为两个 OH、60–250 g mol⁻¹；宏二醇代理为两个 OH、250–450 g mol⁻¹。三类构件均只接受 C/N/O/F/Cl/Br 元素集合；这是一条原型路线范围，不是普遍的 TPU 化学边界。

通过门的构件再用 512-bit Morgan 指纹的 Tanimoto max-min 规则选取多样化子集。每个二异氰酸酯轮转关联 4 个宏二醇代理，每个关联宏二醇再轮转关联 3 个扩链剂，得到 `96 × 4 × 3 = 1,152` 个组合。这样保留结构覆盖，同时避免把 3,212 万个理论组合伪装成可处理的候选库。

`macrodiol_proxy_smiles` 是 Gold-V 中的小分子双醇结构代理；`macrodiol_nominal_mn_g_mol` 则是未来聚醚/聚酯宏二醇的**目标 Mn 假设**。二者不能混为同一实测变量。计量以每 1 mol 宏二醇端基链为基准，按照双官能端基和无副反应假设解出扩链剂与二异氰酸酯用量；`stoichiometry_residual` 和 `nco_oh_ratio_calculated` 是必查的闭合字段。

## 如何正确使用

```powershell
# 重建候选空间（仅读取冻结的候选结构视图）
uv run python .\代码\生成候选配方.py

# 核验发布哈希和计量闭合，不写文件
uv run python .\代码\生成候选配方.py --检查
```

```python
import pandas as pd

formulations = pd.read_csv("候选/可合成配方候选.csv.gz")
review_queue = formulations.loc[
    (formulations["macrodiol_nominal_mn_g_mol"] == 2000.0)
    & (formulations["hard_segment_mass_fraction_target"] == 0.45)
    & (formulations["nco_oh_ratio_target"] == 1.02)
].copy()

# 后续阶段才填入 DFT/MD、适用域、EHS、供应和文献新颖性结果。
assert review_queue["dft_md_status"].eq("not_calculated").all()
```

不能按 `performance_prediction_status` 排序：它目前全部是 `not_scored_by_baseline`。第一阶段模型在严格来源留出上没有显示出可直接用于跨来源发现的泛化能力，因此本阶段刻意不制造“高性能 Top-100”名单。下一步应以此空间为输入，先做适用域、可得性/EHS与文献新颖性预审，再建立 DFT/MD 复核队列。

## 数据来源与论文引用

本发布不新增外部数据。二异氰酸酯虚拟结构来自 PolyUniverse 固定 Zenodo 版本；二醇与宏二醇结构代理来自 SMiPoly 公开单体示例。论文或补充材料应引用原始作者与固定数据版本，而不是只引用本仓库：

1. Ohno, M.; Hayashi, Y.; Zhang, Q.; Kaneko, Y.; Yoshida, R. SMiPoly: Generation of a Synthesizable Polymer Virtual Library Using Rule-Based Polymerization Reactions. *Journal of Chemical Information and Modeling* **2023**, *63*, 5539–5548. https://doi.org/10.1021/acs.jcim.3c00329.
2. Yue, T. PolyUniverse: Generation Results [Data set]; Zenodo, 2024. https://doi.org/10.5281/zenodo.12585902.
3. Yue, T.; He, J.; Li, Y. Polyuniverse: Generation of a Large-Scale Polymer Library Using Rule-Based Polymerization Reactions for Polymer Informatics. *Digital Discovery* **2024**, *3*, 2465–2478. https://doi.org/10.1039/D4DD00196F.

完整项目引文键、许可证和定位信息见 [数据来源与参考文献](../文档/数据来源与参考文献.md) 的 `[7]`、`[135]`–`[137]` 与 [来源与引用](../结果/可用数据集/来源与引用.csv)。源数据的 `CC BY 4.0` / `BSD-3-Clause` 条件与本候选发布无关的第三方材料采购、生产和商业使用许可必须另行确认。
