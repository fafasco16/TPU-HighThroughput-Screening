"""只读审计 Zenodo 无溶剂聚氨酯链增长动力学数据。

数据集 DOI：10.5281/zenodo.6406174。原始 ZIP 内含一个旧版 XLS 和一个
元数据 PDF。本脚本核验固定来源与 ZIP 成员，只把 XLS 解压到系统临时目录，
再通过 Microsoft Excel COM 以只读、禁宏、不保存方式读取。原始测量值不被
清洗或覆盖；工作簿中缺失的时间保持缺失，只记录前一时间作为上下文。

本来源是可靠实验动力学参考，可进入 Gold-E 参考层，但不是 TPU 力学性能的
直接监督标签。训练权重和数据集划分均不在本脚本中物化。
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第十批实验_无溶剂PU反应动力学"
)
API_FILE = SOURCE_DIR / "Zenodo_API元数据.json"
SOURCE_ZIP = SOURCE_DIR / "Solvent_Free_Adhesives_Dataset_5-2.zip"
HASH_MANIFEST = SOURCE_DIR / "来源文件哈希.json"

DATASET_DOI = "10.5281/zenodo.6406174"
DATASET_URL = "https://zenodo.org/records/6406174"
API_URL = "https://zenodo.org/api/records/6406174"
DOWNLOAD_URL = (
    "https://zenodo.org/api/records/6406174/files/"
    "Solvent_Free_Adhesives_Dataset_5-2.zip/content"
)
PAPER_DOI = "10.1039/D2RA08326D"
PAPER_URL = "https://pubs.rsc.org/en/content/articlehtml/2023/ra/d2ra08326d"
LICENSE = "CC-BY-4.0"
AUDIT_VERSION = "batch10-solvent-free-pu-kinetics-v1"

EXPECTED_LOCAL = {
    "Zenodo_API元数据.json": (
        6_114,
        "24542b7dfcb8f866fcdffc705cde035e4bcd6f50e83e90fe6fcd0f87939727ef",
        "5218718893c3ed6955f0e878ed62a6eb",
    ),
    "Solvent_Free_Adhesives_Dataset_5-2.zip": (
        580_544,
        "dcf72184ff617b9ad42141628262d6ee57ad99171a635b05254bebd91a8e8e52",
        "6282bca96c6961619b4e82ff0e4da735",
    ),
}
EXPECTED_MEMBERS = {
    "Metadata_Dataset_5-2_Solvent_Free_Adhesives.pdf": (
        579_884,
        "d214ee10f250680070bd7d3b089067307de1ced77ef24b0cc49840300e08ed1d",
    ),
    "Solvent_Free_Adhesives_Dataset_5-2.xls": (
        215_040,
        "ad4b98e3ae69668ce2542038c100d002aa01929b0da45d2eaf62049f2469ed0f",
    ),
}

OUTPUT_NAMES = (
    "内容审计摘要.json",
    "原料身份清单.tsv",
    "反应条件清单.tsv",
    "测量列清单.tsv",
    "NCO测量长表.tsv",
    "论文实验协议.json",
    "文件校验清单.tsv",
)


class AuditBlocked(RuntimeError):
    """固定来源身份、结构或科学计数漂移时终止。"""


MATERIAL_ROWS: tuple[dict[str, Any], ...] = (
    {
        "原料代码": "PEA",
        "角色": "宏二醇",
        "化学身份": "polyethylene adipate, alpha,omega-diol",
        "CAS": "24937-05-1",
        "Mn_g_mol": 2050,
        "分子量_g_mol": "",
        "组成说明": "polyethylene adipate；工作簿给出 Mn 约 2050 g/mol",
    },
    {
        "原料代码": "PDEA",
        "角色": "宏二醇",
        "化学身份": "polydiethylene adipate, alpha,omega-diol",
        "CAS": "25036-49-1",
        "Mn_g_mol": 2700,
        "分子量_g_mol": "",
        "组成说明": "polydiethylene adipate；工作簿给出 Mn 约 2700 g/mol",
    },
    {
        "原料代码": "PCL",
        "角色": "宏二醇",
        "化学身份": "polycaprolactone alpha,omega-diol",
        "CAS": "36890-68-3",
        "Mn_g_mol": 2000,
        "分子量_g_mol": "",
        "组成说明": "polycaprolactone diol；工作簿给出 Mn 约 2000 g/mol",
    },
    {
        "原料代码": "PEG",
        "角色": "宏二醇",
        "化学身份": "polyethylene glycol",
        "CAS": "25322-68-3",
        "Mn_g_mol": 1000,
        "分子量_g_mol": "",
        "组成说明": "polyethylene glycol；工作簿给出 Mn 约 1000 g/mol",
    },
    {
        "原料代码": "HDI",
        "角色": "二异氰酸酯",
        "化学身份": "1,6-hexamethylene diisocyanate",
        "CAS": "822-06-0",
        "Mn_g_mol": "",
        "分子量_g_mol": 168.19,
        "组成说明": "单一 HDI 身份",
    },
    {
        "原料代码": "TDI",
        "角色": "二异氰酸酯",
        "化学身份": "2,4-/2,6-toluene diisocyanate mixture",
        "CAS": "584-84-9;91-08-7",
        "Mn_g_mol": "",
        "分子量_g_mol": 174.16,
        "组成说明": "2,4-TDI:2,6-TDI = 4:1 mol（80:20 mol%）",
    },
)


def _columns(
    sheet: str,
    macrodiol: str,
    diisocyanate: str,
    ratio: float,
    temperatures_and_repeats: Iterable[tuple[int, int, int]],
) -> list[dict[str, Any]]:
    return [
        {
            "sheet": sheet,
            "macrodiol": macrodiol,
            "diisocyanate": diisocyanate,
            "ratio": ratio,
            "temperature_c": temperature,
            "column": column,
            "replicate_index": repeat,
        }
        for column, temperature, repeat in temperatures_and_repeats
    ]


COLUMN_SPECS = tuple(
    _columns(
        "PEA+HDI", "PEA", "HDI", 0.3,
        ((3, 70, 1), (4, 70, 2), (7, 80, 1), (8, 80, 2), (11, 90, 1), (12, 90, 2)),
    )
    + _columns(
        "PEA+HDI", "PEA", "HDI", 0.5,
        ((5, 70, 1), (6, 70, 2), (9, 80, 1), (10, 80, 2), (13, 90, 1), (14, 90, 2)),
    )
    + _columns(
        "PDEA+HDI", "PDEA", "HDI", 0.3,
        ((3, 70, 1), (5, 80, 1), (7, 90, 1)),
    )
    + _columns(
        "PDEA+HDI", "PDEA", "HDI", 0.5,
        ((4, 70, 1), (6, 80, 1), (8, 90, 1)),
    )
    + _columns(
        "PDEA+TDI", "PDEA", "TDI", 1.25,
        ((3, 60, 1), (4, 60, 2), (5, 70, 1), (6, 70, 2), (7, 80, 1), (8, 80, 2)),
    )
    + _columns(
        "PCL+TDI", "PCL", "TDI", 0.3,
        ((3, 60, 1), (4, 70, 1), (5, 70, 2), (6, 80, 1), (7, 80, 2)),
    )
    + _columns(
        "PEG+TDI", "PEG", "TDI", 0.3,
        ((3, 50, 1), (4, 50, 2), (5, 70, 1), (6, 70, 2), (7, 80, 1)),
    )
)

WORKBOOK_INITIAL_NCO = {
    ("PEA+HDI", 0.3): 1.200,
    ("PEA+HDI", 0.5): 1.968,
    ("PDEA+HDI", 0.3): 0.916,
    ("PDEA+HDI", 0.5): 1.509,
    ("PDEA+TDI", 1.25): 3.599,
    ("PCL+TDI", 0.3): 1.228,
    ("PEG+TDI", 0.3): 2.395,
}

# 论文批次表只覆盖下列组合；未报告的组合保持空值，绝不以工作簿值代填。
PAPER_BATCH_INITIAL_NCO = {
    ("PEA+HDI", 0.3): 1.256,
    ("PEA+HDI", 0.5): 2.059,
    ("PDEA+HDI", 0.3): 0.938,
    ("PDEA+HDI", 0.5): 1.541,
    ("PCL+TDI", 0.3): 1.237,
}

EXPECTED_SHEET_COUNTS = {
    "PEA+HDI": {"conditions": 6, "columns": 12, "points": 61},
    "PDEA+HDI": {"conditions": 6, "columns": 6, "points": 32},
    "PDEA+TDI": {"conditions": 3, "columns": 6, "points": 38},
    "PCL+TDI": {"conditions": 3, "columns": 5, "points": 21},
    "PEG+TDI": {"conditions": 3, "columns": 5, "points": 19},
}


PAPER_PROTOCOL: dict[str, Any] = {
    "论文DOI": PAPER_DOI,
    "论文题名": (
        "Time-temperature superposition for kinetic mapping of solventless "
        "autocatalytic addition of diisocyanates and macrodiols"
    ),
    "期刊": "RSC Advances",
    "年份": 2023,
    "卷页": "13, 9686-9696",
    "论文链接": PAPER_URL,
    "反应性质": {
        "溶剂": "无外加溶剂",
        "催化剂": "无外加催化剂；体系按论文表述为自催化加成",
        "气氛": "干燥氮气吹扫",
        "搅拌": "机械搅拌",
        "温度控制": "恒温装置，目标温度控制在正负1摄氏度",
    },
    "原料协议": {
        "HDI": "Covestro；51.5±0.5 %NCO",
        "TDI": "Covestro；48.7±0.2 %NCO；2,4-/2,6-TDI为80/20 mol%",
        "宏二醇装置": "三颈烧瓶、恒温装置和回流冷凝器",
        "异氰酸酯加入": "异氰酸酯预热后加入宏二醇",
    },
    "取样与滴定": {
        "单次取样量_mL": "0.1-0.9",
        "吸收液": "10 mL丙酮中的0.1 M二丁胺",
        "反应处理": "40摄氏度保持30-40 min后冷却",
        "指示剂": "2-3滴溴酚蓝",
        "回滴液": "0.1 M盐酸水溶液",
        "标准": "改编自ASTM D5155",
        "NCO公式": "%NCO = 0.42 × (V_B - V_S) / m_S",
    },
    "建模边界": {
        "理论t0": "工作簿理论初始%NCO不是实测点，单独保存",
        "零值": "零%NCO测量保留在原始审计层；论文TTS/相关分析排除零值",
        "缺失时间": "不插补；只保存前一非空时间作为上下文",
    },
}


POWERSHELL_READER = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$excel = $null
$workbook = $null

function Matrix-Value($matrix, [int]$row, [int]$column, [int]$rows, [int]$columns) {
    if ($rows -eq 1 -and $columns -eq 1) { return $matrix }
    return $matrix.GetValue($row, $column)
}

try {
    $xlsPath = [IO.Path]::GetFullPath($env:TPU_BATCH10_XLS_PATH)
    if (-not (Test-Path -LiteralPath $xlsPath -PathType Leaf)) { throw '缺少临时XLS' }
    $item = Get-Item -LiteralPath $xlsPath -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw '拒绝重解析点XLS' }
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excel.AutomationSecurity = 3
    $workbook = $excel.Workbooks.Open($xlsPath, 0, $true)
    if (-not $workbook.ReadOnly) { throw 'Excel没有只读打开工作簿' }

    $sheets = [System.Collections.Generic.List[object]]::new()
    for ($sheetIndex = 1; $sheetIndex -le $workbook.Worksheets.Count; $sheetIndex++) {
        $sheet = $null
        $used = $null
        try {
            $sheet = $workbook.Worksheets.Item($sheetIndex)
            $used = $sheet.UsedRange
            $rows = [int]$used.Rows.Count
            $columns = [int]$used.Columns.Count
            $startRow = [int]$used.Row
            $startColumn = [int]$used.Column
            $values = $used.Value2
            $cells = [System.Collections.Generic.List[object]]::new()
            for ($row = 1; $row -le $rows; $row++) {
                for ($column = 1; $column -le $columns; $column++) {
                    $value = Matrix-Value $values $row $column $rows $columns
                    if ($null -ne $value -and [string]$value -ne '') {
                        $cells.Add([pscustomobject]@{
                            row = $startRow + $row - 1
                            column = $startColumn + $column - 1
                            value = $value
                        })
                    }
                }
            }
            $sheets.Add([pscustomobject]@{
                name = [string]$sheet.Name
                used_start_row = $startRow
                used_start_column = $startColumn
                used_rows = $rows
                used_columns = $columns
                cells = @($cells)
            })
        } finally {
            if ($null -ne $used) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($used) }
            if ($null -ne $sheet) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($sheet) }
        }
    }
    [pscustomobject]@{
        read_only = [bool]$workbook.ReadOnly
        worksheet_count = [int]$workbook.Worksheets.Count
        sheets = @($sheets)
    } | ConvertTo-Json -Depth 8 -Compress
} finally {
    if ($null -ne $workbook) {
        $workbook.Close($false)
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($workbook)
    }
    if ($null -ne $excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""


def _hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_file(path: Path, size: int, sha256: str, md5: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AuditBlocked(f"缺失、非普通文件或符号链接：{path}")
    actual = {
        "文件": path.name,
        "字节数": path.stat().st_size,
        "SHA256": _hash(path, "sha256"),
        "MD5": _hash(path, "md5"),
        "校验": "通过",
    }
    if (actual["字节数"], actual["SHA256"], actual["MD5"]) != (size, sha256, md5):
        raise AuditBlocked(f"固定来源文件漂移：{path.name}")
    return actual


def _validate_api() -> dict[str, Any]:
    try:
        document = json.loads(API_FILE.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditBlocked("Zenodo API元数据不是严格UTF-8 JSON") from exc
    metadata = document.get("metadata", {})
    files = document.get("files", [])
    if document.get("id") != 6_406_174 or document.get("doi") != DATASET_DOI:
        raise AuditBlocked("Zenodo记录ID或DOI漂移")
    if metadata.get("license", {}).get("id") != "cc-by-4.0":
        raise AuditBlocked("Zenodo许可漂移")
    if metadata.get("access_right") != "open":
        raise AuditBlocked("Zenodo开放状态漂移")
    if len(files) != 1:
        raise AuditBlocked("Zenodo文件数不是1")
    official = files[0]
    if (
        official.get("key") != SOURCE_ZIP.name
        or official.get("size") != EXPECTED_LOCAL[SOURCE_ZIP.name][0]
        or official.get("checksum") != f"md5:{EXPECTED_LOCAL[SOURCE_ZIP.name][2]}"
        or official.get("links", {}).get("self") != DOWNLOAD_URL
    ):
        raise AuditBlocked("Zenodo官方文件身份漂移")
    return document


def _safe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _verify_zip() -> tuple[list[dict[str, Any]], bytes]:
    member_rows: list[dict[str, Any]] = []
    workbook_payload = b""
    with zipfile.ZipFile(SOURCE_ZIP) as archive:
        if archive.testzip() is not None:
            raise AuditBlocked("ZIP CRC校验失败")
        infos = archive.infolist()
        if len(infos) != 2 or {info.filename for info in infos} != set(EXPECTED_MEMBERS):
            raise AuditBlocked("ZIP成员集合漂移")
        for info in infos:
            if not _safe_member_name(info.filename) or info.is_dir():
                raise AuditBlocked(f"ZIP成员路径不安全：{info.filename}")
            if info.flag_bits & 0x1:
                raise AuditBlocked(f"ZIP成员被加密：{info.filename}")
            expected_size, expected_sha = EXPECTED_MEMBERS[info.filename]
            with archive.open(info, "r") as handle:
                payload = handle.read()
            digest = hashlib.sha256(payload).hexdigest()
            if len(payload) != expected_size or digest != expected_sha:
                raise AuditBlocked(f"ZIP成员漂移：{info.filename}")
            member_rows.append(
                {
                    "文件": f"ZIP!/{info.filename}",
                    "字节数": len(payload),
                    "SHA256": digest,
                    "MD5": hashlib.md5(payload).hexdigest(),  # noqa: S324 - provenance only
                    "校验": "通过",
                }
            )
            if info.filename.endswith(".xls"):
                workbook_payload = payload
    if not workbook_payload:
        raise AuditBlocked("ZIP中缺失XLS")
    return sorted(member_rows, key=lambda row: str(row["文件"])), workbook_payload


def _read_workbook(payload: bytes) -> dict[str, Any]:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        raise AuditBlocked("旧版XLS审计需要PowerShell与Microsoft Excel COM")
    encoded = base64.b64encode(POWERSHELL_READER.encode("utf-16le")).decode("ascii")
    with tempfile.TemporaryDirectory(prefix="tpu-batch10-xls-") as temporary:
        xls_path = Path(temporary) / "Solvent_Free_Adhesives_Dataset_5-2.xls"
        xls_path.write_bytes(payload)
        if _hash(xls_path, "sha256") != EXPECTED_MEMBERS[xls_path.name][1]:
            raise AuditBlocked("临时XLS写入后哈希漂移")
        environment = os.environ.copy()
        environment["TPU_BATCH10_XLS_PATH"] = str(xls_path)
        completed = subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            check=False,
            env=environment,
            timeout=60,
        )
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AuditBlocked(f"Excel COM只读解析失败：{error}")
    try:
        workbook = json.loads(completed.stdout.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditBlocked("Excel COM没有返回严格UTF-8 JSON") from exc
    if workbook.get("read_only") is not True or workbook.get("worksheet_count") != 6:
        raise AuditBlocked("工作簿没有只读打开或工作表数漂移")
    return workbook


def _cell_maps(workbook: dict[str, Any]) -> tuple[dict[str, dict[tuple[int, int], Any]], dict[str, dict[str, int]]]:
    cells: dict[str, dict[tuple[int, int], Any]] = {}
    shapes: dict[str, dict[str, int]] = {}
    for sheet in workbook["sheets"]:
        name = str(sheet["name"])
        if name in cells:
            raise AuditBlocked(f"重复工作表：{name}")
        cells[name] = {
            (int(cell["row"]), int(cell["column"])): cell["value"]
            for cell in sheet["cells"]
        }
        shapes[name] = {
            "used_start_row": int(sheet["used_start_row"]),
            "used_start_column": int(sheet["used_start_column"]),
            "used_rows": int(sheet["used_rows"]),
            "used_columns": int(sheet["used_columns"]),
        }
    expected = set(EXPECTED_SHEET_COUNTS) | {"explanations"}
    if set(cells) != expected:
        raise AuditBlocked(f"工作表集合漂移：{sorted(cells)}")
    return cells, shapes


def _number(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuditBlocked(f"非数值单元格：{context}")
    result = float(value)
    if not math.isfinite(result):
        raise AuditBlocked(f"非有限数值：{context}")
    return result


def _column_letter(column: int) -> str:
    value = column
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _ratio_text(ratio: float) -> str:
    return f"1:{ratio:g}"


def _condition_id(spec: dict[str, Any]) -> str:
    ratio = str(spec["ratio"]).replace(".", "p")
    return (
        f"b10_{str(spec['macrodiol']).lower()}_{str(spec['diisocyanate']).lower()}_"
        f"r{ratio}_t{spec['temperature_c']}"
    )


def _parse_tables(
    workbook: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    cells, shapes = _cell_maps(workbook)
    material_by_code = {row["原料代码"]: row for row in MATERIAL_ROWS}
    specs_by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for spec in COLUMN_SPECS:
        specs_by_condition[_condition_id(spec)].append(spec)

    column_rows: list[dict[str, Any]] = []
    measurement_rows: list[dict[str, Any]] = []
    condition_point_counts: Counter[str] = Counter()
    previous_times: dict[tuple[str, int], float] = {}

    for spec in COLUMN_SPECS:
        sheet = str(spec["sheet"])
        column = int(spec["column"])
        condition_id = _condition_id(spec)
        repeat = int(spec["replicate_index"])
        column_id = f"{condition_id}_rep{repeat}"
        header_rows = (3, 4, 5) if sheet != "PDEA+TDI" else (3, 4)
        raw_headers = [str(cells[sheet].get((row, column), "")) for row in header_rows]
        if not any(raw_headers):
            raise AuditBlocked(f"测量列标题缺失：{sheet}/{_column_letter(column)}")
        same_condition_columns = len(specs_by_condition[condition_id])
        column_rows.append(
            {
                "测量列ID": column_id,
                "条件ID": condition_id,
                "工作表": sheet,
                "工作簿列号": column,
                "工作簿列字母": _column_letter(column),
                "宏二醇代码": spec["macrodiol"],
                "二异氰酸酯代码": spec["diisocyanate"],
                "摩尔比": _ratio_text(float(spec["ratio"])),
                "温度_C": spec["temperature_c"],
                "重复序号": repeat,
                "重复关系": (
                    "同一条件重复滴定" if same_condition_columns > 1 else "该条件仅一个滴定列"
                ),
                "原始表头": " | ".join(raw_headers),
                "独立材料条件": "否",
                "训练权重": "",
                "拆分组": f"{DATASET_DOI}|{condition_id}",
            }
        )

        shape = shapes[sheet]
        last_row = shape["used_start_row"] + shape["used_rows"] - 1
        for row in range(7, last_row + 1):
            raw_nco = cells[sheet].get((row, column))
            if raw_nco is None or raw_nco == "":
                continue
            nco = _number(raw_nco, f"{sheet}!{_column_letter(column)}{row}")
            if nco < 0:
                raise AuditBlocked(f"负%NCO：{sheet}!{_column_letter(column)}{row}")
            raw_time = cells[sheet].get((row, 2))
            context_previous = previous_times.get((sheet, column), "")
            if raw_time is None or raw_time == "":
                time_value: float | str = ""
                time_status = "源表缺失_未插补"
            else:
                time_value = _number(raw_time, f"{sheet}!B{row}")
                if time_value < 0:
                    raise AuditBlocked(f"负反应时间：{sheet}!B{row}")
                previous_times[(sheet, column)] = time_value
                context_previous = time_value
                time_status = "源表已报告"
            point_id = f"{column_id}_row{row}"
            measurement_rows.append(
                {
                    "测量点ID": point_id,
                    "条件ID": condition_id,
                    "测量列ID": column_id,
                    "工作表": sheet,
                    "宏二醇代码": spec["macrodiol"],
                    "二异氰酸酯代码": spec["diisocyanate"],
                    "摩尔比": _ratio_text(float(spec["ratio"])),
                    "温度_C": spec["temperature_c"],
                    "重复序号": repeat,
                    "时间_h_原始": time_value,
                    "时间状态": time_status,
                    "前一非空时间_h_仅上下文": context_previous,
                    "实测NCO_pct": nco,
                    "工作簿理论初始NCO_pct": WORKBOOK_INITIAL_NCO[(sheet, float(spec["ratio"]))],
                    "是否零值": "是" if nco == 0 else "否",
                    "论文TTS用途": "排除零值" if nco == 0 else "可用（仍须按条件分组）",
                    "来源位置": f"{SOURCE_ZIP.name}!/{Path('Solvent_Free_Adhesives_Dataset_5-2.xls').name}#{sheet}!{_column_letter(column)}{row}",
                    "数据来源类型": "实验_二丁胺滴定",
                    "Gold层": "Gold-E",
                    "准入状态": (
                        "conditional_reference" if time_value == "" else "admitted_reference"
                    ),
                    "训练权重": "",
                    "拆分组": f"{DATASET_DOI}|{condition_id}",
                }
            )
            condition_point_counts[condition_id] += 1

    condition_rows: list[dict[str, Any]] = []
    for condition_id, specs in specs_by_condition.items():
        first = specs[0]
        sheet = str(first["sheet"])
        ratio = float(first["ratio"])
        macro = material_by_code[str(first["macrodiol"])]
        iso = material_by_code[str(first["diisocyanate"])]
        paper_value: float | str = PAPER_BATCH_INITIAL_NCO.get((sheet, ratio), "")
        condition_rows.append(
            {
                "条件ID": condition_id,
                "反应体系": sheet,
                "宏二醇代码": macro["原料代码"],
                "宏二醇化学身份": macro["化学身份"],
                "宏二醇CAS": macro["CAS"],
                "宏二醇Mn_g_mol": macro["Mn_g_mol"],
                "二异氰酸酯代码": iso["原料代码"],
                "二异氰酸酯化学身份": iso["化学身份"],
                "二异氰酸酯CAS": iso["CAS"],
                "二异氰酸酯分子量_g_mol": iso["分子量_g_mol"],
                "宏二醇摩尔份": 1,
                "异氰酸酯摩尔份": ratio,
                "摩尔比": _ratio_text(ratio),
                "温度_C": first["temperature_c"],
                "工作簿理论初始NCO_pct": WORKBOOK_INITIAL_NCO[(sheet, ratio)],
                "论文批次理论初始NCO_pct": paper_value,
                "理论值关系": (
                    "论文与工作簿并列保留" if paper_value != "" else "论文批次表未报告_不代填"
                ),
                "测量列数": len(specs),
                "非空NCO点数": condition_point_counts[condition_id],
                "独立材料条件": "是",
                "数据来源类型": "实验_无溶剂PU链增长动力学",
                "Gold层": "Gold-E",
                "准入状态": "admitted_reference",
                "训练权重": "",
                "拆分组": f"{DATASET_DOI}|{condition_id}",
            }
        )

    condition_rows.sort(key=lambda row: str(row["条件ID"]))
    column_rows.sort(key=lambda row: (str(row["工作表"]), int(row["工作簿列号"])))
    measurement_rows.sort(
        key=lambda row: (str(row["工作表"]), str(row["测量列ID"]), str(row["测量点ID"]))
    )

    sheet_counts: dict[str, dict[str, int]] = {}
    for sheet, expected in EXPECTED_SHEET_COUNTS.items():
        actual = {
            "conditions": sum(row["反应体系"] == sheet for row in condition_rows),
            "columns": sum(row["工作表"] == sheet for row in column_rows),
            "points": sum(row["工作表"] == sheet for row in measurement_rows),
        }
        if actual != expected:
            raise AuditBlocked(f"工作表计数漂移：{sheet}: {actual} != {expected}")
        sheet_counts[sheet] = actual
    if len(condition_rows) != 21 or len(column_rows) != 34 or len(measurement_rows) != 171:
        raise AuditBlocked("总条件、测量列或非空%NCO点计数漂移")
    if len({row["条件ID"] for row in condition_rows}) != 21:
        raise AuditBlocked("条件ID不唯一")
    if len({row["测量列ID"] for row in column_rows}) != 34:
        raise AuditBlocked("测量列ID不唯一")
    if len({row["测量点ID"] for row in measurement_rows}) != 171:
        raise AuditBlocked("测量点ID不唯一")

    checks = {
        "sheet_counts": sheet_counts,
        "missing_time_points": sum(row["时间_h_原始"] == "" for row in measurement_rows),
        "zero_nco_points": sum(row["实测NCO_pct"] == 0 for row in measurement_rows),
        "peg_header_only_empty_second_titration_column": "H",
        "all_training_weights_blank": all(
            row["训练权重"] == "" for row in condition_rows + column_rows + measurement_rows
        ),
    }
    if checks["missing_time_points"] != 2:
        raise AuditBlocked("缺失时间测量点数漂移")
    return condition_rows, column_rows, measurement_rows, checks


def _tsv(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    return stream.getvalue().encode("utf-8")


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    if path.name not in OUTPUT_NAMES or path.parent.resolve() != SOURCE_DIR.resolve():
        raise AuditBlocked(f"审计输出不在白名单：{path}")
    if path.is_symlink():
        raise AuditBlocked(f"拒绝覆盖符号链接：{path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".audit.tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


MATERIAL_COLUMNS = tuple(MATERIAL_ROWS[0]) + ("数据集DOI", "训练权重")
CONDITION_COLUMNS = (
    "条件ID", "反应体系", "宏二醇代码", "宏二醇化学身份", "宏二醇CAS",
    "宏二醇Mn_g_mol", "二异氰酸酯代码", "二异氰酸酯化学身份",
    "二异氰酸酯CAS", "二异氰酸酯分子量_g_mol", "宏二醇摩尔份",
    "异氰酸酯摩尔份", "摩尔比", "温度_C", "工作簿理论初始NCO_pct",
    "论文批次理论初始NCO_pct", "理论值关系", "测量列数", "非空NCO点数",
    "独立材料条件", "数据来源类型", "Gold层", "准入状态", "训练权重", "拆分组",
)
COLUMN_COLUMNS = (
    "测量列ID", "条件ID", "工作表", "工作簿列号", "工作簿列字母",
    "宏二醇代码", "二异氰酸酯代码", "摩尔比", "温度_C", "重复序号",
    "重复关系", "原始表头", "独立材料条件", "训练权重", "拆分组",
)
MEASUREMENT_COLUMNS = (
    "测量点ID", "条件ID", "测量列ID", "工作表", "宏二醇代码",
    "二异氰酸酯代码", "摩尔比", "温度_C", "重复序号", "时间_h_原始",
    "时间状态", "前一非空时间_h_仅上下文", "实测NCO_pct",
    "工作簿理论初始NCO_pct", "是否零值", "论文TTS用途", "来源位置",
    "数据来源类型", "Gold层", "准入状态", "训练权重", "拆分组",
)
FILE_COLUMNS = ("文件", "字节数", "SHA256", "MD5", "校验")


def run_audit(*, write_outputs: bool = True) -> dict[str, Any]:
    source_files = [
        _verify_file(SOURCE_DIR / name, *expected)
        for name, expected in EXPECTED_LOCAL.items()
    ]
    if not HASH_MANIFEST.is_file() or HASH_MANIFEST.is_symlink():
        raise AuditBlocked("缺少来源文件哈希清单")
    _validate_api()
    member_rows, workbook_payload = _verify_zip()
    workbook = _read_workbook(workbook_payload)
    conditions, columns, measurements, checks = _parse_tables(workbook)

    material_rows = [
        {**row, "数据集DOI": DATASET_DOI, "训练权重": ""} for row in MATERIAL_ROWS
    ]
    admission_counts = Counter(str(row["准入状态"]) for row in measurements)
    summary = {
        "audit_version": AUDIT_VERSION,
        "source": {
            "title": "TERMINUS WP5: Chain Extension Kinetics for Solvent-Free Adhesive Components",
            "dataset_doi": DATASET_DOI,
            "dataset_url": DATASET_URL,
            "api_url": API_URL,
            "download_url": DOWNLOAD_URL,
            "paper_doi": PAPER_DOI,
            "paper_url": PAPER_URL,
            "license": LICENSE,
            "access": "open",
            "data_origin": "experimental_dibutylamine_titration",
        },
        "counts": {
            "raw_archive_files": 1,
            "zip_members": 2,
            "material_identities": len(material_rows),
            "unique_reaction_conditions": len(conditions),
            "measured_columns": len(columns),
            "nonempty_nco_points": len(measurements),
            "missing_time_points_retained": checks["missing_time_points"],
            "zero_nco_points_retained": checks["zero_nco_points"],
        },
        "sheet_counts": checks["sheet_counts"],
        "measurement_admission_counts": dict(sorted(admission_counts.items())),
        "scientific_classification": {
            "gold_layer": "Gold-E",
            "admission": "reliable_experimental_reference",
            "direct_tpu_mechanical_supervision": False,
            "recommended_uses": [
                "无溶剂PU/TPU合成可行性与反应时间窗建模",
                "宏二醇-二异氰酸酯-温度条件下的NCO消耗动力学代理模型",
                "高通量候选的合成温度和取样时间优先级排序",
                "实验与DFT/MD反应描述符的多保真校准参考",
            ],
            "training_weight_materialized": False,
            "split_materialized": False,
        },
        "reconciliation_checks": checks,
        "limitations": [
            "该来源给出反应动力学，不直接给出TPU拉伸、韧性或DMA标签。",
            "PDEA+TDI工作表有2个%NCO点缺失时间；保留但不插补，并标为条件参考。",
            "PEG+TDI的H列表头写有第二次滴定但整列无数值，因此不计入34个测量列。",
            "理论t=0值与实测滴定点分列保存，不能当作实测标签。",
            "论文批次理论值与工作簿理论值略有差异，两者并列保存而不相互覆盖。",
        ],
    }

    outputs = {
        "内容审计摘要.json": _json(summary),
        "原料身份清单.tsv": _tsv(material_rows, MATERIAL_COLUMNS),
        "反应条件清单.tsv": _tsv(conditions, CONDITION_COLUMNS),
        "测量列清单.tsv": _tsv(columns, COLUMN_COLUMNS),
        "NCO测量长表.tsv": _tsv(measurements, MEASUREMENT_COLUMNS),
        "论文实验协议.json": _json(PAPER_PROTOCOL),
        "文件校验清单.tsv": _tsv(source_files + member_rows, FILE_COLUMNS),
    }
    if tuple(outputs) != OUTPUT_NAMES:
        raise AuditBlocked("审计输出集合漂移")
    if write_outputs:
        for name, payload in outputs.items():
            _atomic_write(SOURCE_DIR / name, payload)
    return {
        "summary": summary,
        "materials": material_rows,
        "conditions": conditions,
        "columns": columns,
        "measurements": measurements,
        "protocol": PAPER_PROTOCOL,
        "files": source_files + member_rows,
        "outputs": outputs,
    }


if __name__ == "__main__":
    result = run_audit(write_outputs=True)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
