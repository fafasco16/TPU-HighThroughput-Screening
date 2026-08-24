# xTB 构象系综描述符实施方案

## 1. 目的与结论

本方案承接 `CREST 3.0.2 + GFN2-xTB 6.7.1` 构件构象搜索。输入是通过审计的 `crest_conformers.xyz`，输出是逐构象量化结果、298.15 K 电子能代理权重、构件级系综描述符及配方级回填字段。该层用于比较构象柔性、分子电性、NCO/OH 位点电荷和几何暴露程度，并为后续反应路径计算筛选对象；它不是 TPU 拉伸强度、韧性、DMA 或相分离的直接预测模型。

本批 CREST 未使用隐式溶剂，因此主结果必须保持气相 GFN2-xTB 势能面一致。这里的“Boltzmann 权重”只由同一方法的电子能计算，正式字段命名为 `boltzmann_proxy_weight_298K`，不得写成实验平衡布居或严格热力学布居。

## 2. 固定方法与版本

| 项目 | 固定值 | 理由 |
|---|---|---|
| 构象来源 | CREST 3.0.2 `crest_conformers.xyz` | 当前批次已经完成或正在完成的唯一构件系综 |
| 单点程序 | xTB 6.7.1 | 与构象搜索的 GFN2-xTB 方法一致，服务器已核验版本 |
| 哈密顿量 | GFN2-xTB，`--gfn 2` | GFN2-xTB 面向结构、频率与非共价相互作用等任务[1] |
| 总电荷/未配对电子 | 从任务清单逐项读取；当前候选预期 `0/0` | 不依赖程序默认值，避免错误自旋或电荷 |
| 单点电子温度 | 300 K，`--etemp 300` | xTB 6.7.1 默认也是 300 K；该量用于 SCC 占据，不等于构象权重温度[4] |
| 构象权重温度 | 298.15 K | 统一的室温比较基准 |
| 溶剂 | 主分析为 `gas_phase` | 与本批气相 CREST 势能面闭合 |
| 能量单位 | Hartree 原值，同时派生 kcal mol⁻¹ 相对能 | `crest_conformers.xyz` 注释行能量按 Hartree 解释[5] |

运行前将二进制身份写入清单：

```bash
xtb --version
xtb --citation
sha256sum "$(command -v xtb)"
```

生产环境必须记录完整版本字符串和二进制 SHA-256。方法或主版本不同的结果不得混入同一个系综平均。

## 3. 输入验收与构象选择

### 3.1 构件级输入门

每个构件必须同时满足：

1. `运行状态.json` 为 `completed`，输入哈希与任务清单一致，`crest_conformers.xyz` 哈希与状态记录一致；
2. XYZ 中每一帧原子数相同、元素顺序相同、坐标为有限数，注释行第一项可解析为 Hartree 能量；CREST 官方格式要求系综内原子顺序保持不变[5]；
3. 总电荷和未配对电子数已确认，当前中性闭壳层不得仅凭 SMILES 自动假定；
4. 角色和反应位点闭合：二异氰酸酯应匹配两个 `N=C=O` 碳位点，二醇/宏二醇代理应匹配两个 `[OX2H1]` 氧位点；匹配数不符时进入人工复核，不计算有利的替代值；
5. 2 个 `blocked_input_geometry` 构件继续保留阻断记录，不从分母删除，不用二维结构或相似分子填补。

### 3.2 构象选择规则

`crest_conformers.xyz` 是 CREGEN 去除重复与转子简并后的构象集合；CREGEN 默认能窗为相对最低能量 6 kcal mol⁻¹[6]。本阶段对文件中的全部构象运行单点，不再按预测性质挑选，也不预先删除低布居构象。这样避免以同一批待计算性质反向选择输入。

构象选择记录以下字段：

```text
component_id
conformer_id
crest_rank
crest_energy_hartree
crest_relative_energy_kcal_mol
crest_ensemble_sha256
conformer_xyz_sha256
atom_order_sha256
selection_status
```

若后续为了成本设置构象上限，必须发布为新的协议版本，并至少保留所有 CREST 相对能量不高于 3.0 kcal mol⁻¹的构象；本版本不设置数量上限。

## 4. xTB 6.7.1 可执行命令与输出

### 4.1 单构象生产命令

每个构象使用独立目录，禁止多个进程共享 `xtbrestart`、`charges`、`wbo` 或 `xtbout.json`：

```bash
xtb conformer.xyz \
  --sp --gfn 2 --chrg 0 --uhf 0 \
  --acc 0.5 --iterations 500 --etemp 300 \
  --pop --dipole --wbo --json --norestart -P 1 \
  > xtb.out 2>&1
```

其中 `--chrg` 和 `--uhf` 必须由任务清单替换，不能在脚本中永久写死。多个构象采用进程级并行，每个 xTB 单点固定 1 线程；并发数不得超过 Slurm 实际分配 CPU 数。

输入预检可单独执行：

```bash
xtb conformer.xyz --define --gfn 2 --chrg 0 --uhf 0 --norestart
```

需要实际溶剂敏感性时，示例命令为：

```bash
xtb conformer.xyz \
  --sp --gfn 2 --chrg 0 --uhf 0 \
  --alpb thf bar1M \
  --acc 0.5 --iterations 500 --etemp 300 \
  --pop --dipole --wbo --json --norestart -P 1 \
  > xtb.out 2>&1
```

只有实验路线确认使用 THF 时才允许用上例；DMF、DMSO、甲苯等必须按真实工艺选择。ALPB 在 xTB 6.3.3 以后可用，GFN2-xTB 有已参数化溶剂集合[7,8]。

### 4.2 6.7.1 已确认的命令/输出边界

| 命令或输出 | 6.7.1 状态 | 本方案用途 |
|---|---|---|
| `--sp`、`--gfn 2`、`--chrg`、`--uhf` | 6.7.1 标签手册明确支持[4] | 单点方法、电荷、自旋 |
| `--pop`、`--dipole`、`--wbo` | 明确支持；GFN2 的这些属性通常也默认打印[4,9] | 人工审计与 `wbo` 文件 |
| `--json` | 明确生成 `xtbout.json`[4] | 机器读取能量、电荷、轨道和偶极 |
| `xtbout.json` 的 `total energy` | Hartree；6.7.1 源码字段[10] | 相对能与权重 |
| `xtbout.json` 的 `orbital energies / eV`、`fractional occupation` | eV 与占据数；6.7.1 源码字段[10] | HOMO/LUMO 派生 |
| `xtbout.json` 的 `HOMO-LUMO gap / eV` | eV；6.7.1 源码字段[10] | 与派生能隙交叉核验 |
| `xtbout.json` 的 `dipole / a.u.` | 三分量原子单位；6.7.1 源码字段[10] | 偶极模长，乘 2.541746 得 Debye |
| `xtbout.json` 的 `partial charges` | GFN2-xTB Mulliken 原子电荷；数组顺序与输入原子顺序一致[4,10] | NCO 碳/OH 氧位点电荷 |
| `charges` | 6.7.1 手册列为 SCC Mulliken 电荷文件；是否随当前命令写出需烟雾验证[4] | 仅作可选核验，核心电荷来自 JSON |
| `wbo` | Wiberg 键级文件[4] | N=C、C=O、C-O、O-H 键级 |
| GFN2 属性表 `Mol. α(0) /au` | 官方属性文档明确列出，同时给出逐原子 `α(0)`[9] | D4 模型相关的各向同性极化率代理 |
| `--alpha` | 6.7.1 命令存在，定义为扩展静态偶极极化率属性[4] | 仅作可选烟雾测试，不作为核心字段 |
| `.xtbok` 与退出码 0 | 手册定义为成功运行信号[4] | 成功门的一部分，不能单独代表字段完整 |

极化率必须分清两类。GFN2 默认属性表中的 `Mol. α(0) /au` 与逐原子 `α(0)`来自 GFN2/D4 的电荷和配位数依赖模型[1,9]，字段命名为 `gfn2_d4_alpha0_au`，不能写成实验极化率。虽然 6.7.1 接受 `--alpha`，但 tagged 源码中清晰的“Numerical polarizability tensor”打印路径属于 PTB 属性输出[12]；在安装二进制用标准分子完成字段和单位烟雾测试前，本流程不得虚构 GFN2 JSON 中存在极化率张量，也不得把 `--alpha` 输出替换核心字段。

## 5. 逐构象解析与校验

### 5.1 前线轨道

从 `orbital energies / eV` 和 `fractional occupation` 读取数组。当前闭壳层构件定义 HOMO 为最后一个占据数不小于 1.0 的轨道，LUMO 为其下一个轨道。要求：

- HOMO 和 LUMO 均存在、数值有限；
- 派生 `LUMO-HOMO` 与 JSON 的 `HOMO-LUMO gap / eV` 差异不超过 `1e-4 eV`；
- 若电子温度导致前线占据无法按上述规则唯一划分，则标记 `ambiguous_frontier_occupancy`，不得强行给值。

保存 `homo_ev`、`lumo_ev`、`homo_lumo_gap_ev`。这些轨道能是 GFN2-xTB 方法依赖量，不等同于实验电离能或电子亲和能。

### 5.2 偶极、电荷与极化率

- 偶极：保存 `dipole_x_au`、`dipole_y_au`、`dipole_z_au`、`dipole_magnitude_debye`；模长换算采用 `1 au = 2.541746 D`。只对模长做构象平均，不直接平均会随分子取向改变的笛卡尔分量。
- 电荷：保存全部 GFN2-xTB Mulliken 电荷，同时单列反应位点；要求电荷和与分子总电荷差异不超过 `1e-5 e`。
- 极化率：从标准输出解析逐原子与分子 `α(0)`，保存原始原子单位；解析器同时保存命中的原文行和输出 SHA-256。没有明确标签或单位时留空并报 `missing_polarizability_output`。

### 5.3 NCO 碳与 OH 氧位点描述符

位点索引在初始 RDKit 分子上确定并写为 1-based 索引。CREST 文件格式要求原子顺序不变[5]，每一构象仍需用元素序列哈希再次确认。每个分子通常有两个反应位点，必须分别保存，不能只保留“最有利”位点。

逐位点字段为：

```text
site_id
site_type                       # nco_carbon 或 hydroxyl_oxygen
atom_index_1based
gfn2_mulliken_charge_e
gfn2_atomic_alpha0_au
bond_wbo_primary
bond_wbo_secondary
site_sasa_a2
site_relative_sasa
site_nonbonded_clearance_a
```

键级定义：NCO 碳保存 `WBO(N=C)` 和 `WBO(C=O)`；OH 氧保存 `WBO(C-O)` 和 `WBO(O-H)`。若氢未显式存在、键对在 `wbo` 文件缺失或拓扑发生改变，该构象失败。

`site_sasa_a2`、`site_relative_sasa` 和 `site_nonbonded_clearance_a` 不是 xTB 原生输出，而是对 xTB 构象的确定性几何派生量。建议固定 Bondi 半径表版本、1.40 Å 探针和每原子 960 个 Fibonacci 球面点；位点暴露点比例为 `site_relative_sasa`，乘 `4π(r_vdw+1.40)^2` 得位点 SASA。最小非键接净间隙排除 1–2 与 1–3 原子后计算，并保留半径表、点数和算法版本。缺失元素半径时停止该位点，不用元素平均值填补。

两个同类位点在构件级另外保存 `site_mean`、`site_min`、`site_max` 和 `site_abs_difference`，用于识别不对称反应性；这些量仍只是电性和位阻代理，不能单独宣称反应速率。

## 6. 298.15 K 电子能代理权重

对同一构件通过全部门禁的构象，用本阶段 xTB 单点总电子能重新计算相对能量，而不是混用 CREST 注释能和 xTB 新能量：

\[
\Delta E_i=(E_i-E_{\min})\times 627.509474\quad \mathrm{kcal\ mol^{-1}}
\]

\[
p_i=\frac{\exp[-\Delta E_i/(RT)]}{\sum_j\exp[-\Delta E_j/(RT)]},
\qquad R=0.00198720425864083\ \mathrm{kcal\ mol^{-1}\ K^{-1}},
\quad T=298.15\ \mathrm{K}.
\]

实现时使用减去最小能量后的 log-sum-exp 形式，避免指数下溢。要求 `Σp_i` 与 1 的差小于 `1e-12`。任一标量性质 `x` 输出：

\[
\bar{x}=\sum_i p_i x_i,\qquad
\sigma_x=\sqrt{\sum_i p_i(x_i-\bar{x})^2}.
\]

除加权均值和标准差外，保存最小值、最大值、主构象权重、1% 以上构象数以及有效构象数：

\[
N_{\mathrm{eff}}=\exp\left(-\sum_i p_i\ln p_i\right).
\]

该权重忽略逐构象 ZPVE、振动/转动/平动热修正以及 CREST 去除转子后可能存在的简并度，因此只能称为“电子能代理权重”。若将来需要物理布居，需对一致能窗内构象做频率和自由能处理；CREST 的构象熵流程本身也要求单独协议与不确定度评估[11]。

## 7. 失败门和可发布状态

### 7.1 逐构象硬失败

出现以下任一情况，逐构象状态记为 `failed` 并保留日志：

- xTB 非零退出、无 `.xtbok`、无有效 `xtbout.json`；
- 出现 `.sccnotconverged`、SCC 未收敛或总能量/性质为 NaN/Inf；
- JSON 的方法或版本不是 `GFN2-xTB / 6.7.1`；
- 电荷和、原子数、元素顺序或输入哈希不一致；
- HOMO/LUMO 与报告能隙不闭合；
- 反应位点数量改变、WBO 显示拓扑改变，或位点索引无法回连；
- 输出目录含其他构象的 restart/结果文件，无法证明一构象一目录。

### 7.2 构件级门

- 所有入选构象均成功：`ensemble_status=complete`，发布全套系综描述符；
- 任一构象失败：保留成功的逐构象结果，但 `ensemble_status=incomplete`，不发布 Boltzmann 加权构件值，不进入配方排名；
- 只有极化率解析失败而能量、电荷、轨道、偶极均通过：可发布 `partial_property`，极化率相关字段为空，模型不得静默插补；
- CREST 或初始几何阻断：保持 `blocked_input_geometry`，不运行 xTB。

失败后允许在新 `attempt_id` 下调整 SCC 收敛策略，但必须保留旧日志；不得只挑成功构象后重新归一化并冒充完整系综。

## 8. 数据文件与回填字段

建议保持浅层输出：

```text
计算/xTB系综/
├── 构象级描述符.csv.gz
├── 构件级系综描述符.csv
├── 失败与阻断清单.csv
└── xTB系综发布清单.json
```

### 8.1 构象级表

除第 3、5 节字段外，必须包含：

```text
descriptor_release_id
task_index
candidate_id
component_role
conformer_id
charge
uhf
xtb_version
xtb_binary_sha256
method
environment_model
electronic_temperature_k
ensemble_temperature_k
total_energy_hartree
relative_energy_kcal_mol
boltzmann_proxy_weight_298K
homo_ev
lumo_ev
homo_lumo_gap_ev
dipole_magnitude_debye
gfn2_d4_alpha0_au
run_status
warning_codes
input_sha256
xtbout_json_sha256
stdout_sha256
wbo_sha256
```

位点表可采用长表，主键为 `(candidate_id, conformer_id, site_id)`，避免把多位点压进不可查询的字符串。

### 8.2 构件级表

每个连续性质保存 `_weighted_mean`、`_weighted_sd`、`_min`、`_max`。另保存：

```text
conformer_count_input
conformer_count_success
energy_span_kcal_mol
dominant_conformer_weight
conformer_count_weight_ge_0p01
effective_conformer_count
ensemble_status
```

### 8.3 配方级回填

用稳定 `candidate_id` 把构件级字段回填到 48 条配方，并加角色前缀：

```text
diisocyanate__*
macrodiol_proxy__*
chain_extender__*
```

配方级可再派生三类差值/组合量：`NCO_C_charge - OH_O_charge`、三构件偶极/极化率对比、三构件有效构象数与位点暴露的瓶颈值。配方原始结构、硬段质量分数、NCO/OH、目标 Mn、适用域距离、EHS/采购/新颖性状态继续独立保留。不得把宏二醇代理的小分子描述符解释为 Mn=2000 真实低聚物的描述符。

## 9. 气相与溶剂模型的决策

主发布使用气相，原因不是气相等同于 TPU 反应环境，而是当前 CREST 构象和能量全部来自同一气相 GFN2-xTB 势能面；保持方法一致才能得到可审计的第一版相对描述符。

当实验路线确定溶剂后，另发一个 `environment_model=ALPB_<solvent>_bar1M` 的敏感性版本，并满足：

1. CREST 构象搜索和逐构象单点均使用相同 `--alpb <solvent> bar1M`；
2. 不把气相构象只做溶剂单点后称为完整溶液系综，因为溶剂可能改变构象排序与采样覆盖；
3. 溶剂名必须来自 6.7.1 已参数化列表，记录 ALPB 版本和参考态[4,7,8]；
4. 无溶剂熔融聚合不能用任意低极性 ALPB 溶剂代替。气相和 ALPB 都不包含聚合物基体的局部介电、显式氢键、拥挤和多体环境。

## 10. 能回答与不能回答的问题

本阶段可以回答：在统一 GFN2-xTB 协议下，哪些构件更柔性、具有更多低能构象；前线轨道、电荷、偶极和 D4 极化率代理如何随构象变化；NCO/OH 位点是否存在明显电性或几何不对称；哪些配方值得进入成对反应路径和真实宏二醇闭合。

本阶段不能直接解释宏观力学，原因包括：

- 构件是孤立小分子，未描述聚合反应转化率、序列和副反应；
- 宏二醇仍是结构代理，没有真实重复单元、Mn、Mw、PDI、链长和端基分布；
- 没有硬/软段微相分离、结晶、氢键网络、缠结和自由体积；
- 没有加工历史、退火、残余溶剂、缺陷、试样尺寸、温湿度和应变率；
- HOMO/LUMO、Mulliken 电荷和位点 SASA 是方法依赖代理，既不是反应自由能垒，也不是实验速率常数；
- 电子能构象权重不是凝聚相自由能布居。

因此这些字段只进入多目标筛选、适用域判断和不确定度分析。拉伸强度、断裂伸长率、韧性、循环恢复、DMA 模量和 Tg 必须由闭合的聚合物模型、实验数据及最终真实合成验证支持。

## 11. 最小验收测试

生产批处理前先取一个刚性二异氰酸酯和一个柔性二醇，各抽取两个构象执行烟雾测试：

1. 相同输入连续运行两次，JSON 数值与文件哈希在允许的文本时间戳差异外可复现；
2. JSON 键名与 6.7.1 tagged 源码一致，轨道单位为 eV、能量为 Hartree、偶极为 a.u.；
3. 电荷和、能隙和偶极换算通过；
4. `wbo` 能按 1-based 原子索引找到目标键；
5. 标准输出能稳定解析 `Mol. α(0) /au`，否则将极化率标记为非核心缺失字段；
6. 位点映射在两个构象中保持一致；
7. 298.15 K 权重和为 1，构象顺序打乱后构件级结果不变；
8. 构象失败时不会静默重归一化或生成构件级完整状态。

## 12. 参考文献与官方资料

[1] Bannwarth, C.; Ehlert, S.; Grimme, S. GFN2-xTB—An Accurate and Broadly Parametrized Self-Consistent Tight-Binding Quantum Chemical Method with Multipole Electrostatics and Density-Dependent Dispersion Contributions. *Journal of Chemical Theory and Computation* **2019**, *15* (3), 1652–1671. https://doi.org/10.1021/acs.jctc.8b01176.

[2] Pracht, P.; Bohle, F.; Grimme, S. Automated Exploration of the Low-Energy Chemical Space with Fast Quantum Chemical Methods. *Physical Chemistry Chemical Physics* **2020**, *22* (14), 7169–7192. https://doi.org/10.1039/C9CP06869D.

[3] Pracht, P.; Grimme, S.; Bannwarth, C.; et al. CREST—A Program for the Exploration of Low-Energy Molecular Chemical Space. *The Journal of Chemical Physics* **2024**, *160* (11), 114110. https://doi.org/10.1063/5.0197592.

[4] Grimme Lab. *xTB 6.7.1 Manual Page*, tagged source `v6.7.1`; GitHub, 2024. https://github.com/grimme-lab/xtb/blob/v6.7.1/man/xtb.1.adoc (accessed 2026-08-24).

[5] CREST Developers. *CREST File Formats: Ensemble and Trajectory Files*. https://crest-lab.github.io/crest-docs/page/documentation/coords.html (accessed 2026-08-24).

[6] CREST Developers. *Ensemble Sorting (CREGEN)*. https://crest-lab.github.io/crest-docs/page/examples/example_2.html (accessed 2026-08-24).

[7] Ehlert, S.; Stahn, M.; Spicher, S.; Grimme, S. Robust and Efficient Implicit Solvation Model for Fast Semiempirical Methods. *Journal of Chemical Theory and Computation* **2021**, *17* (7), 4250–4261. https://doi.org/10.1021/acs.jctc.1c00471.

[8] Grimme Lab. *xTB Documentation: Implicit Solvation*. https://xtb-docs.readthedocs.io/en/latest/gbsa.html (accessed 2026-08-24).

[9] Grimme Lab. *xTB Documentation: Properties*. https://xtb-docs.readthedocs.io/en/latest/properties.html (accessed 2026-08-24).

[10] Grimme Lab. *xTB 6.7.1 Machine-Readable JSON Writer*, tagged source `v6.7.1`; GitHub, 2024. https://github.com/grimme-lab/xtb/blob/v6.7.1/src/main/json.F90 (accessed 2026-08-24).

[11] Pracht, P.; Grimme, S. Calculation of Absolute Molecular Entropies and Heat Capacities Made Simple. *Chemical Science* **2021**, *12* (19), 6551–6568. https://doi.org/10.1039/D1SC00621E.

[12] Grimme Lab. *xTB 6.7.1 Property Output Source*, tagged source `v6.7.1`; GitHub, 2024. https://github.com/grimme-lab/xtb/blob/v6.7.1/src/main/property.F90 (accessed 2026-08-24).
