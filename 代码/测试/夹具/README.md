# 适配器最小夹具

这些夹具只保留验证源结构所需的最少记录，不是可用于建模的完整数据集。

| 夹具 | 来源文件 | 来源 SHA-256 | 取样位置与生成方式 |
|---|---|---|---|
| `smipoly_最小.csv` | `01_原始数据/基础数据/smipoly_monomers.csv` | `1E70860B89B492EB0E3272615DD7220B35719C6C770E1299269AEB359BE59713` | 复制两个具有相同原始 SMILES 的公开单体记录，用于验证重复组和“不推断 TPU 角色”约束。 |
| `pue326_最小.csv` | `01_原始数据/代码仓库镜像/DQ/experiment/datasets/PUE.csv` | `E5D07B13764089579F90F16FDA6A70024D67C683F5BDD41591720F9474308040` | 复制 `SS1` 与 `SS2` 两行，用于验证 24 列指纹和母记录谱系锁。 |
| `xlsx_最小结构.json` | `TPU_HBond_2021_Source_Main.xlsx`；`ViscTempData.xlsx` | `A71E665B656D471A59EA814D44C12D73A7F7E9C2D1E23044FFB6DC4B572BF8C4`；`BBEC41DA4FF3CAE1231686E5D219EC217AC53D85E0F5FC561E408E78FDAF7085` | 人工转录 `Figure 1b!A1:O4` 的结构化缩小值，以及 `P_44M_4!A1:B4`。测试运行时用 openpyxl 在临时目录生成 XLSX；不提交第三方二进制文件。 |

`Mystery` 工作表是测试专用的合成未知 sheet，用于证明适配器会生成审计记录而不是静默忽略。
