import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(SCRIPT_DIR, "..");
const OUTPUT_PATH = path.join(
  PROJECT_ROOT,
  "06_审核导出",
  "TPU数据库_v0.1_审核目录.xlsx",
);
const DEFAULT_PREVIEW_DIR = path.join(os.tmpdir(), "TPU数据库_v0.1_审核目录_预览");

const INPUTS = {
  manifest: path.join(PROJECT_ROOT, "清单", "来源清单.csv"),
  schema: path.join(PROJECT_ROOT, "结构定义", "v0.1字段字典.yaml"),
  sources: path.join(PROJECT_ROOT, "配置", "数据源.yaml"),
  qualityDir: path.join(PROJECT_ROOT, "文档", "质量报告"),
  snapshotDir: path.join(PROJECT_ROOT, "05_数据库快照"),
};

const COLORS = {
  navy: "#16324F",
  teal: "#0F766E",
  blue: "#2563EB",
  paleBlue: "#EAF2F8",
  paleTeal: "#E6F4F1",
  paleGray: "#F4F6F8",
  midGray: "#D7DEE5",
  text: "#1F2937",
  muted: "#5F6B76",
  white: "#FFFFFF",
  green: "#DCFCE7",
  greenText: "#166534",
  yellow: "#FEF3C7",
  yellowText: "#92400E",
  red: "#FEE2E2",
  redText: "#991B1B",
  info: "#DBEAFE",
  infoText: "#1E40AF",
};

function parseArguments(argv) {
  const prefix = "--预览目录=";
  const argument = argv.find((item) => item.startsWith(prefix));
  return {
    previewDir: argument
      ? path.resolve(PROJECT_ROOT, argument.slice(prefix.length))
      : DEFAULT_PREVIEW_DIR,
  };
}

function textValue(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function booleanOrBlank(value) {
  if (value === true || value === false) return value;
  const normalized = textValue(value).toLowerCase();
  if (normalized === "true") return true;
  if (normalized === "false") return false;
  return "";
}

function numberOrZero(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function dateOrText(value) {
  const normalized = textValue(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(normalized)) {
    return new Date(`${normalized}T12:00:00`);
  }
  return normalized;
}

function excelString(value) {
  return textValue(value).replaceAll('"', '""');
}

function columnLetter(index) {
  let number = index;
  let result = "";
  while (number > 0) {
    const remainder = (number - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    number = Math.floor((number - 1) / 26);
  }
  return result;
}

function splitFlowItems(value) {
  const items = [];
  let start = 0;
  let depth = 0;
  let quote = "";
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (quote) {
      if (character === quote && value[index - 1] !== "\\") quote = "";
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === "[" || character === "{") depth += 1;
    else if (character === "]" || character === "}") depth -= 1;
    else if (character === "," && depth === 0) {
      items.push(value.slice(start, index).trim());
      start = index + 1;
    }
  }
  items.push(value.slice(start).trim());
  return items.filter((item) => item !== "");
}

function yamlScalar(value) {
  const normalized = value.trim();
  if (normalized === "") return "";
  if (normalized === "null" || normalized === "~") return null;
  if (normalized === "true") return true;
  if (normalized === "false") return false;
  if (/^-?\d+(?:\.\d+)?$/.test(normalized)) return Number(normalized);
  if (
    (normalized.startsWith('"') && normalized.endsWith('"')) ||
    (normalized.startsWith("'") && normalized.endsWith("'"))
  ) {
    return normalized.slice(1, -1).replaceAll('\\"', '"').replaceAll("\\'", "'");
  }
  if (normalized.startsWith("[") && normalized.endsWith("]")) {
    return splitFlowItems(normalized.slice(1, -1)).map(yamlScalar);
  }
  if (normalized.startsWith("{") && normalized.endsWith("}")) {
    return Object.fromEntries(
      splitFlowItems(normalized.slice(1, -1)).map((item) => {
        const delimiter = item.indexOf(":");
        if (delimiter < 0) throw new Error(`无法解析 YAML 行内映射：${item}`);
        return [item.slice(0, delimiter).trim(), yamlScalar(item.slice(delimiter + 1))];
      }),
    );
  }
  return normalized;
}

function parseYamlSubset(text) {
  const lines = text
    .split(/\r?\n/)
    .map((raw) => ({
      indent: raw.match(/^ */)?.[0].length ?? 0,
      content: raw.trim(),
    }))
    .filter((line) => line.content !== "" && !line.content.startsWith("#"));

  function parseBlock(start, indent) {
    const isSequence = lines[start]?.indent === indent && lines[start].content.startsWith("- ");
    const container = isSequence ? [] : {};
    let index = start;
    while (index < lines.length) {
      const line = lines[index];
      if (line.indent < indent) break;
      if (line.indent > indent) {
        throw new Error(`YAML 缩进不符合预期（第 ${index + 1} 个有效行）：${line.content}`);
      }
      if (isSequence) {
        if (!line.content.startsWith("- ")) break;
        const itemText = line.content.slice(2).trim();
        if (itemText === "") {
          const [nested, nextIndex] = parseBlock(index + 1, lines[index + 1].indent);
          container.push(nested);
          index = nextIndex;
          continue;
        }
        const delimiter = itemText.indexOf(":");
        if (delimiter < 0) {
          container.push(yamlScalar(itemText));
          index += 1;
          continue;
        }
        const item = {};
        const key = itemText.slice(0, delimiter).trim();
        const rawValue = itemText.slice(delimiter + 1).trim();
        item[key] = rawValue === "" ? null : yamlScalar(rawValue);
        index += 1;
        if (index < lines.length && lines[index].indent > indent) {
          const [rest, nextIndex] = parseBlock(index, lines[index].indent);
          if (rest && !Array.isArray(rest)) Object.assign(item, rest);
          else if (rawValue === "") item[key] = rest;
          index = nextIndex;
        }
        container.push(item);
        continue;
      }

      if (line.content.startsWith("- ")) break;
      const delimiter = line.content.indexOf(":");
      if (delimiter < 0) throw new Error(`无法解析 YAML 映射：${line.content}`);
      const key = line.content.slice(0, delimiter).trim();
      const rawValue = line.content.slice(delimiter + 1).trim();
      if (rawValue !== "") {
        container[key] = yamlScalar(rawValue);
        index += 1;
        continue;
      }
      index += 1;
      if (index < lines.length && lines[index].indent > indent) {
        const [nested, nextIndex] = parseBlock(index, lines[index].indent);
        container[key] = nested;
        index = nextIndex;
      } else {
        container[key] = {};
      }
    }
    return [container, index];
  }

  if (lines.length === 0) return {};
  return parseBlock(0, lines[0].indent)[0];
}

async function readText(filePath) {
  return fs.readFile(filePath, "utf8");
}

async function sha256(filePath) {
  const content = await fs.readFile(filePath);
  return createHash("sha256").update(content).digest("hex");
}

async function csvObjects(csvText, sheetName) {
  const csvWorkbook = await Workbook.fromCSV(csvText, { sheetName });
  const range = csvWorkbook.worksheets.getItem(sheetName).getUsedRange(true);
  if (!range) return [];
  const matrix = range.values ?? [];
  if (matrix.length < 2) return [];
  const headers = matrix[0].map((value) => textValue(value));
  return matrix
    .slice(1)
    .filter((row) => row.some((value) => textValue(value) !== ""))
    .map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index]])));
}

async function listFiles(directory, extensions) {
  const result = [];
  async function walk(current) {
    let entries;
    try {
      entries = await fs.readdir(current, { withFileTypes: true });
    } catch (error) {
      if (error?.code === "ENOENT") return;
      throw error;
    }
    entries.sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) await walk(fullPath);
      else if (entry.isFile() && extensions.has(path.extname(entry.name).toLowerCase())) {
        result.push(fullPath);
      }
    }
  }
  await walk(directory);
  return result;
}

function normalizeManifest(rows) {
  return rows
    .map((row) => ({
      source_id: textValue(row.source_id),
      source_file_id: textValue(row.source_file_id),
      raw_path: textValue(row.raw_path),
      original_filename: textValue(row.original_filename),
      size_bytes: numberOrZero(row.size_bytes),
      sha256: textValue(row.sha256),
      doi: textValue(row.doi),
      url: textValue(row.url),
      accessed_at: dateOrText(row.accessed_at),
      license_spdx: textValue(row.license_spdx) || "UNKNOWN",
      derivatives_allowed: booleanOrBlank(row.derivatives_allowed),
      redistribution_allowed: booleanOrBlank(row.redistribution_allowed),
      evidence_grade: textValue(row.evidence_grade),
      material_scope: textValue(row.material_scope),
      status: textValue(row.status),
      notes: textValue(row.notes),
    }))
    .sort((left, right) => {
      const leftUnregistered = left.source_id === "raw_vault_unregistered" ? 1 : 0;
      const rightUnregistered = right.source_id === "raw_vault_unregistered" ? 1 : 0;
      return (
        leftUnregistered - rightUnregistered ||
        left.source_id.localeCompare(right.source_id, "en") ||
        left.raw_path.localeCompare(right.raw_path, "zh-CN") ||
        left.source_file_id.localeCompare(right.source_file_id, "en")
      );
    });
}

function flattenFieldDictionary(schemaDocument) {
  const rows = [];
  for (const [tableName, table] of Object.entries(schemaDocument.tables ?? {})) {
    const primaryKeys = new Set(table.primary_key ?? []);
    for (const [fieldName, field] of Object.entries(table.fields ?? {})) {
      rows.push([
        tableName,
        textValue(table.description),
        (table.primary_key ?? []).join(", "),
        fieldName,
        textValue(field.type),
        field.required === true,
        textValue(field.enum),
        textValue(field.description),
        primaryKeys.has(fieldName),
      ]);
    }
  }
  return rows;
}

function registeredSourceRows(sourceDocument) {
  return [...(sourceDocument.sources ?? [])]
    .sort((left, right) => textValue(left.source_id).localeCompare(textValue(right.source_id), "en"))
    .map((source) => [
      textValue(source.source_id),
      textValue(source.path),
      textValue(source.license_spdx) || "UNKNOWN",
      booleanOrBlank(source.derivatives_allowed),
      booleanOrBlank(source.redistribution_allowed),
      textValue(source.evidence_grade),
      textValue(source.material_scope),
      textValue(source.status),
    ]);
}

function normalizeIssue(issue, sourcePath) {
  const get = (...keys) => {
    for (const key of keys) {
      if (Object.hasOwn(issue, key) && issue[key] !== null && issue[key] !== undefined) {
        return issue[key];
      }
    }
    return "";
  };
  return [
    path.relative(PROJECT_ROOT, sourcePath).replaceAll("\\", "/"),
    textValue(get("severity", "严重程度", "level", "级别")) || "info",
    textValue(get("issue_code", "code", "问题代码")) || "UNSPECIFIED",
    textValue(get("table", "table_name", "表名")),
    textValue(get("record_id", "记录ID", "row_id")),
    textValue(get("field", "field_name", "字段")),
    textValue(get("message", "消息", "description", "问题描述")),
    booleanOrBlank(get("public_release_allowed", "可发布", "publishable")),
    textValue(get("source_locator", "来源定位", "locator")),
  ];
}

async function loadQualityIssues() {
  const reportFiles = await listFiles(INPUTS.qualityDir, new Set([".csv", ".json"]));
  const qualityFiles = reportFiles.filter((filePath) => path.basename(filePath).includes("质量问题"));
  const issues = [];
  for (const filePath of qualityFiles) {
    if (path.extname(filePath).toLowerCase() === ".csv") {
      const objects = await csvObjects(await readText(filePath), "质量导入");
      issues.push(...objects.map((issue) => normalizeIssue(issue, filePath)));
      continue;
    }
    const parsed = JSON.parse(await readText(filePath));
    const candidates = Array.isArray(parsed)
      ? parsed
      : Array.isArray(parsed.issues)
        ? parsed.issues
        : Array.isArray(parsed.quality_issues)
          ? parsed.quality_issues
          : [];
    issues.push(...candidates.map((issue) => normalizeIssue(issue, filePath)));
  }
  issues.sort(
    (left, right) =>
      left[1].localeCompare(right[1], "en") ||
      left[0].localeCompare(right[0], "zh-CN") ||
      left[2].localeCompare(right[2], "en") ||
      left[4].localeCompare(right[4], "en"),
  );
  if (issues.length === 0) {
    const reportsExist = qualityFiles.length > 0;
    issues.push([
      reportsExist
        ? qualityFiles.map((filePath) => path.relative(PROJECT_ROOT, filePath).replaceAll("\\", "/")).join("；")
        : "文档/质量报告",
      "info",
      reportsExist ? "NO_QUALITY_ISSUES" : "NO_ISSUES_FILE",
      "",
      "",
      "",
      reportsExist
        ? "QC 已运行，当前规则未发现错误或警告；这不等同于数据完备或论文已经具备发表条件。"
        : "当前未发现质量报告 CSV/JSON；待 Python 管道生成后重新运行本构建器。",
      "",
      "",
    ]);
  }
  return { issues, qualityFiles, reportFiles };
}

function coverageDescriptors(manifestRows) {
  const dataStart = 5;
  const dataEnd = Math.max(dataStart, dataStart + manifestRows.length - 1);
  const dataRange = {
    bytes: `'数据来源'!$E$${dataStart}:$E$${dataEnd}`,
    evidence: `'数据来源'!$M$${dataStart}:$M$${dataEnd}`,
    scope: `'数据来源'!$N$${dataStart}:$N$${dataEnd}`,
    status: `'数据来源'!$O$${dataStart}:$O$${dataEnd}`,
    layer: `'数据来源'!$Q$${dataStart}:$Q$${dataEnd}`,
    extension: `'数据来源'!$R$${dataStart}:$R$${dataEnd}`,
  };
  const descriptors = [];
  const add = (dimension, label, conditionRange, condition, note) => {
    const escaped = excelString(condition);
    descriptors.push({
      dimension,
      label,
      countFormula: `=COUNTIF(${conditionRange},"${escaped}")`,
      bytesFormula: `=SUMIF(${conditionRange},"${escaped}",${dataRange.bytes})`,
      note,
    });
  };

  for (const layer of ["基础数据", "代码仓库镜像", "外部数据", "仅供参考"]) {
    add("原始数据层", layer, dataRange.layer, layer, "按“数据来源”的派生层级列精确匹配");
  }
  const uniqueValues = (key) =>
    [...new Set(manifestRows.map((row) => textValue(row[key]) || "未标注"))].sort((a, b) =>
      a.localeCompare(b, "zh-CN"),
    );
  for (const value of uniqueValues("material_scope")) {
    add("材料范围", value, dataRange.scope, value === "未标注" ? "" : value, "来自来源清单 material_scope");
  }
  for (const value of uniqueValues("evidence_grade")) {
    add("证据等级", value, dataRange.evidence, value === "未标注" ? "" : value, "来自来源清单 evidence_grade");
  }
  for (const value of uniqueValues("status")) {
    add("来源状态", value, dataRange.status, value === "未标注" ? "" : value, "来自来源清单 status");
  }

  const extensionCounts = new Map();
  for (const row of manifestRows) {
    const extension = path.extname(row.original_filename).toLowerCase() || "[无扩展名]";
    extensionCounts.set(extension, (extensionCounts.get(extension) ?? 0) + 1);
  }
  const topExtensions = [...extensionCounts.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], "en"))
    .slice(0, 12)
    .map(([extension]) => extension);
  for (const extension of topExtensions) {
    add("主要文件类型", extension, dataRange.extension, extension, "按“数据来源”的派生扩展名列精确匹配；仅列频次最高的 12 类");
  }
  return descriptors;
}

function licenseRows(manifestRows, sourceDocument) {
  const licenseNames = new Set(manifestRows.map((row) => row.license_spdx || "UNKNOWN"));
  for (const source of sourceDocument.sources ?? []) {
    licenseNames.add(textValue(source.license_spdx) || "UNKNOWN");
  }
  return [...licenseNames]
    .sort((left, right) => (left === "UNKNOWN" ? 1 : 0) - (right === "UNKNOWN" ? 1 : 0) || left.localeCompare(right, "en"))
    .map((license) => {
      const registered = (sourceDocument.sources ?? []).filter(
        (source) => (textValue(source.license_spdx) || "UNKNOWN") === license,
      );
      const redistributionAllowed =
        registered.length > 0 && registered.every((source) => source.redistribution_allowed === true);
      return {
        license,
        verdict: redistributionAllowed ? "允许" : "禁止或待复核",
        registeredCount: registered.length,
        note: redistributionAllowed
          ? "仅限遵守许可证、署名及来源条款后公开再分发。"
          : "采用 fail-closed：未确认许可或再分发权利时不得随仓库公开。",
      };
    });
}

async function inputInventory(pathsToInventory, rowCounts) {
  const rows = [];
  for (const filePath of [...pathsToInventory].sort((a, b) => a.localeCompare(b, "zh-CN"))) {
    const stat = await fs.stat(filePath);
    const relativePath = path.relative(PROJECT_ROOT, filePath).replaceAll("\\", "/");
    rows.push([
      "输入文件",
      relativePath,
      `${stat.size} Byte`,
      `SHA-256: ${await sha256(filePath)}${rowCounts.has(filePath) ? `；记录数: ${rowCounts.get(filePath)}` : ""}`,
    ]);
  }
  return rows;
}

function prepareSheet(sheet, title, note, lastColumn, freezeRows = 4) {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${lastColumn}1`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A2:${lastColumn}2`).merge();
  sheet.getRange("A2").values = [[note]];
  sheet.getRange(`A1:${lastColumn}1`).format = {
    fill: COLORS.navy,
    font: { bold: true, color: COLORS.white, size: 16 },
    verticalAlignment: "center",
  };
  sheet.getRange(`A2:${lastColumn}2`).format = {
    fill: COLORS.paleBlue,
    font: { color: COLORS.muted, italic: true, size: 10 },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("1:1").format.rowHeight = 30;
  sheet.getRange("2:2").format.rowHeight = 32;
  sheet.freezePanes.freezeRows(freezeRows);
}

function addTable(sheet, rangeAddress, tableName, style = "TableStyleMedium2") {
  const table = sheet.tables.add(rangeAddress, true, tableName);
  table.style = style;
  table.showHeaders = true;
  table.showFilterButton = true;
  table.showBandedRows = true;
  return table;
}

function setWidths(sheet, widths, finalRow) {
  widths.forEach((width, index) => {
    const column = columnLetter(index + 1);
    sheet.getRange(`${column}1:${column}${finalRow}`).format.columnWidth = width;
  });
}

function addStatusFormats(range, rules) {
  for (const rule of rules) {
    range.conditionalFormats.add("containsText", {
      text: rule.text,
      format: { fill: rule.fill, font: { color: rule.font, bold: true } },
    });
  }
}

function populateOverview(sheet, sourceRows, dimensions) {
  prepareSheet(
    sheet,
    "TPU 高通量筛选数据库 v0.1 · 审核总览",
    "本工作簿是可追溯审核目录，不替代原始数据、规范化 Parquet 或 DuckDB 快照；公开发布必须通过许可证与质量门控。",
    "H",
    3,
  );

  const cards = [
    ["来源文件数", `=COUNTA('数据来源'!$B$5:$B$${dimensions.sourceEnd})`, "原始数据量 (Byte)", `=SUM('数据来源'!$E$5:$E$${dimensions.sourceEnd})`, "可用文件数", `=COUNTIF('数据来源'!$O$5:$O$${dimensions.sourceEnd},"available")`, "待复核文件数", `=COUNTIF('数据来源'!$O$5:$O$${dimensions.sourceEnd},"review_required")`],
    ["字段数量", `=COUNTA('字段字典'!$D$5:$D$${dimensions.fieldEnd})`, "登记来源数", `=COUNTA('总览'!$A$17:$A$${dimensions.overviewSourceEnd})`, "已知许可证文件", `=COUNTIF('数据来源'!$J$5:$J$${dimensions.sourceEnd},"<>UNKNOWN")`, "未知许可证文件", `=COUNTIF('数据来源'!$J$5:$J$${dimensions.sourceEnd},"UNKNOWN")`],
    ["许可证类型数", `=COUNTA('许可证'!$A$5:$A$${dimensions.licenseEnd})`, "可公开再分发文件", `=SUM('许可证'!$D$5:$D$${dimensions.licenseEnd})`, "质量问题数", `=COUNTIF('质量问题'!$C$5:$C$${dimensions.qualityEnd},"<>NO_ISSUES_FILE")-COUNTIF('质量问题'!$C$5:$C$${dimensions.qualityEnd},"NO_QUALITY_ISSUES")`, "快照文件数", "='构建信息'!$C$8"],
  ];
  const rows = [4, 7, 10];
  for (let index = 0; index < cards.length; index += 1) {
    const row = rows[index];
    const values = cards[index];
    for (let columnIndex = 0; columnIndex < values.length; columnIndex += 2) {
      const labelCell = sheet.getCell(row - 1, columnIndex);
      const valueCell = sheet.getCell(row - 1, columnIndex + 1);
      labelCell.values = [[values[columnIndex]]];
      valueCell.formulas = [[values[columnIndex + 1]]];
      labelCell.format = {
        fill: COLORS.teal,
        font: { bold: true, color: COLORS.white },
        verticalAlignment: "center",
      };
      valueCell.format = {
        fill: COLORS.paleTeal,
        font: { bold: true, color: COLORS.teal, size: 13 },
        horizontalAlignment: "right",
        verticalAlignment: "center",
        numberFormat: "#,##0",
      };
    }
    sheet.getRange(`A${row}:H${row}`).format.rowHeight = 28;
  }

  sheet.getRange("A13:H14").merge();
  sheet.getRange("A13").values = [[
    "审核要点：UNKNOWN 许可证、review_required 状态及仅供参考数据默认禁止公开再分发；SMiPoly 仅提供候选结构，不能作为 TPU 实验性能标签。",
  ]];
  sheet.getRange("A13:H14").format = {
    fill: COLORS.yellow,
    font: { color: COLORS.yellowText, bold: true },
    wrapText: true,
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: "#F59E0B" },
  };

  const headers = ["来源ID", "项目内相对路径", "许可证", "允许衍生", "允许再分发", "证据等级", "材料范围", "状态"];
  sheet.getRange(`A16:H${dimensions.overviewSourceEnd}`).values = [headers, ...sourceRows];
  addTable(sheet, `A16:H${dimensions.overviewSourceEnd}`, "RegisteredSourcesTable", "TableStyleMedium4");
  addStatusFormats(sheet.getRange(`H17:H${dimensions.overviewSourceEnd}`), [
    { text: "available", fill: COLORS.green, font: COLORS.greenText },
    { text: "review_required", fill: COLORS.yellow, font: COLORS.yellowText },
  ]);
  addStatusFormats(sheet.getRange(`C17:C${dimensions.overviewSourceEnd}`), [
    { text: "UNKNOWN", fill: COLORS.red, font: COLORS.redText },
  ]);
  setWidths(sheet, [24, 48, 20, 14, 14, 22, 24, 20], dimensions.overviewSourceEnd);
  sheet.getRange(`B17:B${dimensions.overviewSourceEnd}`).format.wrapText = true;
}

function populateSourceFiles(sheet, manifestRows) {
  const headers = [
    "来源ID", "来源文件ID", "原始相对路径", "原始文件名", "大小(Byte)", "SHA-256", "DOI", "来源URL",
    "访问日期", "许可证", "允许衍生", "允许再分发", "证据等级", "材料范围", "状态", "备注", "原始数据层(派生)", "文件扩展名(派生)",
  ];
  const dataRows = manifestRows.map((row) => [
    row.source_id, row.source_file_id, row.raw_path, row.original_filename, row.size_bytes, row.sha256, row.doi,
    row.url, row.accessed_at, row.license_spdx, row.derivatives_allowed, row.redistribution_allowed,
    row.evidence_grade, row.material_scope, row.status, row.notes,
    row.raw_path.split("/")[0] === "01_原始数据" && row.raw_path.split("/")[1]
      ? row.raw_path.split("/")[1]
      : "其他",
    path.extname(row.original_filename).toLowerCase() || "[无扩展名]",
  ]);
  const finalRow = 4 + dataRows.length;
  prepareSheet(
    sheet,
    "数据来源与文件级溯源清单",
    "每行对应一个本地原始文件；SHA-256、许可证、访问日期与发布状态用于完整性复核和 fail-closed 发布门控。",
    "R",
  );
  sheet.getRange(`A4:R${finalRow}`).values = [headers, ...dataRows];
  addTable(sheet, `A4:R${finalRow}`, "SourceFilesTable");
  setWidths(sheet, [24, 27, 52, 32, 16, 26, 22, 36, 14, 20, 14, 14, 22, 22, 20, 54, 20, 20], finalRow);
  sheet.getRange(`E5:E${finalRow}`).format.numberFormat = "#,##0";
  sheet.getRange(`I5:I${finalRow}`).format.numberFormat = "yyyy-mm-dd";
  sheet.getRange(`C5:C${finalRow}`).format.wrapText = true;
  sheet.getRange(`P5:P${finalRow}`).format.wrapText = true;
  addStatusFormats(sheet.getRange(`O5:O${finalRow}`), [
    { text: "available", fill: COLORS.green, font: COLORS.greenText },
    { text: "review_required", fill: COLORS.yellow, font: COLORS.yellowText },
    { text: "unavailable", fill: COLORS.red, font: COLORS.redText },
  ]);
  addStatusFormats(sheet.getRange(`J5:J${finalRow}`), [
    { text: "UNKNOWN", fill: COLORS.red, font: COLORS.redText },
  ]);
}

function populateFieldDictionary(sheet, fieldRows) {
  const headers = ["表名", "表说明", "主键字段", "字段名", "数据类型", "必填", "枚举", "字段说明", "是否主键"];
  const finalRow = 4 + fieldRows.length;
  prepareSheet(
    sheet,
    "TPU 数据库 v0.1 字段字典",
    "字段定义来自结构定义/v0.1字段字典.yaml；原始值与规范值应并存，所有规范记录必须保留来源定位。",
    "I",
  );
  sheet.getRange(`A4:I${finalRow}`).values = [headers, ...fieldRows];
  addTable(sheet, `A4:I${finalRow}`, "FieldDictionaryTable", "TableStyleMedium4");
  setWidths(sheet, [20, 46, 24, 30, 16, 12, 20, 54, 14], finalRow);
  sheet.getRange(`B5:B${finalRow}`).format.wrapText = true;
  sheet.getRange(`H5:H${finalRow}`).format.wrapText = true;
}

function populateCoverage(sheet, descriptors, overviewTotalCell) {
  const headers = ["覆盖维度", "覆盖项", "文件数", "数据量(Byte)", "文件占比", "说明"];
  const dataRows = descriptors.map((descriptor) => [descriptor.dimension, descriptor.label, null, null, null, descriptor.note]);
  const finalRow = 4 + dataRows.length;
  prepareSheet(
    sheet,
    "数据覆盖概览",
    "统计由可审计公式直接引用“数据来源”工作表；同一文件会在不同覆盖维度重复计入，请勿跨维度求和。",
    "F",
  );
  sheet.getRange(`A4:F${finalRow}`).values = [headers, ...dataRows];
  descriptors.forEach((descriptor, index) => {
    const row = index + 5;
    sheet.getRange(`C${row}:E${row}`).formulas = [[
      descriptor.countFormula,
      descriptor.bytesFormula,
      `=IF(${overviewTotalCell}=0,0,C${row}/${overviewTotalCell})`,
    ]];
  });
  addTable(sheet, `A4:F${finalRow}`, "DataCoverageTable", "TableStyleMedium9");
  setWidths(sheet, [18, 28, 16, 20, 16, 54], finalRow);
  sheet.getRange(`C5:D${finalRow}`).format.numberFormat = "#,##0";
  sheet.getRange(`E5:E${finalRow}`).format.numberFormat = "0.0%";
  sheet.getRange(`F5:F${finalRow}`).format.wrapText = true;
}

function populateQuality(sheet, issues) {
  const headers = ["报告文件", "严重程度", "问题代码", "表名", "记录ID", "字段", "问题描述", "允许公开", "来源定位"];
  const finalRow = 4 + issues.length;
  prepareSheet(
    sheet,
    "质量问题审核清单",
    "自动汇总文档/质量报告中的 CSV/JSON；QC 零问题与尚未生成报告会分别明确标注。",
    "I",
  );
  sheet.getRange(`A4:I${finalRow}`).values = [headers, ...issues];
  addTable(sheet, `A4:I${finalRow}`, "QualityIssuesTable", "TableStyleMedium3");
  setWidths(sheet, [34, 16, 24, 20, 24, 20, 58, 16, 42], finalRow);
  sheet.getRange(`G5:G${finalRow}`).format.wrapText = true;
  sheet.getRange(`I5:I${finalRow}`).format.wrapText = true;
  addStatusFormats(sheet.getRange(`B5:B${finalRow}`), [
    { text: "critical", fill: COLORS.red, font: COLORS.redText },
    { text: "error", fill: COLORS.red, font: COLORS.redText },
    { text: "warning", fill: COLORS.yellow, font: COLORS.yellowText },
    { text: "info", fill: COLORS.info, font: COLORS.infoText },
  ]);
}

function populateLicenses(sheet, licenses, sourceEnd) {
  const headers = ["许可证标识", "文件数", "数据量(Byte)", "可公开再分发文件", "待复核文件", "公开再分发判定", "已登记来源数", "说明"];
  const dataRows = licenses.map((row) => [row.license, null, null, null, null, row.verdict, row.registeredCount, row.note]);
  const finalRow = 4 + dataRows.length;
  prepareSheet(
    sheet,
    "许可证与公开再分发门控",
    "统计公式引用文件级来源清单；未确认许可、衍生权利或再分发权利时一律按禁止公开处理。",
    "H",
  );
  sheet.getRange(`A4:H${finalRow}`).values = [headers, ...dataRows];
  licenses.forEach((license, index) => {
    const row = index + 5;
    const criterion = excelString(license.license);
    sheet.getRange(`B${row}:E${row}`).formulas = [[
      `=COUNTIF('数据来源'!$J$5:$J$${sourceEnd},"${criterion}")`,
      `=SUMIF('数据来源'!$J$5:$J$${sourceEnd},"${criterion}",'数据来源'!$E$5:$E$${sourceEnd})`,
      license.verdict === "允许"
        ? `=COUNTIFS('数据来源'!$J$5:$J$${sourceEnd},"${criterion}",'数据来源'!$O$5:$O$${sourceEnd},"available")`
        : "=0",
      `=COUNTIFS('数据来源'!$J$5:$J$${sourceEnd},"${criterion}",'数据来源'!$O$5:$O$${sourceEnd},"review_required")`,
    ]];
  });
  addTable(sheet, `A4:H${finalRow}`, "LicenseGateTable", "TableStyleMedium5");
  setWidths(sheet, [22, 14, 20, 20, 18, 22, 18, 58], finalRow);
  sheet.getRange(`B5:E${finalRow}`).format.numberFormat = "#,##0";
  sheet.getRange(`H5:H${finalRow}`).format.wrapText = true;
  addStatusFormats(sheet.getRange(`F5:F${finalRow}`), [
    { text: "允许", fill: COLORS.green, font: COLORS.greenText },
    { text: "禁止或待复核", fill: COLORS.red, font: COLORS.redText },
  ]);
  addStatusFormats(sheet.getRange(`A5:A${finalRow}`), [
    { text: "UNKNOWN", fill: COLORS.red, font: COLORS.redText },
  ]);
}

function populateBuildInfo(sheet, buildRows) {
  const headers = ["类型", "名称", "值", "来源/说明"];
  const finalRow = 4 + buildRows.length;
  prepareSheet(
    sheet,
    "工作簿构建与输入指纹",
    "输入文件按项目相对路径排序并记录 SHA-256；相同输入应生成相同的表结构、排序和公式。",
    "D",
  );
  sheet.getRange(`A4:D${finalRow}`).values = [headers, ...buildRows];
  addTable(sheet, `A4:D${finalRow}`, "BuildInfoTable", "TableStyleMedium4");
  setWidths(sheet, [20, 42, 28, 76], finalRow);
  sheet.getRange(`B5:D${finalRow}`).format.wrapText = true;
}

async function renderPreviews(workbook, previewDir, previewRanges) {
  await fs.mkdir(previewDir, { recursive: true });
  const previews = [];
  for (const [sheetName, range] of Object.entries(previewRanges)) {
    const blob = await workbook.render({ sheetName, range, scale: 1, format: "png" });
    const outputPath = path.join(previewDir, `${sheetName}.png`);
    await fs.writeFile(outputPath, new Uint8Array(await blob.arrayBuffer()));
    previews.push(outputPath);
  }
  return previews;
}

async function main() {
  const { previewDir } = parseArguments(process.argv.slice(2));
  const [manifestText, schemaText, sourceText] = await Promise.all([
    readText(INPUTS.manifest),
    readText(INPUTS.schema),
    readText(INPUTS.sources),
  ]);
  const manifestRows = normalizeManifest(await csvObjects(manifestText, "来源清单导入"));
  const schemaDocument = parseYamlSubset(schemaText);
  const sourceDocument = parseYamlSubset(sourceText);
  const fieldRows = flattenFieldDictionary(schemaDocument);
  const sourceRows = registeredSourceRows(sourceDocument);
  const { issues, qualityFiles, reportFiles } = await loadQualityIssues();
  const snapshotFiles = await listFiles(INPUTS.snapshotDir, new Set([".json"]));
  const descriptors = coverageDescriptors(manifestRows);
  const licenses = licenseRows(manifestRows, sourceDocument);

  const rowCounts = new Map([
    [INPUTS.manifest, manifestRows.length],
    [INPUTS.schema, fieldRows.length],
    [INPUTS.sources, sourceRows.length],
    ...qualityFiles.map((filePath) => [filePath, issues.filter((issue) => issue[0] === path.relative(PROJECT_ROOT, filePath).replaceAll("\\", "/")).length]),
  ]);
  const inventoryPaths = [INPUTS.manifest, INPUTS.schema, INPUTS.sources, ...reportFiles, ...snapshotFiles];
  const buildRows = [
    ["构建器", "工作簿", "TPU数据库_v0.1_审核目录.xlsx", "由代码/build_catalog.mjs 使用 @oai/artifact-tool 构建"],
    ["模式", "排序与计算", "确定性", "固定工作表顺序；记录、许可证、输入指纹按稳定键排序；不使用当前时间或随机数"],
    ["模式", "许可门控", "fail-closed", "UNKNOWN 或权利未确认的数据不得公开再分发"],
    ["数据库快照", "JSON 文件数", snapshotFiles.length, snapshotFiles.length > 0 ? "读取 05_数据库快照/*.json" : "当前尚无快照 JSON"],
    ...(await inputInventory(inventoryPaths, rowCounts)),
  ];

  const workbook = Workbook.create();
  const sheets = Object.fromEntries(
    ["总览", "数据来源", "字段字典", "数据覆盖", "质量问题", "许可证", "构建信息"].map((name) => [
      name,
      workbook.worksheets.add(name),
    ]),
  );

  const dimensions = {
    sourceEnd: 4 + manifestRows.length,
    fieldEnd: 4 + fieldRows.length,
    overviewSourceEnd: 16 + sourceRows.length,
    qualityEnd: 4 + issues.length,
    licenseEnd: 4 + licenses.length,
    buildEnd: 4 + buildRows.length,
  };

  populateSourceFiles(sheets["数据来源"], manifestRows);
  populateFieldDictionary(sheets["字段字典"], fieldRows);
  populateCoverage(sheets["数据覆盖"], descriptors, `'总览'!$B$4`);
  populateQuality(sheets["质量问题"], issues);
  populateLicenses(sheets["许可证"], licenses, dimensions.sourceEnd);
  populateBuildInfo(sheets["构建信息"], buildRows);
  populateOverview(sheets["总览"], sourceRows, dimensions);

  const overviewCheck = await workbook.inspect({
    kind: "table",
    range: "总览!A1:H20",
    include: "values,formulas",
    tableMaxRows: 20,
    tableMaxCols: 8,
    maxChars: 7000,
  });
  const licenseCheck = await workbook.inspect({
    kind: "table",
    range: `许可证!A1:H${dimensions.licenseEnd}`,
    include: "values,formulas",
    tableMaxRows: 20,
    tableMaxCols: 8,
    maxChars: 5000,
  });
  const sourceCheck = await workbook.inspect({
    kind: "table",
    range: "数据来源!A1:R9",
    include: "values,formulas",
    tableMaxRows: 9,
    tableMaxCols: 18,
    maxChars: 5000,
  });
  const coverageCheck = await workbook.inspect({
    kind: "table",
    range: `数据覆盖!A1:F${Math.min(4 + descriptors.length, 32)}`,
    include: "values,formulas",
    tableMaxRows: 32,
    tableMaxCols: 6,
    maxChars: 7000,
  });
  console.log("[检查:总览]\n" + overviewCheck.ndjson);
  console.log("[检查:许可证]\n" + licenseCheck.ndjson);
  console.log("[检查:数据来源]\n" + sourceCheck.ndjson);
  console.log("[检查:数据覆盖]\n" + coverageCheck.ndjson);

  const formulaErrors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "最终公式错误扫描",
    maxChars: 6000,
  });
  console.log("[公式错误扫描]\n" + formulaErrors.ndjson);
  if (/#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/.test(formulaErrors.ndjson)) {
    throw new Error("检测到公式错误，停止导出。\n" + formulaErrors.ndjson);
  }

  const previewRanges = {
    "总览": `A1:H${dimensions.overviewSourceEnd}`,
    "数据来源": "A1:R26",
    "字段字典": `A1:I${Math.min(dimensions.fieldEnd, 30)}`,
    "数据覆盖": `A1:F${Math.min(4 + descriptors.length, 36)}`,
    "质量问题": `A1:I${Math.min(dimensions.qualityEnd, 30)}`,
    "许可证": `A1:H${dimensions.licenseEnd}`,
    "构建信息": `A1:D${dimensions.buildEnd}`,
  };
  const previewPaths = await renderPreviews(workbook, previewDir, previewRanges);

  await fs.mkdir(path.dirname(OUTPUT_PATH), { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(OUTPUT_PATH);
  await fs.rm(`${OUTPUT_PATH}.inspect.ndjson`, { force: true });

  console.log(JSON.stringify({
    output: OUTPUT_PATH,
    previews: previewPaths,
    sheetNames: Object.keys(sheets),
    counts: {
      sourceFiles: manifestRows.length,
      registeredSources: sourceRows.length,
      fields: fieldRows.length,
      coverageRows: descriptors.length,
      qualityRows: issues.filter((row) => !["NO_ISSUES_FILE", "NO_QUALITY_ISSUES"].includes(row[2])).length,
      licenseRows: licenses.length,
      snapshotFiles: snapshotFiles.length,
    },
  }, null, 2));
}

await main();
