#!/usr/bin/env node
/**
 * Figshare 化学辅助源审计器。
 *
 * 运行依赖：Node.js 与 @oai/artifact-tool（已按 2.8.24 API 验证）。
 * 若依赖不能由常规 Node 包解析找到，可将 ARTIFACT_TOOL_NODE_MODULES
 * 指向包含 @oai/artifact-tool 的 node_modules 目录。
 *
 * 本脚本只读取两个来源的官方元数据和原始工作簿，并只覆盖各自目录中的
 * `内容审计摘要.json`、`文件校验清单.tsv`、`工作表解析清单.tsv`。
 */

import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

async function loadArtifactTool() {
  try {
    return await import("@oai/artifact-tool");
  } catch (primaryError) {
    const nodeModules = process.env.ARTIFACT_TOOL_NODE_MODULES;
    if (!nodeModules) {
      throw new Error(
        "缺少 @oai/artifact-tool。请安装依赖，或设置 ARTIFACT_TOOL_NODE_MODULES 指向其 node_modules 目录。",
        { cause: primaryError },
      );
    }
    const resolver = createRequire(
      pathToFileURL(path.join(path.dirname(path.resolve(nodeModules)), "artifact-tool-resolver.cjs")),
    );
    const entry = resolver.resolve("@oai/artifact-tool");
    return import(pathToFileURL(entry).href);
  }
}

const { FileBlob, SpreadsheetFile } = await loadArtifactTool();
const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, "../..");
const shpuDirectory = path.join(
  projectRoot,
  "01_原始数据/外部数据/新增开放数据/Figshare_自愈离子胶黏PU源数据",
);
const castorDirectory = path.join(
  projectRoot,
  "01_原始数据/外部数据/新增开放数据/Figshare_蓖麻油脂肪族PU化学性能",
);
const shpuWorkbookPath = path.join(shpuDirectory, "Source Data.xlsx");
const shpuMetadataPath = path.join(shpuDirectory, "官方元数据.json");
const castorMetadataPath = path.join(castorDirectory, "官方元数据.json");
const AUDIT_OUTPUTS = new Set(
  [
    path.join(shpuDirectory, "内容审计摘要.json"),
    path.join(shpuDirectory, "文件校验清单.tsv"),
    path.join(shpuDirectory, "工作表解析清单.tsv"),
    path.join(castorDirectory, "内容审计摘要.json"),
    path.join(castorDirectory, "文件校验清单.tsv"),
  ].map((value) => path.resolve(value)),
);

const SHEET_SEMANTICS = {
  "Fig. 2a": ["材料力学", 3, "PE10/GY3、PE10/GY5、PE10/GY7 三组应力-应变序列"],
  "Fig. 2b": ["材料离子传输", 1, "1000/T-log(σ) 序列"],
  "Fig. 2e": ["材料自愈力学", 4, "Pristine 与 6/12/24 h healing 四组应力-应变序列"],
  "Fig. 2f": ["材料循环力学", 2, "首次加载与 1 h 后二次加载两组序列"],
  "Fig. 2g": ["材料介电性能", 3, "PDMS、VHB、SHPU 三组频率-介电常数序列"],
  "Fig. 2i": ["材料电阻动态", 1, "时间-电阻序列"],
  "Fig. 2k": ["材料应变传感", 2, "理论与实验电阻-应变序列"],
  "Fig. 3a": ["界面几何", 1, "二维界面轮廓坐标；不是材料样本"],
  "Fig. 3e": ["界面韧性端点", 3, "SHPU、VHB 4950、Sylgard 184 的平均值/标准差"],
  "Fig. 4c": ["器件等效电路", 3, "Cef、Cac、Cedl 三组频率-电容序列"],
  "Fig. 4g": ["器件释放", 2, "粗糙/光滑表面两组电压-释放时间条件族"],
  "Fig. 5b": ["器件粘附", 2, "玻璃/铝两组电压-法向力条件族"],
  "Fig. 5c": ["器件粘附", 12, "法向/剪切两模式乘六类基底的端点组；基底不是新 PU 配方"],
  "Fig. 5d": ["界面摩擦端点", 6, "六类基底的粗糙度、法向压力与静摩擦系数"],
  "Fig. 5f": ["器件自愈粘附", 4, "Pristine/Self-healed 乘铝/玻璃四组端点，并含泄漏电流"],
  "Fig. 5g": ["器件接近传感", 2, "金属/玻璃两组距离-归一化电容序列"],
  "Fig. 5h": ["器件动态传感", 1, "时间-归一化电容变化序列"],
  "Fig. 5j": ["器件角度传感", 1, "角度-归一化电容变化序列"],
};

const UNIT_PATTERNS = [
  ["%", /%/],
  ["MPa", /\bMPa\b/i],
  ["kPa", /\bkPa\b/i],
  ["Pa", /(?:^|\s)Pa(?:$|\s|\))/i],
  ["K^-1", /K\s*[-−]?1\b/i],
  ["S cm^-1", /\bS\s*cm\s*[-−]?1\b/i],
  ["Hz", /\bHz\b/i],
  ["kΩ", /\bk\s*(?:Ω|ohm)\b/i],
  ["Ω", /(?:^|\s)(?:Ω|ohm)(?:$|\s|\))/i],
  ["J/m²", /\bJ\s*\/\s*m(?:2|²)\b/i],
  ["F", /(?:^|\s)F(?:$|\s|\))/],
  ["kV", /\bkV\b/i],
  ["V", /(?:^|\s)V(?:$|\s|\))/],
  ["nA", /\bnA\b/i],
  ["mA", /\bmA\b/i],
  ["A", /(?:^|\s)A(?:$|\s|\))/],
  ["N", /(?:^|\s)N(?:$|\s|\))/],
  ["mm", /\bmm\b/i],
  ["cm", /\bcm\b/i],
  ["μm", /\b(?:μm|um)\b/i],
  ["s", /(?:^|\s)s(?:$|\s|\))/],
  ["min", /\bmin\b/i],
  ["h", /(?:^|\s)h(?:$|\s|\))/i],
  ["°", /°/],
];

function digest(bytes, algorithm) {
  return crypto.createHash(algorithm).update(bytes).digest("hex");
}

function textOf(value) {
  if (value === null || value === undefined) return "";
  if (value instanceof Date) return value.toISOString();
  return String(value).trim();
}

function tsvEscape(value) {
  return String(value ?? "")
    .replaceAll("\t", " ")
    .replaceAll("\r", " ")
    .replaceAll("\n", " ");
}

async function overwriteAuditFile(filePath, content) {
  const resolved = path.resolve(filePath);
  if (!AUDIT_OUTPUTS.has(resolved)) {
    throw new Error(`拒绝写入白名单以外路径：${resolved}`);
  }
  const parent = path.dirname(resolved);
  const realParent = await fs.realpath(parent);
  if (path.normalize(realParent).toLowerCase() !== path.normalize(parent).toLowerCase()) {
    throw new Error(`拒绝通过重解析目录写入审计输出：${parent}`);
  }
  try {
    const current = await fs.lstat(resolved);
    if (current.isSymbolicLink() || !current.isFile()) {
      throw new Error(`拒绝覆盖非普通文件或符号链接审计输出：${resolved}`);
    }
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  const temporary = path.join(
    parent,
    `.${path.basename(resolved)}.${process.pid}.${crypto.randomBytes(8).toString("hex")}.audit.tmp`,
  );
  let handle;
  try {
    handle = await fs.open(temporary, "wx", 0o600);
    await handle.writeFile(content, "utf8");
    await handle.sync();
    await handle.close();
    handle = undefined;
    await fs.chmod(temporary, 0o444);
    try {
      await fs.chmod(resolved, 0o666);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    await fs.rename(temporary, resolved);
    await fs.chmod(resolved, 0o444);
  } catch (error) {
    if (handle) await handle.close().catch(() => {});
    await fs.rm(temporary, { force: true }).catch(() => {});
    try {
      await fs.chmod(resolved, 0o444);
    } catch (chmodError) {
      if (chmodError?.code !== "ENOENT") throw chmodError;
    }
    throw error;
  }
}

const shpuWorkbookBytes = await fs.readFile(shpuWorkbookPath);
const shpuMetadata = JSON.parse(await fs.readFile(shpuMetadataPath, "utf8"));
const castorMetadata = JSON.parse(await fs.readFile(castorMetadataPath, "utf8"));
const officialShpuFile = shpuMetadata.files.find((file) => file.name === "Source Data.xlsx");
const officialCastorTable = castorMetadata.files.find((file) => file.name === "Table_1.xls");
if (!officialShpuFile || !officialCastorTable) {
  throw new Error("官方元数据缺少预期文件 Source Data.xlsx 或 Table_1.xls。", { cause: undefined });
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(shpuWorkbookPath));
const sheetAudits = [];

for (const sheet of workbook.worksheets.items) {
  const usedRange = sheet.getUsedRange(true);
  const values = usedRange?.values ?? [];
  const formulas = usedRange?.formulas ?? [];
  const rowCount = values.length;
  const columnCount = values.reduce((maximum, row) => Math.max(maximum, row?.length ?? 0), 0);
  let nonEmptyCells = 0;
  let finiteNumericCells = 0;
  let textCells = 0;
  let dateCells = 0;
  let booleanCells = 0;
  let formulaCells = 0;
  let formulaErrorTokens = 0;
  let blankCellsInsideUsedRectangle = 0;
  const units = new Set();
  const candidateLabels = new Set();
  const textPreview = [];
  const numericFrequency = new Map();

  for (let rowIndex = 0; rowIndex < rowCount; rowIndex += 1) {
    const row = values[rowIndex] ?? [];
    const formulaRow = formulas[rowIndex] ?? [];
    for (let columnIndex = 0; columnIndex < columnCount; columnIndex += 1) {
      const value = row[columnIndex];
      const formula = formulaRow[columnIndex];
      if (typeof formula === "string" && formula.trim()) formulaCells += 1;
      if (value === null || value === undefined || value === "") {
        blankCellsInsideUsedRectangle += 1;
        continue;
      }
      nonEmptyCells += 1;
      if (typeof value === "number" && Number.isFinite(value)) {
        finiteNumericCells += 1;
        const key = Number(value).toPrecision(12);
        numericFrequency.set(key, (numericFrequency.get(key) ?? 0) + 1);
      } else if (value instanceof Date) {
        dateCells += 1;
      } else if (typeof value === "boolean") {
        booleanCells += 1;
      } else {
        textCells += 1;
        const text = textOf(value);
        if (textPreview.length < 24 && text) textPreview.push(text.slice(0, 180));
        for (const [unit, pattern] of UNIT_PATTERNS) {
          if (pattern.test(text)) units.add(unit);
        }
        for (const match of text.matchAll(
          /\b(?:PE10\/GY[357]|SHPU|PDMS|VHB(?: 4950)?|Sylgard 184|Pristine|Self-healed|Aluminum|Glass|PVDF|Plywood|Paper)\b/gi,
        )) {
          candidateLabels.add(match[0]);
        }
        if (/^#(?:REF!|DIV\/0!|VALUE!|NAME\?|N\/A)/i.test(text)) formulaErrorTokens += 1;
      }
    }
  }

  const repeatedNumericCells = [...numericFrequency.values()]
    .filter((count) => count > 1)
    .reduce((sum, count) => sum + count, 0);
  const maximumNumericMultiplicity = numericFrequency.size
    ? Math.max(...numericFrequency.values())
    : 0;
  const [scientificLayer, semanticSeriesGroups, scientificInterpretation] =
    SHEET_SEMANTICS[sheet.name] ?? ["未分类", 0, "需人工复核"];

  sheetAudits.push({
    sheet_name: sheet.name,
    scientific_layer: scientificLayer,
    semantic_series_or_endpoint_groups: semanticSeriesGroups,
    scientific_interpretation: scientificInterpretation,
    used_rows: rowCount,
    used_columns: columnCount,
    nonempty_cells: nonEmptyCells,
    finite_numeric_cells: finiteNumericCells,
    text_cells: textCells,
    date_cells: dateCells,
    boolean_cells: booleanCells,
    formula_cells: formulaCells,
    formula_error_tokens: formulaErrorTokens,
    blank_cells_inside_used_rectangle: blankCellsInsideUsedRectangle,
    repeated_numeric_cells: repeatedNumericCells,
    max_numeric_value_multiplicity: maximumNumericMultiplicity,
    units_detected: [...units],
    candidate_labels: [...candidateLabels],
    text_preview: textPreview,
  });
}

const totalKeys = [
  "nonempty_cells",
  "finite_numeric_cells",
  "text_cells",
  "date_cells",
  "boolean_cells",
  "formula_cells",
  "formula_error_tokens",
  "blank_cells_inside_used_rectangle",
  "repeated_numeric_cells",
];
const totals = Object.fromEntries(totalKeys.map((key) => [key, 0]));
for (const sheet of sheetAudits) {
  for (const key of totalKeys) totals[key] += sheet[key];
}
const detectedUnits = [...new Set(sheetAudits.flatMap((sheet) => sheet.units_detected))];

const shpuMd5 = digest(shpuWorkbookBytes, "md5");
const shpuSha256 = digest(shpuWorkbookBytes, "sha256");
const shpuSummary = {
  audit_version: "figshare-chemistry-audit-v2",
  audited_at: "2026-07-20",
  source: {
    doi: "10.6084/m9.figshare.21716516.v1",
    title: shpuMetadata.title,
    authors: shpuMetadata.authors.map((author) => author.full_name),
    license: shpuMetadata.license,
    declared_type: shpuMetadata.defined_type_name,
    publication_date: shpuMetadata.published_date,
    official_file: {
      id: officialShpuFile.id,
      name: officialShpuFile.name,
      bytes: officialShpuFile.size,
      md5: officialShpuFile.computed_md5,
    },
  },
  local_file_verification: {
    name: "Source Data.xlsx",
    bytes: shpuWorkbookBytes.length,
    md5: shpuMd5,
    sha256: shpuSha256,
    byte_match: shpuWorkbookBytes.length === officialShpuFile.size,
    md5_match: shpuMd5 === officialShpuFile.computed_md5,
  },
  workbook: {
    sheet_count: workbook.worksheets.items.length,
    detected_units: detectedUnits,
    totals,
    sheets: sheetAudits,
  },
  scientific_unit_guardrail: {
    explicit_target_formulation_labels: ["PE10/GY3", "PE10/GY5", "PE10/GY7"],
    explicit_target_formulation_label_count: 3,
    selected_shpu_system_count_without_formula_resolution: 1,
    comparator_labels: ["PDMS", "VHB", "VHB 4950", "Sylgard 184"],
    note: "三个 PE10/GY 配方标签、所选 SHPU、对照材料、愈合状态、器件基底和图板之间存在不同科学层级；在论文方法映射完成前，不得将它们相加为独立 TPU 配方数。",
    observation_unit: "按配方/材料、试样与批次、语义曲线或端点组、曲线点四层登记；18 个工作表和数值单元格均不是材料样本数。",
  },
  admission: {
    domain: "辅助聚氨酯自愈介电层与离子粘附器件数据；不是常规 TPU 软段/硬段配方主训练集。",
    layer: "auxiliary_application_multitask",
    training_eligibility: "有条件准入：仅使用具有明确单位、材料/器件层级和条件标签的端点或曲线。",
    recommended_weight_cap: 0.2,
    leakage_group: "doi + SHPU chemistry family + device batch/figure panel；同一图的原始、作图、平均值和标准差层必须进入同一划分。",
  },
  risks: [
    "工作簿混合原始观测、理论值、作图整理、平均值和标准差；数值重复不等于独立重复试验。",
    "器件性能受几何、电压、基底、环境、负载和控制策略影响，不能直接映射为本体 TPU 力学标签。",
    "缺乏逐试样唯一 ID、批次与完整配方映射时，不能提升主 TPU 配方样本数。",
    "重复数值统计只用于定位潜在副本；任何删除操作都必须基于行列语义与图板映射。",
  ],
  references: [
    "Gao, D.; Thangavel, G.; Lee, J.; Lv, J.; Li, Y.; Ciou, J.-H.; Xiong, J.; Park, T.; Lee, P. S. Source Data.xlsx [Data set], version 1; Figshare, 2022. https://doi.org/10.6084/m9.figshare.21716516.v1.",
    "Gao, D.; Thangavel, G.; Lee, J.; Lv, J.; Li, Y.; Ciou, J.-H.; Xiong, J.; Park, T.; Lee, P. S. A Supramolecular Gel-Elastomer System for Soft Iontronic Adhesives. Nature Communications 2023, 14, 1990. https://doi.org/10.1038/s41467-023-37535-4.",
  ],
};

const castorTablePath = path.join(castorDirectory, "Table_1.xls");
let castorTableBytes = null;
let castorLocalMd5 = null;
let castorLocalSha256 = null;
try {
  const bytes = await fs.readFile(castorTablePath);
  castorTableBytes = bytes.length;
  castorLocalMd5 = digest(bytes, "md5");
  castorLocalSha256 = digest(bytes, "sha256");
} catch (error) {
  if (error?.code !== "ENOENT") throw error;
}

const castorSummary = {
  audit_version: "figshare-chemistry-audit-v2",
  audited_at: "2026-07-20",
  source: {
    doi: "10.6084/m9.figshare.14279117.v1",
    title: castorMetadata.title,
    authors: castorMetadata.authors.map((author) => author.full_name),
    license: castorMetadata.license,
    declared_type: castorMetadata.defined_type_name,
    official_file_count: castorMetadata.files.length,
    official_total_bytes: castorMetadata.files.reduce((sum, file) => sum + file.size, 0),
    table_file: {
      id: officialCastorTable.id,
      name: officialCastorTable.name,
      bytes: officialCastorTable.size,
      md5: officialCastorTable.computed_md5,
    },
  },
  local_state: {
    table_file_present: castorTableBytes !== null,
    local_table_bytes: castorTableBytes,
    local_table_md5: castorLocalMd5,
    local_table_sha256: castorLocalSha256,
    byte_match: castorTableBytes === officialCastorTable.size,
    md5_match: castorLocalMd5 === officialCastorTable.computed_md5,
    download_status: "failed_official_endpoints_returned_no_content_or_missing_object",
  },
  content_status: {
    parsed_workbooks: 0,
    independently_verified_material_samples: 0,
    note: "官方元数据列出 8 个 JPEG 论文图与 1 个 5,632 字节 XLS 表；本地 XLS 为 0 字节，不能从图像、摘要或论文端点反推原始试样数。",
  },
  scientific_scope_from_official_metadata_only: {
    polyurethane_domain: "蓖麻油衍生多元醇 + HDI/IPDI/HMDI 脂肪族或脂环族二异氰酸酯，含 PCL/壳聚糖短链聚合物；交联生物医用 PU，非 TPU。",
    reported_endpoints_not_locally_parsed: [
      "stress-strain",
      "maximum stress",
      "Young modulus",
      "elongation",
      "TGA",
      "DSC/Tg",
      "contact angle",
      "L-929 cell viability",
    ],
  },
  admission: {
    layer: "metadata_only_rejected_until_files_verified",
    current_training_weight: 0,
    recommended_weight_cap_after_recovery: 0.1,
    leakage_group: "doi + castor-oil polyol family + diisocyanate + additive state；同一配方的图和表必须位于同一划分。",
    reason: "官方内容对象当前不可下载且本地 Table_1.xls 为 0 字节，不满足内容可核验与逐试样溯源要求。",
  },
  references: [
    "Uscategui, Y. L.; Díaz, L. E.; Valero, M. F. Effect of the Addition of Short Chain Polymers on the Chemical Structure, Mechanical, Thermal and Biological Properties of Polyurethanes Synthesized with Aliphatic Diisocyanates and Castor Oil [Data set], version 1; Figshare, 2021. https://doi.org/10.6084/m9.figshare.14279117.v1.",
    "Uscátegui, Y. L.; Díaz, L. E.; Valero, M. F. Efecto de la Adición de Polímeros de Cadena Corta sobre la Estructura Química, Propiedades Mecánicas, Térmicas y Biológicas de Poliuretanos Sintetizados con Diisocianatos Alifáticos y Aceite de Higuerilla. Química Nova 2021, 44 (1), 48–57. https://doi.org/10.21577/0100-4042.20170643.",
  ],
};

const sheetHeaders = [
  "工作表",
  "科学层级",
  "语义曲线或端点组",
  "科学解释",
  "使用行数",
  "使用列数",
  "非空单元格",
  "有限数值单元格",
  "文本单元格",
  "日期单元格",
  "布尔单元格",
  "公式单元格",
  "公式错误标记",
  "矩形内空白",
  "重复数值单元格",
  "单值最大重复次数",
  "检测单位",
  "候选标签",
];
const sheetTsv = [
  sheetHeaders.join("\t"),
  ...sheetAudits.map((sheet) =>
    [
      sheet.sheet_name,
      sheet.scientific_layer,
      sheet.semantic_series_or_endpoint_groups,
      sheet.scientific_interpretation,
      sheet.used_rows,
      sheet.used_columns,
      sheet.nonempty_cells,
      sheet.finite_numeric_cells,
      sheet.text_cells,
      sheet.date_cells,
      sheet.boolean_cells,
      sheet.formula_cells,
      sheet.formula_error_tokens,
      sheet.blank_cells_inside_used_rectangle,
      sheet.repeated_numeric_cells,
      sheet.max_numeric_value_multiplicity,
      sheet.units_detected.join("|"),
      sheet.candidate_labels.join("|"),
    ]
      .map(tsvEscape)
      .join("\t"),
  ),
].join("\n") + "\n";

const fileHeaders = [
  "文件",
  "官方字节",
  "本地字节",
  "官方MD5",
  "本地MD5",
  "本地SHA256",
  "字节匹配",
  "MD5匹配",
  "状态",
];
const shpuFileTsv = [
  fileHeaders.join("\t"),
  [
    "Source Data.xlsx",
    officialShpuFile.size,
    shpuWorkbookBytes.length,
    officialShpuFile.computed_md5,
    shpuMd5,
    shpuSha256,
    shpuWorkbookBytes.length === officialShpuFile.size,
    shpuMd5 === officialShpuFile.computed_md5,
    "verified",
  ]
    .map(tsvEscape)
    .join("\t"),
].join("\n") + "\n";
const castorFileTsv = [
  fileHeaders.join("\t"),
  [
    "Table_1.xls",
    officialCastorTable.size,
    castorTableBytes,
    officialCastorTable.computed_md5,
    castorLocalMd5,
    castorLocalSha256,
    castorTableBytes === officialCastorTable.size,
    castorLocalMd5 === officialCastorTable.computed_md5,
    "download_failed_weight_0",
  ]
    .map(tsvEscape)
    .join("\t"),
].join("\n") + "\n";

await overwriteAuditFile(
  path.join(shpuDirectory, "内容审计摘要.json"),
  JSON.stringify(shpuSummary, null, 2) + "\n",
);
await overwriteAuditFile(path.join(shpuDirectory, "文件校验清单.tsv"), shpuFileTsv);
await overwriteAuditFile(path.join(shpuDirectory, "工作表解析清单.tsv"), sheetTsv);
await overwriteAuditFile(
  path.join(castorDirectory, "内容审计摘要.json"),
  JSON.stringify(castorSummary, null, 2) + "\n",
);
await overwriteAuditFile(path.join(castorDirectory, "文件校验清单.tsv"), castorFileTsv);

console.log(
  JSON.stringify(
    {
      project_root: projectRoot,
      shpu: {
        sheet_count: shpuSummary.workbook.sheet_count,
        totals: shpuSummary.workbook.totals,
        file_verified:
          shpuSummary.local_file_verification.byte_match &&
          shpuSummary.local_file_verification.md5_match,
      },
      castor: {
        local_table_bytes: castorSummary.local_state.local_table_bytes,
        current_training_weight: castorSummary.admission.current_training_weight,
      },
    },
    null,
    2,
  ),
);
