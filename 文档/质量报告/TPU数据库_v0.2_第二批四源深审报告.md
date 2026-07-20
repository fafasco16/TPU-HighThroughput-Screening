# TPU 数据库 v0.2 第二批四源深审报告

> 审计基准日：2026-07-20
> 状态：原始文件已落地并完成只读复算；尚未创建训练划分，尚未物化训练权重。
> 复算入口：`代码/审计/新增开放数据第二批四源.py`
> 下载入口：`代码/获取/下载第二批开放数据四源.py`

## 1. 结论

本批次新增 4 个正式开放数据来源、11 个最小充分科学文件。它们显著增加了 TPU/PU
曲线、本构、流变、温度—速率、DIC 和有限元验证信息，但只增加少量独立材料体系。
因此数据库同时保存“文件—曲线—点”和“材料—物理试样—运行”两套计数，训练时只能
以后者决定独立权重。

| 来源 | 可恢复的科学独立单元 | 大规模数值 | 当前定位 |
|---|---:|---:|---|
| 标准化弹性体表征 | 2 个目标商业 TPE 牌号；42 条目标曲线；物理试样数未知 | 1,341,840 个可用行/同步点 | 商业牌号的力学、松弛、热学和流变辅助层 |
| PU 微球复合材料 | 6 个体积分数、12 个物理试样 | 23,922 个三通道索引行，其中 9 个 YZ 尾端缺失 | PU 复合体系的滞回和体积响应辅助层 |
| 高低速变形后松弛 | 2 个反应固化商用 PU、38 个材料—工况单元、108 条曲线实例；物理试样数未知 | 1,459,510 个跨视图完整坐标对 | PU 黏弹/温度—速率辅助层；不是已知结构 TPU |
| EOS TPU 1301 | 80 个身份闭合直接实验运行、4 条手工数字化曲线；15 个唯一有限元验证运行 | 5,818,564 个身份闭合实验有限点；112,358,792 个验证数值单元 | 单商业牌号本构代理、速率效应和物理正则化 |

本报告中的“大规模数值”不是独立样本数。每条曲线必须先在曲线内归一，每个工况、
试样或模拟运行再归一，最后才允许在材料层组合；任何按行随机划分都会造成严重泄漏。

## 2. 来源与文件证据

### 2.1 标准化弹性体表征

- 数据 DOI：10.5281/zenodo.14983287。[1]
- 论文 DOI：10.1002/aisy.202500699。[2]
- 许可：CC BY 4.0。
- 固定下载：7 个 ZIP，合计 88,262,468 B；148 个条目、139 个文件，解压后
  322,808,455 B。
- 安全检查：CRC、路径穿越、绝对路径、加密、符号链接和重复成员名均通过；139 个
  文件内容哈希均唯一。

数据实际出现 11 个规范化材料标签，而不是简单的 10 个。`NinjaFlex 90A` 只存在于
一份 DSC 文件，其 12,237 行中 `Heat Flow` 有限值为 0，因此登记材料标签但隔离该空响应
协议文件。目标牌号 Cheetah 和 Filaflex 60A 的精确计数为：

| 牌号 | CSV 曲线 | CSV 原始行 | CSV 可用点 | XLS 多变量曲线 | XLS 同步点 | 合计可用点 |
|---|---:|---:|---:|---:|---:|---:|
| Cheetah | 12 | 655,323 | 655,315 | 0 | 0 | 655,315 |
| Filaflex 60A | 14 | 684,446 | 684,431 | 16 | 2,094 | 686,525 |

Filaflex 旧版 XLS 从固定 `Melting.zip` 成员临时提取；成员 SHA-256 为
`3fc855fb76b452a1768df8b18e9edc270843bb9b370fd59213f6b9e2e3dc0295`。Excel 仅以
`ReadOnly=true` 打开，不保存、不转换。17 个工作表中 1 个为 Details、16 个为测量表：
Temperature ramp 1 为 5 条/462 点，Flow ramp 6 为 1 条/22 点，Temperature ramp 3
为 5 条/1,194 点，Temperature ramp 4 为 5 条/416 点。同步行是点数；A/B/C 三个响应
通道不重复算成三份实验。

该来源没有跨模态 specimen UUID。拉伸/压缩的编号只能证明“观察到的重复曲线”，不能
与 DSC、TGA、松弛或黏度文件合并成精确物理试样数。因此
`physical_specimen_count=null`，并以 DOI + 材料牌号整体防止跨折泄漏。

### 2.2 PU 微球复合材料拉伸

- 数据 DOI：10.5281/zenodo.6390478。[3]
- 论文 DOI：10.1007/s42558-022-00046-1。[4]
- 许可：CC BY 4.0。
- 最小充分文件：`Data_csv.zip` 780,946 B 和 `readme.md` 3,480 B。
- 未下载 12 个原始图像 ZIP，合计约 26.9 GB；它们不会增加 6 个条件、12 个试样或
  数值通道身份。

ZIP 含 96 个文件，其中 44 个科学文件、50 个 `__MACOSX` 资源分叉和 2 个正常目录
`.DS_Store`。后 52 个始终为权重 0。6 个体积分数条件各有 2 个物理试样；每个试样的
Machine、DIC-xy 和 DIC-yz 索引都严格为同一 `range(n)`。三通道各 7,974 行；Machine
和 xy 全完整，yz 有 9 个尾端缺失点，分布于 3 个试样。保留对应试样和缺失掩码，不能
删除整条曲线。

6 条 Post 曲线各 500 点，是同条件两个试样的平均和平滑结果，不新增试样。
`MinMax_Jp.xlsx` 的两张工作表逻辑重复，且同一位置的数值 `98,998,646` 与 CSV 的
`0.98998646` 冲突；工作簿和 MinMax 派生标签在用原始曲线复算解决前均为权重 0。

### 2.3 高低速变形后的聚氨酯松弛

- 数据 DOI：10.6084/m9.figshare.23635998.v1。[5]
- 论文 DOI：10.1098/rspa.2022.0830。[6]
- 许可：CC BY 4.0。
- 固定 ZIP：8,831,991 B；51 CSV + 2 XLSX；外层解压 26,105,915 B。

材料 TASK 3 和 TASK 11 是双组分混合、脱泡、室温固化 24 h 并在 65 °C 后固化 4 h
的商用反应固化 PU。公开文件没有异氰酸酯、多元醇、NCO/OH、硬段含量或分子量等
化学身份，因此禁止标成“已知结构 TPU”。

逐 Figure 固定了 header 行数和三种布局：普通相邻 x/y、带空隔列的 stride-3 x/y、
一个共享 x 对多个 y。全量复算得到 153,375 行和 1,459,510 个完整坐标对；
`Figure_17b + Figure_20a + Figure_21a + Figure_21b` 占 1,342,376 对，即 91.97%。
这四个文件主要代表高采样或同试样的不同坐标视图，不能支配训练损失。

关键质量裁决：

- Figure 10 的 CSV 与 XLSX 中 30 个数值完全一致，XLSX 独立权重为 0；
- Figure 4b 的 B 列才是 `log(time)`，A 列是 −85 °C 模量；普通“首列为 x”会解析错误；
- Figure 14/15、16/17/18、20/21、22/23/24、26/27/28、29、33 和 34 均存在
  明确父曲线、坐标变换、模型或精确重复血缘；
- Figure 31a/31b 的温度网格与论文报告的 −70 至 70 °C、2.5 °C 间隔不一致，设置
  `protocol_consistency=false` 并隔离；
- 可恢复 108 条机械实验/重复曲线实例，但没有 sample ID，不能写成 108 个独立试样。

### 2.4 EOS TPU 1301 实验—本构—有限元数据

- 数据 DOI：10.5281/zenodo.15370425。[7]
- 论文 DOI：10.1016/j.ijsolstr.2025.113517。[8]
- 许可：CC BY 4.0。
- 固定 ZIP：450,879,687 B；SHA-256
  `988c4d2f972582b98be2d40e3ebc0d76538330ff9059aaff3f885d322cfec7ee`；
  614 条目/549 文件，解压 1,381,087,479 B。

实验层共有 94 个数值文件、5,825,934 个原始行。排除误入 TPU 目录的 PA12 文件
2,402 行后为 5,823,532 行。`Cyclic_compression_1V...` 首个数据行的 Eng.strain
误写 `V`，只屏蔽该点；`Relaxation_7H...` 文件名与内嵌 `Specimen label=6V` 冲突，
整条 4,967 点曲线在身份解决前隔离。最终得到 92 个身份闭合数值文件和 5,818,564
个有限候选点。

独立性口径为 85 个采集/曲线单元、81 个可明确实验运行、80 个身份闭合直接实验运行；
另有 4 条从论文 PNG 手工点击的拉伸曲线，物理试样 ID 未知。8 个空心哑铃压缩运行
各含 Std/HS 双通道，二者采样率和时间原点不同，只能在运行层绑定，禁止逐行连接。
6 个扭转文件共 4,209,030 行；每行 8 个必需通道全有限，3 个辅助通道按原协议全为 NaN。

标定层的 F/P/T/epl 等文件是 pickle。审计程序只使用 `pickletools.genops()` 静态读取
opcode，并将 GLOBAL 白名单限制为 NumPy scalar/dtype；从未调用普通反序列化，也不执行
归档 Python。7 个 case 共 20 个子运行、7,792 个同步点和 31,168 个 F/P/T/epl 有限标量，
但实际只有 8 个唯一加载条件；正则化扫描和旋转修正变体不增加实验覆盖。

验证层有 16 个目录运行、92 个 CSV、162,764 行和 112,358,792 个有限数值单元。
Torsion/F26 的 9 个文件在 Rotation/F26 中原样复现，折叠后是 15 个唯一模拟运行、
21,284 个唯一运行时间网格点。92 个 CSV 只有 70 个唯一内容；部分相同文件是对称域
分片并在绘图代码中求和，不能物理删除，但独立权重仍为 0。

## 3. 多保真准入边界

以下数字是未来权重天花板，不是已经物化的训练权重：

| 数据类别 | 上限 | 原因 |
|---|---:|---|
| EOS TPU 1301 直接原始实验 | 1.00 | 真实曲线和商业牌号明确；配方仍未知 |
| EOS TPU 1301 手工数字化拉伸 | 0.65 | 图像数字化且物理试样 ID 未知 |
| 与实验映射的 Validation FE | 0.35 | 可作物理代理；不得进入实验验证/测试标签 |
| Calibration 拟合内输出用于性能监督 | 0 | 同源拟合，存在循环论证 |
| Calibration 单独 FE emulator | ≤0.25 | 仅限独立模拟代理任务 |
| TASK 3/11 原始实验曲线 | ≤0.25 | 反应固化 PU 辅助域，结构/配方未知 |
| TASK 3/11 Prony/Abaqus | ≤0.05 | 同源标定模型，只能辅助训练 |
| 精确重复、坐标替代视图、模拟分片、时间步、坏点和身份冲突 | 0 | 不增加科学独立性 |

统一来源上限与任务映射质量不能重复相乘两次。数据库保存 base ceiling、任务 ceiling 和
映射质量的独立字段；未来物化时由一个策略引擎组合。当前四源全部保持
`training_split_created=false` 和 `training_weight_materialized=false`。

## 4. 可复现性与原始数据发布边界

1. 原始开放文件保存在 `01_原始数据/`，受 `.gitignore` 保护，不推送 GitHub。
2. 私有仓库只保存固定下载脚本、审计程序、配置、哈希、来源台账和报告。
3. 下载程序使用固定官方 API/文件 URL、HTTPS 主机白名单、文件大小和 MD5/SHA-256，
   采用 `.part` 与原子替换；重复运行只复核既有文件。
4. 审计程序不联网；审计前后比较科学输入快照，只允许原子覆盖 12 个白名单 JSON/TSV。
5. 旧版 XLS 只在系统临时目录打开，临时目录经过边界校验后删除；原始 ZIP 不解包改写。
6. 连续两次审计必须产生完全相同的 12 个输出 SHA-256，方可进入治理构建。

## 5. 参考文献

[1] Roels, E.; Costa Cornellà, A.; Brancart, J. *A Standardized Elastomer Characterization Framework for Soft Robotics—Accompanying Dataset* [Data set], version 1; Zenodo, 2025. https://doi.org/10.5281/zenodo.14983287.

[2] Roels, E.; Costa Cornellà, A.; Brancart, J. A Standardized Framework for Elastomer Characterization in Soft Robotics. *Advanced Intelligent Systems* **2026**, *8* (3), e202500699. https://doi.org/10.1002/aisy.202500699.

[3] Coret, M.; Verron, E.; Rublon, P. *Images and Data Accompanying Article: Remarkable Response of Hollow Thermoplastic Microspheres–Elastomer Matrix Composites in Uniaxial Tension* [Data set], version 1; Zenodo, 2022. https://doi.org/10.5281/zenodo.6390478.

[4] Coret, M.; Verron, E.; Rublon, P.; Leblé, B. Remarkable Response of Hollow Thermoplastic Microspheres–Elastomer Matrix Composites in Uniaxial Tension. *Mechanics of Soft Materials* **2022**, *4*, 8. https://doi.org/10.1007/s42558-022-00046-1.

[5] Commins, T.; Siviour, C. R. *Data from Stress Relaxation after Low- and High-Rate Deformation of Polyurethanes* [Data set], version 1; The Royal Society/Figshare, 2023. https://doi.org/10.6084/m9.figshare.23635998.v1.

[6] Commins, T.; Siviour, C. R. Stress Relaxation after Low- and High-Rate Deformation of Polyurethanes. *Proceedings of the Royal Society A* **2023**, *479* (2275), 20220830. https://doi.org/10.1098/rspa.2022.0830.

[7] Noels, L.; Jinaga, U. K. *Data of “A Consistent Finite-Strain Thermomechanical Quasi-Nonlinear-Viscoelastic Viscoplastic Constitutive Model for Thermoplastic Polymers”* [Data set], version 1; Zenodo, 2025. https://doi.org/10.5281/zenodo.15370425.

[8] Jinaga, U. K.; Zulueta, K.; Burgoa, A.; Cobian, L.; Freitas, U.; Lackner, M.; Major, Z.; Noels, L. A Consistent Finite-Strain Thermomechanical Quasi-Nonlinear-Viscoelastic Viscoplastic Constitutive Model for Thermoplastic Polymers. *International Journal of Solids and Structures* **2025**, *321*, 113517. https://doi.org/10.1016/j.ijsolstr.2025.113517.
