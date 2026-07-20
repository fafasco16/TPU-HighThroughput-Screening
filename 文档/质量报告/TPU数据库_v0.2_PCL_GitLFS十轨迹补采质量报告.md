# TPU 数据库 v0.2：PCL Git LFS 十轨迹补采质量报告

报告日期：2026-07-20
状态：`provenance_only / training_blocked / redistribution_denied`
来源范围：`scope_pcl_git_lfs_supplement_local`

## 1. 结论

本轮从 Bačová 的 PCL 系统粗粒化模型固定仓库补齐了 Zenodo v1.0_2 归档中十个 Git LFS 指针对应的真实 `trr.bz2` 载荷。[1–3] 十个对象均满足“Git LFS 声明字节 = 本地字节 = OID SHA-256”，并通过 BZip2 解压与 TRR 全帧读取；压缩载荷共 **2,313,207,356 字节**，解压 TRR 共 **2,578,712,040 字节**，合计 **10,569 帧**。

这些帧是十条时间相关模拟运行中的观测，不是 10,569 个材料、配方或独立样本。补采目录与 `Zenodo_PCL软段构象粗粒化MD` 属于同一 DOI、同一固定提交与同一模型家族，因此新增的是载荷闭包，不新增独立科学来源身份。当前训练权重为 **0**，不建立拆分、不物化训练记录。

## 2. 固定来源与本地边界

| 项目 | 固定值 |
|---|---|
| Zenodo 数据 DOI | `10.5281/zenodo.17790918` |
| Zenodo 版本 | `v1.0_2` |
| Zenodo 归档 SHA-256 | `5a59701e7a09f1f8b7907a0c9de70c86ffca05b4825812479b4ad4ad0a127002` |
| GitHub 仓库 | `pbacova/PCL_Supplementary_material_systematic_CG` |
| 归档对应提交 | `446ebadb9ba937d393b6cd7d727256c90e15f24e` |
| 归档对应树 | `51894a12d912275f37a23853a76dbc2f36e09584` |
| 请求但不存在的提交 | `46683548a86a7b3c9007abe9b18da82ecb14dfe3`；GitHub API 返回 422，响应已留证 |
| 本地文件边界 | 35 个文件，2,313,383,883 字节 |
| 根级清单与审计 | 4 个文件，31,431 字节 |
| `来源快照/` | 21 个 JSON，145,096 字节 |
| `轨迹载荷/` | 10 个 `trr.bz2`，2,313,207,356 字节 |

本地 35 个文件均位于 `01_原始数据/**`，继续受 Git 忽略规则约束；质量报告、规则和审计代码可以版本控制，第三方载荷不得推入项目仓库。

## 3. 十条轨迹的身份与科学审计

| 运行家族 | Git LFS OID / 本地 SHA-256 | 压缩字节 | 帧数 | 温度 | 时间范围 | 完成状态 |
|---|---|---:|---:|---:|---:|---|
| 30mer，水中，默认 | `097e6cf7a704b045acd9b5fc5ab1583002234334b1c03d509058b662f478e221` | 213,805,322 | 2,001 | 300 K | 0–100,000 ps | `finished` |
| 30mer，水中，tau0.5 | `dd19c3ce40c8432917e38e633c6a645bba38e83af4c688736bc4108561d76d9c` | 10,794,494 | 101 | 300 K | 0–1,000 ps | `finished` |
| 50mer，水中，默认 | `1dcdb33cfaf3ed8c66a9a15cf6c51b54a6cf6f00b02dca87ddf4dbc3b713c87f` | 355,671,652 | 2,001 | 300 K | 0–100,000 ps | `finished` |
| 100mer，水中，默认 | `8aba52efb0722534bddbdc60041fba887aaa1d0295c944eb15493d4b5f37f828` | 712,058,133 | 2,001 | 300 K | 0–100,000 ps | `finished` |
| 100mer，水中，tau0.5 | `a6e402aa86f2842e7989be38e3865b7121889fd7c9359d7f36492e78bd85d023` | 35,776,708 | 101 | 300 K | 0–1,000 ps | `finished` |
| 100mer，真空，默认 | `fcff4250dba02f33fa0c1bb0c6cf674a4c4359d79fffe4f72acdd53df4f7b42d` | 1,167,538 | 75 | 600 K | 0–7,400 ps | `terminated_before_declared_nsteps` |
| 125mer，水中，默认 | `45b42df46cb833f332942cc0c9d2e3062d3755448a743e847adc18fe7455daa1` | 891,115,769 | 2,001 | 300 K | 0–100,000 ps | `finished` |
| 125mer，水中，tau0.5 | `7a0cc722314124ad1f27002c7a2b193f1415160c5f14eb584a514875b756b16a` | 44,751,235 | 101 | 300 K | 0–1,000 ps | `finished` |
| 125mer，真空，300K | `87f48389f24b48bd11d01c1c4c2baa82b3c99d44f6719283abadbecba3dbbc93` | 46,855,956 | 2,126 | 300 K | 0–212,500 ps | `terminated_after_continuation_beyond_declared_nsteps` |
| 125mer，真空，默认 | `f442561a7744439c34ee8cffcbc096740a1f800d7471c6cfc0e5982a19e8a1a3` | 1,210,549 | 61 | 600 K | 0–6,000 ps | `terminated_by_second_int_term_signal` |

七条运行标为正常完成；其余三条分别为声明步数前终止、续跑超过声明步数后终止和第二次中断信号终止。所有轨迹均不含速度帧和力帧。目录名 `tau0.5` 实际对应 `dt = 0.5 fs`、`tau_t = 0.1 ps`；目录语义不能代替 `mdout.mdp` 中的协议字段。

## 4. 可用价值与当前硬门

当前可以把这十条轨迹作为以下工作的**可追溯复现证据**：

1. 核对 PCL 软段链长、溶剂环境、温度和积分协议对构象统计的影响；
2. 在整条运行内计算并报告平衡后聚合描述符，例如回转半径、端到端距离、构象分布和相关时间；
3. 为未来 TPU 软段跨尺度模型提供方法先验，但不得把 PCL 纯体系直接当成完整 TPU 配方性能标签。

以下条件未闭合前，监督训练权重保持 0：

- 仓库 GitHub 元数据 `license = null`，固定树未发现 `LICENSE` 或 `COPYING`；Zenodo 包的 CC BY 4.0 不能自动覆盖从 GitHub LFS 端点取得的对象；
- 三条非正常终止运行需要单独的平衡、收敛和截断敏感性分析；
- 同一 TRR 内的帧强相关，必须以“父数据 DOI + 固定提交 + 链长 + 环境 + 协议 + 轨迹运行”为分组单位；
- 尚未建立 PCL 构象描述符到 TPU 实验软段化学、相分离或力学性能的校准关系。

权利、收敛、实验映射和聚合描述符全部闭合后，只能重新审议**运行级聚合描述符**，不能给帧级观测赋独立权重。当前机器策略给本补采范围 `base_weight_ceiling = 0.00`；未来建议上限为 0.20，且与 Zenodo 母范围合计不得超过 0.30。这是设计上限，不是当前授权或已物化权重。

## 5. 可复现文件与机器治理

- 获取程序：[`代码/获取/补采PCL_GitLFS十轨迹.py`](../../代码/获取/补采PCL_GitLFS十轨迹.py)
- 审计程序：[`代码/审计/审计PCL_GitLFS十轨迹.py`](../../代码/审计/审计PCL_GitLFS十轨迹.py)
- 来源范围：[`配置/v0.2来源范围.yaml`](../../配置/v0.2来源范围.yaml)
- 资产规则：[`配置/v0.2资产登记规则.yaml`](../../配置/v0.2资产登记规则.yaml)
- 多保真策略：[`配置/v0.2多保真准入与权重策略.yaml`](../../配置/v0.2多保真准入与权重策略.yaml)

本报告不授予任何许可证，也不替代作者、仓库或出版方条款。训练、再分发和公开派生发布必须分别获得动作级权利裁决。

## 参考文献

[1] Bačová, P. *PCL in Vacuum and in Water* [Software/Data], version 1.0_2; Zenodo, 2025. https://doi.org/10.5281/zenodo.17790918.

[2] Bačová, P.; González Huarte, G.; Harmandaris, V.; Molina, S. I. Development of a Systematic Coarse-Grained Model for Poly(ε-Caprolactone) in Melt. *Open Research Europe* **2025**, *5*, 296. https://doi.org/10.12688/openreseurope.21354.2.

[3] Bačová, P. *PCL Supplementary Material Systematic CG*; GitHub repository, fixed commit `446ebadb9ba937d393b6cd7d727256c90e15f24e`, tree `51894a12d912275f37a23853a76dbc2f36e09584`, accessed 2026-07-20. https://github.com/pbacova/PCL_Supplementary_material_systematic_CG.
