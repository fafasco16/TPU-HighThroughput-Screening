# GPU图神经网络基线报告

## 结论

本轮GNN在与Morgan-Ridge完全相同的冻结发布、开发划分、损失权重和测试对象上完成训练。四个内部测试目标的逐行加权RMSE均下降；唯一可估计的来源族留出（Open Polymer Challenge的`Rg`）也优于Ridge，并把R²从负值提高到接近零或小幅正值。因此，GNN可保留为后续候选结构表征的计算性质基线，但现阶段仍不能称为TPU实验性能模型，也不能据此直接宣布新材料体系。

## 运行与数据门禁

- 冻结发布：`tpu-usable-2026-07-21-v1`；
- 输入SHA-256：`0c8f5888034ca2d746063539a8780df284d6697e44b0ba71c698eaecfbba6918`；
- 图样本：26,176条；泄漏硬组：7,558个；
- train/validation/test：20,995/2,648/2,533；同组跨折数为0；
- 目标：`Rg`、`density`、`bulk_modulus`、`thermal_conductivity`；`Tg`未进入正式GNN；
- 节点/键特征：9维/4维；
- PyTorch：2.13.0+cu130；GPU：NVIDIA L40；
- 正式运行：3分08秒，退出码0，最大常驻内存约3.40 GiB；
- 产物：4个checkpoint、2,866条严格评估预测、20行指标；清单记录的checkpoint和输出SHA-256已全部复核通过。

`primary_only`只用主证据训练行；`primary_plus_aux`只在训练折加入模拟辅助行。两种模式的validation和test始终只含`primary_train`。目标标准化只在训练集拟合，validation负责早停和checkpoint选择，test只在checkpoint确定后评估一次。

## 与Morgan-Ridge的内部测试比较

下表采用逐行加权test口径；正的“RMSE降低”表示GNN优于Ridge。

| 训练模式 | 目标 | GNN RMSE | Ridge RMSE | RMSE降低 | GNN R² | Ridge R² |
|---|---|---:|---:|---:|---:|---:|
| primary_only | Rg | 1.790 | 1.920 | 6.8% | 0.322 | 0.220 |
| primary_only | bulk_modulus | 5.096e8 | 5.573e8 | 8.6% | 0.311 | 0.175 |
| primary_only | density | 0.01400 | 0.02800 | 50.0% | 0.956 | 0.824 |
| primary_only | thermal_conductivity | 0.01639 | 0.01797 | 8.8% | 0.636 | 0.562 |
| primary_plus_aux | Rg | 1.783 | 1.837 | 2.9% | 0.327 | 0.286 |
| primary_plus_aux | bulk_modulus | 5.070e8 | 5.478e8 | 7.4% | 0.318 | 0.203 |
| primary_plus_aux | density | 0.01140 | 0.02756 | 58.6% | 0.971 | 0.829 |
| primary_plus_aux | thermal_conductivity | 0.01636 | 0.01792 | 8.7% | 0.637 | 0.565 |

硬组宏平均口径结论一致：8个“训练模式×目标”组合的RMSE均优于Ridge。完整MAE、RMSE、R²和Spearman见`指标.csv`，不得只摘取density的高R²而省略其余目标。

## 来源族留出

当前只有Open Polymer Challenge的`Rg`达到完整来源族留出门槛，共106个独立硬组。

| 训练模式 | GNN RMSE | Ridge RMSE | RMSE降低 | GNN R² | Ridge R² | GNN Spearman | Ridge Spearman |
|---|---:|---:|---:|---:|---:|---:|---:|
| primary_only | 3.815 | 5.373 | 29.0% | 0.073 | -0.839 | 0.648 | 0.143 |
| primary_plus_aux | 3.949 | 4.697 | 15.9% | 0.007 | -0.406 | 0.637 | 0.290 |

这个结果说明图表征在该单一留出来源的`Rg`上比Morgan指纹Ridge更稳，但外推R²仍很低。辅助数据在内部测试有益，在来源留出上却弱于`primary_only` GNN；这提示辅助模拟分布与留出来源仍存在偏移，不能把“数据更多”直接等同于“外推更好”。

## 可用范围

GNN现在可以用于：

1. 给候选构件提供与传统Ridge独立的计算性质排序信号；
2. 在主动学习中识别Ridge与GNN分歧较大的候选；
3. 与xTB/DFT/MD描述符组成后续多保真模型的一个输入分支。

GNN现在不能用于：

1. 把单体或重复单元图直接解释成完整TPU配方、分子量和工艺；
2. 把Gold-C模拟标签写成Gold-E实验真值；
3. 从`Rg`、密度、体积模量或热导率直接推出拉伸强度、韧性、循环恢复或可合成性；
4. 把当前40条阶段配方Pareto写成最终材料排名。

后续应在最后两个CREST构件闭合后重算完整配方描述符，再用“小规模DFT/MD复核 + GNN/Ridge分歧 + 合成可行性门”共同选实验批次。

## 数据来源与参考文献

本轮26,176条图观测实际来自三个固定来源族。以下引用与`结果/可用数据集/来源与引用.csv`及`文档/数据来源与参考文献.md`中的引用键一致。

[1] Yoshida, R.; Hayashi, Y.; Furuya, H.; Hosoya, R.; Kaneko, K.; Sugisawa, H.; Kaneko, Y.; Takahashi, A.; Noguchi, Y.; Nanjo, S.; et al. Omics-Scale Polymer Computational Database Transferable to Real-World Artificial Intelligence Applications. *arXiv* **2025**, arXiv:2511.11626. https://doi.org/10.48550/arXiv.2511.11626.

[2] Hayashi, Y. PolyOmics [Data set]; Hugging Face, 2026. https://doi.org/10.57967/hf/7475. CC BY 4.0.

[3] Hayashi, Y.; RadonPy Consortium. RadonPy PI1070 Computational Polymer Dataset, commit `840dd4a2b5f261fc9370bb6786eff0b71a463d2f`; GitHub, 2022. https://github.com/RadonPy/RadonPy/tree/840dd4a2b5f261fc9370bb6786eff0b71a463d2f/data. BSD-3-Clause.

[4] Hayashi, Y.; Shiomi, J.; Morikawa, J.; Yoshida, R. RadonPy: Automated Physical Property Calculation Using All-Atom Classical Molecular Dynamics Simulations for Polymer Informatics. *npj Computational Materials* **2022**, *8*, 222. https://doi.org/10.1038/s41524-022-00906-4.

[5] Liu, A. NeurIPS - Open Polymer Prediction 2025 Test Data [Data set], version 1; Kaggle, 2025. https://www.kaggle.com/datasets/alexliu99/neurips-open-polymer-prediction-2025-test-data. MIT License.

[6] Liu, G.; Alosious, S.; Mahajan, S.; Inae, E.; Zhu, Y.; Liu, Y.; Zhang, R.; Xu, J.; Howard, A.; Li, Y.; Luo, T.; Jiang, M. Open Polymer Challenge: Post-Competition Report. *arXiv* **2025**, arXiv:2512.08896v1. https://arxiv.org/abs/2512.08896v1.

论文写作应引用原始数据集和方法论文；本报告只是可复现运行摘要，不替代原始来源。
