# TPU 候选 DFT/MD 分层复核协议

## 1. 当前状态与目的

本协议对应 `候选/DFT_MD复核队列.csv` 的 `TPU-DFT-T1-v1`。队列有 48 条虚拟配方，来自固定格点：目标宏二醇 Mn 2000 g mol⁻¹、目标硬段质量分数 0.45、NCO/OH 1.02。它们由三构件联合 Morgan 指纹的 Tanimoto max-min 规则选出，用来覆盖结构空间，不是模型预测的性能前 48 名。

48 条配方包含 48 个不同二异氰酸酯、16 个宏二醇代理和 22 个二醇扩链剂，共 86 个唯一构件。第一层计算按唯一构件去重，每个构件只计算一次，再通过稳定 `candidate_id` 回连到配方。构件的 SMILES 来自 Gold-V；宏二醇仍是小分子双醇结构代理，目标 Mn 是未来真实低聚物的假设，两者不能混为同一分子。

### 1.1 2026-08-25现实商业队列增量

虚拟48条队列继续保留为模型训练和结构空间参考，但实验主线已经建立独立现实库。现实库含7个二异氰酸酯、5个PTMG商品牌号和7个扩链剂，共245个三构件基础体系、980个计量配方。14个离散构件完成CREST 3.0.2和1,445个xTB 6.7.1单点；5个PTMG牌号完成单代表链段xTB代理。全部19个构件已经连接到980条配方，并生成40条DFT/MD复核队列。

`结果/现实筛选/高层DFT候选12.csv`从40条队列中保留4条小型商业对照，并以确定性集合覆盖规则补充8条，使12条同时覆盖全部7个二异氰酸酯、5个PTMG和7个扩链剂。该文件不是性能前12名；它是下一层预反应复合物和正式反应路径的覆盖子集。

当前Ubuntu已建立隔离Psi4 1.10.2环境，可执行HF/6-31G(d) RESP及ωB97M-D3BJ/6-31G(d,p)小片段扭转验证；这不等于获得ORCA，也不等于Psi4已经实现本项目预定的r2SCAN-3c复合协议。Ubuntu和Slurm仍未发现授权可执行的ORCA、Gaussian、NWChem或CP2K，因此22个现实NCO–OH配对的ORCA输入只完成生成与哈希核验，正式r2SCAN-3c优化/频率继续标为`blocked_no_authorized_r2scan3c_engine`。GFN2-xTB缔合能仍不得改名为DFT能垒。

## 2. Tier 0：输入和人工门

任何量化计算开始前，逐构件完成：

1. 由 `canonical_smiles` 生成三维结构，并核对价态、互变异构、立体化学、总电荷和自旋多重度；当前默认中性闭壳层只是需要确认的起始假设。
2. 将 `isocyanate_group_requires_SDS_review`、芳香或卤素结构警示与真实 SDS、供应商规格和本单位 EHS 规则核对。结构警示不是 GHS 分类，也不能替代 SDS。
3. 逐条确认原料或可行合成路线。`procurement_status=not_checked` 时不得把候选移入实验采购表。
4. 检查二异氰酸酯是否属于不稳定、瞬态或仅数据库枚举结构；不能因为 RDKit 可解析就认定可分离、可储存或可购买。

未通过任何一项时，记录失败原因并停止该构件后续计算；不得删除记录或把空值填为有利结果。

## 3. Tier 1：构件构象与反应性代理

### 3.1 推荐计算层级

1. 使用 CREST 进行构象采样，能量模型为 GFN2-xTB；保留搜索版本、命令、温度、能窗、溶剂模型和随机种子。GFN2-xTB 是快速半经验紧束缚方法，CREST 用于低能构象空间自动探索[177,178]。
2. 对能窗内去重后的低能构象进行 `r2SCAN-3c` 几何优化与频率计算；频率没有虚频才标记为局部极小值。`r2SCAN-3c` 是带基组、色散和基组叠加误差修正的复合方法[179]。
3. 当前原型路线以无溶剂/熔融体系为主要假设，气相结果只作统一结构代理。确定真实溶剂或加工环境后，必须用明确的隐式溶剂或凝聚相模型做敏感性复算，不能把气相排序直接解释为反应釜排序。

推荐保存的逐构件输出为：最低构象电子能和热修正、5 kcal mol⁻¹能窗内构象数、偶极矩、各向同性极化率、前线轨道能，以及 NCO 碳或 OH 氧的原子电荷与局部空间可及性。电荷方案、轨道定义和软件版本必须作为字段保存；这些量是方法依赖的反应性/相互作用代理，不是实验反应速率。

### 3.2 结果门禁

每个计算记录至少包含：

```text
calculation_id
candidate_id
formulation_ids
input_smiles
charge
multiplicity
software_and_version
method
basis_or_composite_method
solvation_model
conformer_protocol
geometry_converged
frequency_status
electronic_energy_hartree
thermal_correction_hartree
descriptor_name/value/unit
run_directory_or_archive
input_sha256
output_sha256
warnings
```

优化未收敛、存在非预期虚频、构象搜索失败或电荷/自旋未确认时，该构件只保留失败记录，不能参与数值排名。跨构件比较必须使用完全相同的方法、软件主版本、温度和溶剂约定。

### 3.3 Tier 1 能回答和不能回答的问题

Tier 1 可以比较同一方法下的构象柔性、局部电性、NCO/OH 位点可及性和可能的相互作用倾向，并据此把 48 条队列进一步缩小到成对反应路径计算。它不能直接预测 TPU 的拉伸强度、断裂伸长率、韧性、循环恢复、DMA 模量或宏观相分离尺度。

如果需要比较真实反应活性，下一层应针对二异氰酸酯—醇模型对建立反应物复合物、过渡态和产物路径，报告自由能垒及构象/溶剂敏感性。单独用 HOMO/LUMO 或某一种原子电荷作为反应速率结论是不充分的。

### 3.4 现实构件NCO–OH预反应复合物协议

2026-08-25现实高层DFT子集形成22个唯一配对：10个二异氰酸酯–扩链剂配对和12个二异氰酸酯–PTMG配对。每个配对按两个NCO碳位点×两个OH氧位点生成4个确定性起点，共88项。初始NCO碳–OH氧距离固定为2.70 Å，进攻角固定为105°；端基/取向冲突通过记录化的方位角与扭转角搜索处理，无法消除的小于0.70 Å片段碰撞不强行修复，而是保留为`blocked_initial_interfragment_collision`。

80项通过几何门的任务使用官方xTB 6.7.1、气相GFN2-xTB、`tight`优化和NCO碳–OH氧距离约束。单体参考能来自完全相同xTB版本下的离散构件最低能成功构象或PTMG单链代理。正式代理量为：

\[
\Delta E_{\mathrm{assoc,proxy}}=(E_{\mathrm{complex}}-E_{\mathrm{DII}}-E_{\mathrm{OH}})\times 627.509474\quad\mathrm{kcal\ mol^{-1}}.
\]

该数包含约束、构象形变和气相方法误差，既不是无约束结合自由能，也不是反应能垒。逐任务必须同时满足：退出码0、`GEOMETRY OPTIMIZATION CONVERGED`、无`.sccnotconverged`、最终约束距离2.3–3.1 Å、JSON总能量有限、4个规定输出及SHA-256闭合。xTB即使写出`normal termination`，只要日志含`FAILED TO CONVERGE GEOMETRY OPTIMIZATION`仍判为未收敛。

逐配对采用“宽准入、严标注”：

- 全部可运行起点收敛：`complete/admitted_reference`；
- 初始碰撞起点被阻断、其余至少2项收敛：`complete_with_blocked_starts/conditional_reference`；
- 仅有几何未收敛起点、其余至少2项收敛且无程序/哈希/SCC错误：`conditional_nonconverged_starts/conditional_reference`；
- 少于2项收敛，或存在程序失败、SCC失败、身份/输出哈希错误：`incomplete/blocked`。

条件参考可以用于选择后续r2SCAN-3c输入，但必须携带多起点缺失警告，不能与四起点完整配对等权。`代码/更新预反应优先级.py`分别连接二异氰酸酯–PTMG与二异氰酸酯–扩链剂代理，不生成黑箱总分；`代码/生成ORCA_r2SCAN3c输入包.py`只有在配对资格、最佳任务状态和4个xTB输出哈希闭合后才生成ORCA优化/频率输入。

最终结果显示PTMG配对最佳缔合能代理跨体系为约−13至−76 kcal mol⁻¹，并随链长和全链构象松弛显著变化；它不能作为跨PTMG牌号的直接Pareto目标。该字段保留为`context_only_size_and_global_deformation_confounded`。预反应优先级只使用扩链剂小分子配对能、PTMG配对多起点离散度和扩链剂配对多起点离散度；PTMG绝对能需待局部模型、形变能分解或高层DFT后再比较。

## 4. MD 启动门与建议层级

当前全部队列的 `md_stage` 为 `on_hold_pending_real_macrodiol_identity_Mn_Mw_PDI`。只有同时闭合下列信息才允许启动原子级 MD：

- 宏二醇的真实重复单元、端基、Mn、Mw、PDI 和聚醚/聚酯家族；
- 二异氰酸酯、扩链剂、硬段质量分数、NCO/OH 与目标转化率；
- 链构建规则、链数、链长/分布、残余单体和端基处理；
- 力场来源、未覆盖原子类型的 DFT 参数化与验证；
- 初始密度、退火/压缩过程、温度、压力、时间步长、平衡时间和独立重复；
- 密度、能量、体积和链构象的收敛判据。

启动后建议先做小规模可重复性试验，再计算密度、Tg、回转半径、内聚能密度、氢键统计和软硬段空间关联。RadonPy 提供了全原子聚合物物性自动计算与数据组织的公开参考[145,146]，PolyOmics 提供可迁移的 PU/PURA 计算数据背景[151]；但这些来源不是本候选的实验标定，不能自动赋予 TPU 宏观力学预测可信度。

非平衡拉伸 MD 的应变率通常远高于实验测试；若后续使用，只能在相同协议内作相对排序，并同时报告尺寸、应变率、重复数和不确定度。不得把单条高速 MD 曲线直接写成实验拉伸曲线。

### 4.1 现实候选整数计量计划

`计算/现实MD/低聚链计量计划.csv`已把12条高层候选的目标硬段质量分数转成宏二醇、扩链剂和二异氰酸酯的整数数目。搜索先要求实现值与目标值的绝对差不高于0.015，再优先选择总构件数最少的方案，避免为微小计量误差构造不必要的超长链。当前12条全部通过该门，代理链估计为198–1564个原子。

单链骨架固定为一个NCO端和一个OH端的1:1交替连接。对于`NCO/OH=1.02`的配方，2% NCO过量作为批次/多链分布上下文单列保存，不通过51个以上二醇单元的超长单链伪造精确比例。该计量计划仍是`single_sequence_oligomer_proxy`；PTMG商品分布、CoA、力场覆盖、部分电荷、多链端基比例和盒模型未闭合，因此`md_execution_status`继续阻断。

`计算/现实MD/低聚链化学图.csv.gz`进一步按未反应NCO与OH的指定原子索引逐步形成氨基甲酸酯键。12条二维图均通过RDKit价态/芳香性规范化和SMILES回读；每条恰好保留1个NCO端、1个OH端，氨基甲酸酯键数为`2×二异氰酸酯数−1`，原子数与构件计量求和一致。该产物只证明确定性二维连接规则闭合，`three_dimensional_status=not_generated`、`forcefield_status=not_parameterized`，不能据此启动MD或宣称形貌/性能。

`计算/现实MD/三维种子清单.csv`用固定种子ETKDGv3随机坐标模式为12条二维图生成单构象三维种子，12/12嵌入成功。MMFF94s最多运行1000步，只对最小的198原子链收敛；其余11条以`mmff_max_iterations_seed`保留，不能把达到步数上限解释成力场最小值。三维种子仅用于下一步GFN-FF/正式力场预优化的坐标起点，不是已打包、已平衡或可生产MD的结构。

当前Linux xTB 6.7.1二进制的GFN-FF尺寸烟雾结果冻结在`计算/现实MD/GFNFF尺寸烟雾审计.csv`：198和489原子案例正常收敛，867、991、1118、1259和1564原子案例均在`xtb_gfnff_neighbor`初始化处发生SIGSEGV并无优化结构。故本环境生产门暂定不超过489原子；490–866原子区间未测试，不外推为安全。该门是二进制/环境特定证据，不是GFN-FF方法的普遍尺寸极限；超限链保留ETKDG/MMFF种子并等待替代预优化和生产力场方案。

Ubuntu上另建了隔离`/opt/tpu-md-venv`，固定RadonPy develop提交`5d14893515376a4518e9f1373a1ebc4bb756db14`、RadonPy 1.0b2 wheel SHA-256 `446cb1a94d92a758a162c71f0bfbce3d70e4bc308cd9e8e7b6ff1a8a448a2cba`、LAMMPS 2025.7.22.4.0和MPICH 4.2.0；LAMMPS Python最小启动通过。环境清单和完整pip冻结见`计算/现实MD/环境/`。

`计算/现实MD/GAFF2审计/GAFF2参数覆盖审计.csv`显示12/12低聚链均可由RadonPy GAFF2生成原子、键、角、二面角和improper参数，全部原子获得Gasteiger电荷，电荷和绝对误差不高于约`4.3e-15 e`。然而每条链都出现34–39类独特替代参数提示，主要涉及氨基甲酸酯`ns/cg`相关键、角、二面角和improper。GAFF2分配成功只证明拓扑文件可生成；Gasteiger电荷和替代参数未经过本TPU体系的DFT/实验验证，故`production_md_permission`继续阻断。

最小198原子商业对照完成RadonPy→LAMMPS数据导出和共轭梯度能量最小化烟雾。第一次尝试因未显式调用`make_dat()`而在读数据前退出，保留失败证据；修正后的独立尝试运行1189步并以能量容差停止，势能由约103.01降至48.77 kcal mol⁻¹，最终力二范数约0.261。该结果只证明文件和执行链路可用，不验证GAFF2替代参数、电荷、密度或任何TPU性质；完整输入/日志/终态哈希见`计算/现实MD/LAMMPS烟雾_最小_尝试2/烟雾清单.json`。

在同一最小商业对照上继续构建2条链、396原子的周期盒，RadonPy `poly.amorphous_cell`初始密度设为0.20 g cm⁻³。LAMMPS内部输入及文件名限定ASCII，避免程序对非ASCII输入标记的错误替换。最终发布尝试使用共轭梯度最多20,000步，实际运行9,042步后按LAMMPS能量容差停止；势能由约171.81降至48.11 kcal mol⁻¹，力二范数由约282.10降至0.723。随后在300 K、0.5 fs步长下完成2,000步（1.0 ps）NVT，396个原子、两个正常`Loop time`、返回码0且无`ERROR:`。完整输入、日志、终态、重启文件和SHA-256见`计算/现实MD/LAMMPS烟雾_多链_尝试3/多链烟雾发布清单.json`。

该多链结果仍是低密度执行链烟雾，不是密度平衡：盒体积在NVT中固定，初始密度0.20 g cm⁻³不能解释为TPU预测密度；1 ps也远不足以消除链构象记忆。虽然LAMMPS按能量容差停止，最终力未达到脚本设置的严格力容差，因此不能把终态称为高精度优化结构。39类聚氨酯相关GAFF2替代参数及Gasteiger电荷门继续阻断生产MD。下一步只有在参数/电荷协议获得文献、DFT小模型和商业对照验证后，才能设计低密度压缩、退火、NPT密度平衡和独立重复。

`计算/现实MD/参数验证/`已把12条链的替代消息展开为70类唯一映射：51类涉及重复氨基甲酸酯`ns`类型，19类涉及残余异氰酸酯端基的`cg/ch`共轭类型；按参数类别为14类键、24类角、28类二面角和4类improper。替代事件数与估计氨基甲酸酯键数的Pearson相关系数为0.991303，因此这不是少数末端警告，而是随主链长度系统累积的P0参数风险。完整逐类型、逐配方表及输入/输出哈希见`参数门发布清单.json`。

电荷路线建立了独立`/opt/tpu-resp-env`，固定Psi4 1.10.2[182]、RESP 1.0.0[183,184]、LibXC 7.0.0、Python 3.12.14和RadonPy 1.0b2。原生Psi4/RESP在`COC(=O)NC`模型、单一MMFF构象、HF/6-31G(d)、VDW缩放1.4/1.6/1.8/2.0和点密度1.0下完成两阶段拟合；第二阶段电荷和误差约`2.4×10^-17 e`，阶段间RESP RMS差约0.00693 e。结果只证明原生片段级路线可运行。RadonPy包装器硬编码点密度20，分别在4线程/4 GB、1线程/4 GB和1线程/16 GB下均于第一阶段GRID_ESP发生段错误；失败证据见`计算/现实MD/RESP环境/RadonPy_RESP失败审计.json`。因此当前电荷门为`native_two_stage_resp_ready...fragment_transfer_validation_pending`，尚未放行整链电荷转移、密度平衡或性能计算。

随后按同一协议补充`COC(=O)Nc1ccccc1`、`CCN=C=O`和`O=C=Nc1ccccc1`，与前述模型共同覆盖脂肪族/芳香族氨基甲酸酯及脂肪族/芳香族异氰酸酯端基四个家族、57个原子。四项均完成两阶段RESP，最大绝对电荷和误差约`2.36×10^-16 e`。芳香族异氰酸酯初次因奇异线性方程失败，正式重跑采用与RadonPy防护一致的条件数检查和最小二乘回退；失败记录留在服务器。`计算/现实MD/RESP片段验证/`汇总逐片段及逐原子结果。当前每个家族只有一个MMFF构象，故下一门仍是多构象/多取向联合拟合、点密度敏感性与片段到完整TPU链的等价原子约束。

构象/点密度敏感性矩阵进一步覆盖4个家族×3个种子×点密度0.5/1.0/2.0，共36个独立两阶段RESP任务和513条逐原子电荷，失败0。固定点密度时，核心原子跨构象最大样本标准差为0.04729 e；固定构象改变点密度时最大样本标准差为0.23270 e、全矩阵核心原子最大范围为0.46382 e，最敏感位置是芳香族氨基甲酸酯N。点密度影响显著大于构象种子影响，故后续协议固定RESP插件标准点密度1.0，不混合不同点密度电荷。服务器原始网格的确定性归档SHA-256为`81dff9418d34babab2811ce309389a5ce597db9a2faee7158002de1979b02cb6`；Git只保存`计算/现实MD/RESP敏感性/`紧凑汇总。

在点密度1.0下，四个家族又分别将三个构象的ESP等权放入同一线性系统，共同拟合一组共享原子电荷，而不是简单平均三套独立电荷。四项联合拟合均完成；联合电荷相对三个独立拟合均值的核心原子最大绝对差为0.04058 e，最大电荷和误差约`1.73×10^-15 e`。紧凑结果见`计算/现实MD/RESP联合验证/`，服务器原始归档SHA-256为`523d891d7b1abf0c266234548eed9f053ea6f8d9ed61c705108eb1b75e8dfb78`。电荷门已推进到`joint_multiconformer_fragment_resp_ready_polymer_transfer_validation_pending`；完整TPU链的等价原子映射、总电荷/局部偶极验证以及P0二面角势能仍阻断生产MD。

`计算/现实MD/RESP核心转移/`将四家族联合电荷按`OC(=O)N`和`N=C=O`角色映射回12条现实低聚链，并依据N外部重原子是否芳香选择脂肪族或芳香族参数。共识别134个氨基甲酸酯和12个残余NCO，与整数计量计划逐链完全一致；572条核心原子映射无重叠。核心仅覆盖各链11.68%–19.08%的重原子，联合RESP与整链Gasteiger在映射原子上的最大差为0.51381 e，主要来自异氰酸酯碳。该差异说明Gasteiger不能升级为生产电荷，也不能在不做完整电荷闭合的情况下把核心RESP直接写入LAMMPS。电荷门现为`joint_fragment_core_mapping_completed_full_chain_charge_assignment_pending`。

力场P0门使用完全相同的刚性几何，对脂肪族与芳香族`O=C–N–R`扭转分别扫描-180°至165°、15°间隔24点，同时计算ωB97M-D3BJ/6-31G(d,p)与GAFF2+三构象联合RESP总势能。脂肪族DFT/GAFF2刚性势垒为23.27/22.56 kcal mol⁻¹，最低点均为0°、Pearson r=0.939、曲线RMSE=3.17 kcal mol⁻¹，保留为条件参考。芳香族DFT/GAFF2势垒为19.97/40.25 kcal mol⁻¹，势垒高估20.28 kcal mol⁻¹、r=0.543、RMSE=9.62 kcal mol⁻¹，明确触发项目失败门。原始归档SHA-256为`483f4e1eb53912da8fb65840300bb8210ff0cce3e05a6f04f3725d96ba420f68`，紧凑结果见`计算/现实MD/氨基甲酸酯刚性扫描/`。刚性扫描不是松弛参数化，但已足以禁止芳香族体系沿用当前GAFF2替代参数进入生产MD；下一步按Psi4/OptKing冻结二面角协议[185]对8个信息互补角度松弛其余自由度。

`计算/现实MD/氨基甲酸酯扭转修正/`对刚性同几何残差先做±角度对称平均，再用`cos(nφ)`、阶数1–6逐绝对角留一交叉验证。脂肪族与芳香族分别拟合均选择6阶，修正后完整刚性曲线RMSE分别降至0.240和0.905 kcal mol⁻¹、相关系数为0.9995和0.9907；芳香族留一RMSE仍为1.99 kcal mol⁻¹。若两家族共用同一个3阶修正，脂肪/芳香完整曲线RMSE为5.78/3.81 kcal mol⁻¹，证明当前原子类型必须区分脂肪与芳香环境。该拟合只筛查参数形式，未使用冻结二面角松弛面，也未做独立片段外部验证，系数不得直接写入生产力场。

MM侧按LAMMPS `fix restrain dihedral`与`fix_modify energy yes`协议[186]对相同8个角度做K=5000 kcal mol⁻¹ rad⁻²约束最小化，解除约束后`run 0`读取GAFF2能量。RDKit与LAMMPS二面角定义存在180°偏移，输入显式使用`LAMMPS phi0=wrap(RDKit angle+180°)`；修正前烟雾的179.97°漂移作为失败证据保留。正式8点全部完成，最大角漂移0.195°。脂肪族松弛相对能在0/−180/−150/−90°为0/4.62/6.53/23.98 kcal mol⁻¹；芳香族在0/150/−180/−90°为0/7.49/7.71/14.46 kcal mol⁻¹。相对刚性曲线最大松弛变化32.55 kcal mol⁻¹，证明同约束松弛比较是必要的。原始归档SHA-256为`bac2942d12470cd2fe9e0ec320d2f8dbd547e978eddcc08d4e8db631739dad1b`，紧凑结果见`计算/现实MD/氨基甲酸酯MM约束松弛/`。

DFT冻结二面角松弛v1有5/8点QCHEM收敛；3个高能点达到默认50步上限，原始失败清单和归档SHA-256 `70ef7ea01dd0c093a5f038bc6d80360bfb2583d8e0838cad3b62ac80d038db3d`继续保留。v2禁止覆盖v1成功点，只对脂肪族−90°、芳香族−180/−90°采用Psi4/OptKing困难优化建议[185]：`dynamic_level=1`、`opt_coordinates=BOTH`、初始步长上限0.1 au和200步上限。三个重试全部收敛，最终8/8点最大角漂移0.00058°；v2原始归档SHA-256为`064b5638dd1633085f618501c655406117827c445139e0ac6853dbe7036ea0ccb`，`重试审计.csv`同时保留11条逐尝试记录和最终选择。

8个同角度DFT/MM松弛点全部可比较后，GAFF2相对能最大误差为脂肪族12.22、芳香族5.17 kcal mol⁻¹，所有非零训练角仍为GAFF2高于DFT。四点/家族只允许拟合在0°严格归零的`cos(nφ)-1`一至二阶修正：脂肪族选择二阶，训练RMSE、最大训练误差和逐角留一RMSE为0.475、0.726和2.679 kcal mol⁻¹；芳香族选择一阶，对应0.199、0.302和0.352 kcal mol⁻¹。系数状态为`external_validation_pending`，不直接写入GAFF2。

外部验证使用乙基N-乙基氨基甲酸酯和乙基N-(4-甲基苯基)氨基甲酸酯，每个片段计算−180/−120/−60/0/60/120°六个同角度DFT/MM点。两个外部片段的三构象联合RESP和12个MM点已完成，MM最大角漂移0.165°；12个DFT点正在运行。预声明门及失败处置见[扭转参数外部验证计划](扭转参数外部验证计划.md)，外部失败时不得通过提高四点训练阶数规避。

`计算/现实MD/现实链扭转候选映射/`已在不改写力场的前提下把低阶候选定位到12条现实链。逐个`OC(=O)N`使用羰基O–羰基C–N–N外部重原子作为目标四原子，并依据N外部原子芳香性选择家族；134个实例与整数计量和RESP核心结果完全一致，分为77个脂肪族和57个芳香族，产生211条候选周期项映射。该步骤只证明参数候选可以无歧义地定位到目标扭转，不证明系数可迁移，也未修改GAFF2或LAMMPS输入。

首轮凝聚相校准只预注册两套0.45硬段商业对照：MDI/PTMG-1000/BDO和IPDI/PTMG-1000/BDO，各3个独立重复、约10,000原子。计划与固定种子见`计算/现实MD/商业对照MD计划/`；温度、阶段、收敛和失败报告规则见[商业对照生产MD预注册协议](商业对照生产MD预注册协议.md)。当前状态是`planned_not_executable_parameter_and_batch_gates`，不提前创建被生产参数门禁止的LAMMPS输入。

长DFT扫描必须启用逐角检查点；新提交任务还应在提交前冻结`--单点墙钟秒`。检查点烟雾见`计算/现实MD/DFT检查点烟雾/`：脂肪族0°在600秒上限内完成，检查点记录1完成、0失败、0剩余并由最终清单哈希覆盖。墙钟超时是失败状态，不得当作缺失值从汇总中删除。

`计算/现实MD/混合电荷诊断/`固定572个已验证RESP核心原子，对其余8514个原子保留整链Gasteiger起点并施加满足总电荷为0的最小L2均匀修正。12条链共9086个原子均达到数值中性，最大总电荷残差约`6.94×10^-18 e`，未映射原子最大均匀修正仅0.00253 e；然而相同ETKDG/MMFF坐标上的点电荷偶极变化最高71.10 D。该结果表明小的逐原子中性化修正会沿长链产生显著整体静电变化，总电荷闭合不能替代化学等价约束、局部偶极和片段边界验证。混合电荷只作缺口诊断，不写入LAMMPS。

## 5. 从计算到实验的决策门

建议按以下顺序缩小候选：

1. 48 条结构多样性队列完成 Tier 0，留下身份、路线、SDS/EHS 和可得性均可复核者；
2. 对唯一构件完成 Tier 1，按收敛状态、构象复杂度和局部反应性代理排除异常者；
3. 对不超过 12 个配方做成对反应路径和真实宏二醇身份闭合；
4. 对不超过 6 个配方开展多重复 MD；
5. 综合结构新颖性、计算不确定度、原料安全和实验可执行性，形成 5–10 个实验短名单；
6. 合成后用 GPC、FTIR/NCO 转化、DSC/DMA、完整拉伸与循环曲线验证，并把真实批次写入 Gold-E，而不是回写虚拟候选标签。

## 6. 参考文献

CREST结束后的逐构象单点命令、JSON字段、Boltzmann代理权重和NCO/OH位点定义，统一采用[xTB系综描述符实施方案](xTB系综描述符实施方案.md)，不得另写一套不兼容口径。

[145] Hayashi, Y.; RadonPy Consortium. *RadonPy PI1070 Computational Polymer Dataset*, commit `840dd4a2b5f261fc9370bb6786eff0b71a463d2f`; GitHub, 2022. https://github.com/RadonPy/RadonPy/tree/840dd4a2b5f261fc9370bb6786eff0b71a463d2f/data.

[146] Hayashi, Y.; Shiomi, J.; Morikawa, J.; Yoshida, R. RadonPy: Automated Physical Property Calculation Using All-Atom Classical Molecular Dynamics Simulations for Polymer Informatics. *npj Computational Materials* **2022**, *8*, 222. https://doi.org/10.1038/s41524-022-00906-4.

[151] Hayashi, Y. *PolyOmics* [Data set]; Hugging Face, 2026. https://doi.org/10.57967/hf/7475.

[177] Bannwarth, C.; Ehlert, S.; Grimme, S. GFN2-xTB—An Accurate and Broadly Parametrized Self-Consistent Tight-Binding Quantum Chemical Method with Multipole Electrostatics and Density-Dependent Dispersion Contributions. *Journal of Chemical Theory and Computation* **2019**, *15* (3), 1652–1671. https://doi.org/10.1021/acs.jctc.8b01176.

[178] Pracht, P.; Bohle, F.; Grimme, S. Automated Exploration of the Low-Energy Chemical Space with Fast Quantum Chemical Methods. *Physical Chemistry Chemical Physics* **2020**, *22* (14), 7169–7192. https://doi.org/10.1039/C9CP06869D.

[179] Grimme, S.; Hansen, A.; Ehlert, S.; Mewes, J.-M. r2SCAN-3c: A “Swiss Army Knife” Composite Electronic-Structure Method. *The Journal of Chemical Physics* **2021**, *154* (6), 064103. https://doi.org/10.1063/5.0040021.

[180] Pracht, P.; Grimme, S.; Bannwarth, C.; et al. CREST—A Program for the Exploration of Low-Energy Molecular Chemical Space. *The Journal of Chemical Physics* **2024**, *160* (11), 114110. https://doi.org/10.1063/5.0197592.

[181] Neese, F. Software Update: The ORCA Program System—Version 6.0. *Wiley Interdisciplinary Reviews: Computational Molecular Science* **2025**, *15* (2), e70019. https://doi.org/10.1002/wcms.70019.

[182] Smith, D. G. A.; Burns, L. A.; Simmonett, A. C.; et al. Psi4 1.4: Open-Source Software for High-Throughput Quantum Chemistry. *The Journal of Chemical Physics* **2020**, *152* (18), 184108. https://doi.org/10.1063/5.0006002.

[183] Bayly, C. I.; Cieplak, P.; Cornell, W. D.; Kollman, P. A. A Well-Behaved Electrostatic Potential Based Method Using Charge Restraints for Deriving Atomic Charges: The RESP Model. *The Journal of Physical Chemistry* **1993**, *97* (40), 10269–10280. https://doi.org/10.1021/j100142a004.

[184] Alenaizan, A.; Burns, L. A.; Sherrill, C. D. Python Implementation of the Restrained Electrostatic Potential Charge Model. *International Journal of Quantum Chemistry* **2020**, *120* (2), e26035. https://doi.org/10.1002/qua.26035.

[185] Psi4 Project. *Geometry Optimization: Frozen Dihedral Constraints*, Psi4 1.10.x Manual. https://psicode.org/psi4manual/1.10.x/optking.html (accessed 2026-08-25).

[186] LAMMPS Developers. *fix restrain command: Dihedral Restraints and Energy Minimization*. https://docs.lammps.org/latest/fix_restrain.html (accessed 2026-08-25).
