"""只读审计本地原始来源，生成三目标定向扩库队列。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "数据" / "原始" / "外部数据" / "新增开放数据"
DIRECTED = ROOT / "结果" / "定向筛选"
LABELS = DIRECTED / "三目标实验标签.csv.gz"
COMPUTATIONAL = DIRECTED / "三目标计算证据.csv.gz"
OUTPUTS = {
    "审计": DIRECTED / "本地来源审计.csv",
    "队列": DIRECTED / "本地扩库队列.csv",
    "说明": DIRECTED / "本地来源审计说明.md",
}
MANIFEST = DIRECTED / "本地来源审计发布清单.json"
RELEASE_ID = "tpu-local-source-audit-2026-08-30-v1"

KEYWORDS = {
    "toughness": [
        "stress-strain",
        "stress strain",
        "tensile",
        "fracture",
        "toughness",
        "energy absorption",
        "拉伸",
        "韧性",
        "断裂",
        "应力应变",
        "力学",
    ],
    "cyclic_recovery": [
        "cyclic",
        "cycle",
        "loading-unloading",
        "loading unloading",
        "hysteresis",
        "recovery",
        "residual strain",
        "fatigue",
        "stress relaxation",
        "shape memory",
        "循环",
        "加载卸载",
        "滞后",
        "恢复",
        "残余应变",
        "疲劳",
        "松弛",
        "形状记忆",
    ],
    "thermal_stability": [
        "tga",
        "dtg",
        "thermograv",
        "decomposition",
        "degradation",
        "thermal stability",
        "dsc",
        "热重",
        "分解",
        "热稳定",
        "热降解",
        "玻璃化",
        "热分析",
    ],
    "formulation": [
        "formulation",
        "composition",
        "recipe",
        "synthesis",
        "polyol",
        "isocyanate",
        "chain extender",
        "nco",
        "配方",
        "组成",
        "合成",
        "多元醇",
        "异氰酸酯",
        "扩链剂",
        "原料",
    ],
    "raw_curve": [
        "raw data",
        "source data",
        "primary data",
        "curve",
        "stress-strain",
        "loading-unloading",
        "tga-dtg",
        "原始数据",
        "源数据",
        "曲线",
    ],
    "license": [
        "license",
        "licence",
        "cc by",
        "mit license",
        "bsd",
        "许可证",
        "许可",
    ],
}

MACHINE_EXTENSIONS = {
    ".csv",
    ".tsv",
    ".xlsx",
    ".xls",
    ".json",
    ".yaml",
    ".yml",
    ".parquet",
    ".txt",
}
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2"}
TEXT_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv"}
SIGNAL_OVERRIDES = {
    "Texas_湿干单根电纺PU纤维力学": {
        "toughness": False,
        "formulation": False,
    },
    "MaterialsCloud_商用PU泡沫多轴断裂力学": {
        "cyclic_recovery": False,
    },
    "Mendeley_TPU95A_TPMS应变率力学": {
        "toughness": False,
    },
    "SND_TPU导电轨迹循环拉伸": {
        "toughness": False,
        "cyclic_recovery": False,
        "raw_curve": False,
    },
    "Mendeley_热可逆超分子PU宽应变率": {
        "thermal_stability": False,
    },
    "第十批实验_无溶剂PU反应动力学": {
        "toughness": False,
    },
    "Figshare_商用PUR形状记忆本构FEA": {
        "cyclic_recovery": False,
    },
    "MDPI_MDI聚醚双组分PU分子动力学": {
        "toughness": False,
    },
    "ScienceDB_微孔PU动态力学": {
        "toughness": False,
    },
    "Zenodo_导电自修复可回收PU复合材料": {
        "formulation": True,
        "raw_curve": True,
    },
}


def classify_text(text: str) -> dict[str, bool]:
    lowered = text.lower()
    return {
        name: any(keyword in lowered for keyword in keywords)
        for name, keywords in KEYWORDS.items()
    }


def _read_text_sample(path: Path, limit: int = 65_536) -> str:
    payload = path.read_bytes()[:limit]
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _xlsx_sheet_names(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            payload = archive.read("xl/workbook.xml")
        root = ElementTree.fromstring(payload)
        return [
            element.attrib.get("name", "")
            for element in root.iter()
            if element.tag.endswith("}sheet")
        ]
    except (KeyError, OSError, zipfile.BadZipFile, ElementTree.ParseError):
        return []


def _zip_member_names(path: Path, limit: int = 2_000) -> list[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            return [item.filename for item in archive.infolist()[:limit]]
    except (OSError, zipfile.BadZipFile):
        return []


def _inventory_fingerprint(source_dir: Path, files: list[Path]) -> str:
    rows = [
        f"{path.relative_to(source_dir).as_posix()}|{path.stat().st_size}"
        for path in sorted(files)
    ]
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _directed_coverage() -> dict[str, dict[str, int]]:
    if not LABELS.is_file() and not COMPUTATIONAL.is_file():
        return {}
    coverage: dict[str, dict[str, int]] = {}
    pattern = re.compile(r"新增开放数据[/\\]([^/\\]+)")
    for evidence_path in (LABELS, COMPUTATIONAL):
        if not evidence_path.is_file():
            continue
        evidence = pd.read_csv(
            evidence_path,
            usecols=["target_family", "source_locator"],
            low_memory=False,
        )
        for row in evidence.itertuples(index=False):
            match = pattern.search(str(row.source_locator))
            if not match:
                continue
            directory = match.group(1)
            target = str(row.target_family)
            coverage.setdefault(directory, {})[target] = (
                coverage.setdefault(directory, {}).get(target, 0) + 1
            )
    expansions = [
        ("DRUM机械回收拉伸端点.csv", "DRUM_TPUU_机械回收", "toughness"),
        ("DRUM机械回收循环端点.csv", "DRUM_TPUU_机械回收", "cyclic_recovery"),
        ("DRUM机械回收TGA端点.csv", "DRUM_TPUU_机械回收", "thermal_stability"),
        ("TPUU循环端点.csv", "DRUM_TPUU_低天花板", "cyclic_recovery"),
        ("低天花板TPUU热稳定端点.csv", "DRUM_TPUU_低天花板", "thermal_stability"),
        ("Zenodo多孔TPU拉伸端点.csv", "Zenodo_多孔导电TPU纳米复合膜", "toughness"),
        ("Figshare强韧自愈端点.csv", "Figshare_碳酸酯TPU强韧自愈", "toughness"),
        ("标准化热塑性弹性体拉伸端点.csv", "Zenodo_标准化弹性体表征", "toughness"),
        ("标准热塑性弹性体松弛端点.csv", "Zenodo_标准化弹性体表征", "cyclic_recovery"),
        ("标准化热塑性弹性体TGA端点.csv", "Zenodo_标准化弹性体表征", "thermal_stability"),
        ("PHCU双目标端点.csv", "第八批实验_非异氰酸酯PHCU热塑性聚氨酯", "toughness"),
        ("PHCU双目标端点.csv", "第八批实验_非异氰酸酯PHCU热塑性聚氨酯", "thermal_stability"),
        ("TGA热稳定端点.csv", "第十三批实验_日期籽油PU-PIR", "thermal_stability"),
        ("QUB生物基自修复TPU拉伸端点.csv", "QUB_生物基三重自修复TPU", "toughness"),
        ("QUB生物基自修复TPU循环端点.csv", "QUB_生物基三重自修复TPU", "cyclic_recovery"),
        ("QUB生物基自修复TPUTGA端点.csv", "QUB_生物基三重自修复TPU", "thermal_stability"),
        ("DataInBrief形状记忆PU拉伸端点.csv", "DataInBrief_聚氨酯形状记忆多模态原始数据", "toughness"),
        ("DataInBrief形状记忆PU循环端点.csv", "DataInBrief_聚氨酯形状记忆多模态原始数据", "cyclic_recovery"),
        ("DataInBrief形状记忆PU热稳定端点.csv", "DataInBrief_聚氨酯形状记忆多模态原始数据", "thermal_stability"),
        ("商业TPU温度疲劳端点.csv", "Mendeley_商业TPU温度疲劳多工况", "toughness"),
        ("商业TPU温度疲劳端点.csv", "Mendeley_商业TPU温度疲劳多工况", "cyclic_recovery"),
        ("商业TPU恢复配对端点.csv", "Mendeley_商业TPU温度疲劳多工况", "cyclic_recovery"),
        ("Tecoflex药物复合TPU多性能端点.csv", "Zenodo_Tecoflex药物复合TPU", "toughness"),
        ("Tecoflex药物复合TPU多性能端点.csv", "Zenodo_Tecoflex药物复合TPU", "thermal_stability"),
        ("IIR-OH聚氨酯循环端点.csv", "第十八批实验_IIR-OH聚氨酯", "cyclic_recovery"),
        ("IIR-OH聚氨酯水解保持端点.csv", "第十八批实验_IIR-OH聚氨酯", "cyclic_recovery"),
        ("IIR-OH聚氨酯水解保持端点.csv", "第十八批实验_IIR-OH聚氨酯", "toughness"),
        ("TPU95A应力松弛端点.csv", "Mendeley_TPU95A_TPMS应变率力学", "cyclic_recovery"),
        ("PCF20泡沫拉伸断裂端点.csv", "MaterialsCloud_商用PU泡沫多轴断裂力学", "toughness"),
        ("TPU1301拉伸端点.csv", "Zenodo_TPU1301热黏弹黏塑本构", "toughness"),
        ("TPU1301应力松弛端点.csv", "Zenodo_TPU1301热黏弹黏塑本构", "cyclic_recovery"),
        ("生物基玻璃体拉伸端点.csv", "Zenodo_生物基共轭氨基甲酸酯玻璃体", "toughness"),
        ("生物基玻璃体松弛端点.csv", "Zenodo_生物基共轭氨基甲酸酯玻璃体", "cyclic_recovery"),
        ("生物基玻璃体TGA端点.csv", "Zenodo_生物基共轭氨基甲酸酯玻璃体", "thermal_stability"),
        ("PCU85单纤维循环端点.csv", "Texas_湿干单根电纺PU纤维力学", "cyclic_recovery"),
        ("PU高低速松弛工况端点.csv", "Figshare_PU高低速变形后应力松弛", "cyclic_recovery"),
        ("PU铜热解TGA端点.csv", "第八批混合_PU铜调控热解多尺度", "thermal_stability"),
        ("FDM_TPU晶格基材力学端点.csv", "Mendeley_FDM_TPU晶格与基材力学", "toughness"),
        ("PU微球复合加载卸载端点.csv", "Zenodo_PU微球复合材料拉伸", "toughness"),
        ("PU微球复合加载卸载端点.csv", "Zenodo_PU微球复合材料拉伸", "cyclic_recovery"),
        ("SLS_TPU1301工艺拉伸端点.csv", "Mendeley_SLS_TPU工艺力学", "toughness"),
        ("PU泡沫动态压缩端点.csv", "Mendeley_PU泡沫动态力学_精选表", "toughness"),
        ("导电自修复PU拉伸与回收端点.csv", "Zenodo_导电自修复可回收PU复合材料", "toughness"),
        ("导电自修复PU恢复文献指标.csv", "Zenodo_导电自修复可回收PU复合材料", "cyclic_recovery"),
    ]
    for filename, directory, target in expansions:
        expansion = DIRECTED / filename
        if expansion.is_file():
            count = len(pd.read_csv(expansion))
            coverage.setdefault(directory, {})[target] = (
                coverage.setdefault(directory, {}).get(target, 0) + count
            )
    return coverage


def _evidence_items(corpus_items: list[str]) -> list[str]:
    all_keywords = [keyword for values in KEYWORDS.values() for keyword in values]
    selected = []
    for item in corpus_items:
        lowered = item.lower()
        if any(keyword in lowered for keyword in all_keywords):
            selected.append(item.replace("\n", " ")[:240])
        if len(selected) >= 12:
            break
    return selected


def _audit_source(
    source_dir: Path,
    directed_coverage: dict[str, dict[str, int]],
) -> dict[str, object]:
    files = sorted(path for path in source_dir.rglob("*") if path.is_file())
    extension_counts = Counter(path.suffix.lower() or "[none]" for path in files)
    direct_items = [source_dir.name]
    direct_items.extend(path.relative_to(source_dir).as_posix() for path in files)
    context_items = list(direct_items)
    for path in files:
        suffix = path.suffix.lower()
        if suffix in {".csv", ".tsv", ".txt"} and path.stat().st_size <= 5 * 1024 * 1024:
            sample = _read_text_sample(path)
            direct_items.append(sample.splitlines()[0] if sample else "")
            context_items.append(sample)
        elif suffix in {".md", ".json", ".yaml", ".yml"} and path.stat().st_size <= 5 * 1024 * 1024:
            context_items.append(_read_text_sample(path))
        elif suffix == ".xlsx":
            direct_items.extend(_xlsx_sheet_names(path))
        elif suffix == ".zip":
            direct_items.extend(_zip_member_names(path))
    direct_flags = classify_text("\n".join(direct_items))
    context_flags = classify_text("\n".join(context_items))
    flags = {
        "toughness": direct_flags["toughness"],
        "cyclic_recovery": direct_flags["cyclic_recovery"],
        "thermal_stability": direct_flags["thermal_stability"],
        "raw_curve": direct_flags["raw_curve"],
        "formulation": context_flags["formulation"],
        "license": context_flags["license"],
    }
    coverage = directed_coverage.get(source_dir.name, {})
    for target in ("toughness", "cyclic_recovery", "thermal_stability"):
        flags[target] = flags[target] or target in coverage
    for signal, value in SIGNAL_OVERRIDES.get(source_dir.name, {}).items():
        flags[signal] = value
    target_names = [
        name
        for name in ("toughness", "cyclic_recovery", "thermal_stability")
        if flags[name]
    ]
    machine_count = sum(extension_counts[ext] for ext in MACHINE_EXTENSIONS)
    archive_count = sum(extension_counts[ext] for ext in ARCHIVE_EXTENSIONS)
    machine_readable = machine_count > 0
    manifest_signal = any(
        any(token in path.name.lower() for token in ("manifest", "readme", "来源", "审计"))
        for path in files
    )
    score = (
        4 * len(target_names)
        + 3 * int(flags["formulation"])
        + 2 * int(machine_readable)
        + 2 * int(flags["raw_curve"])
        + int(manifest_signal)
        + int(flags["license"])
    )
    if not target_names:
        priority = "exclude"
        status = "exclude_no_target_signal"
        next_action = "不进入三目标扩库；保留原始档案"
    elif score >= 12:
        priority = "high"
        status = "ready_for_source_adapter" if machine_readable else "manual_review"
        next_action = "优先解析机器表、曲线、配方和协议并做去重"
    elif score >= 8:
        priority = "medium"
        status = "needs_formulation_mapping" if machine_readable else "needs_archive_review"
        next_action = "先核对压缩包/工作表和组分映射，再决定接入"
    else:
        priority = "low"
        status = "reference_or_manual_review"
        next_action = "只作补充参考，除非高优先级来源仍不足"
    if target_names and set(target_names) <= set(coverage):
        status = "materialized_all_detected_targets"
        next_action = "已接入；仅在补齐化学映射或新增独立模态时继续"
    elif coverage:
        status = "partially_materialized"
        next_action = "保留已接入目标，仅处理尚未物化的目标信号"
    if source_dir.name == "第七批计算_异山梨醇动态聚氨酯多尺度力学":
        status = "blocked_data_rights"
        next_action = "等待上游仓库明确数据许可；仅保留本地Gold-C参考，禁止公开数值再分发"
    if source_dir.name == "第十三批实验_日期籽油PU-PIR":
        status = "blocked_units_protocol"
        next_action = "等待正文图轴/方法或作者补证单位与协议；禁止生成绝对韧性标签"
    return {
        "release_id": RELEASE_ID,
        "source_directory": source_dir.name,
        "source_path": str(source_dir.relative_to(ROOT)).replace("\\", "/"),
        "total_file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "machine_readable_file_count": machine_count,
        "archive_file_count": archive_count,
        "pdf_file_count": extension_counts[".pdf"],
        "extension_summary": ";".join(
            f"{extension}:{count}" for extension, count in sorted(extension_counts.items())
        ),
        "toughness_signal": flags["toughness"],
        "cyclic_recovery_signal": flags["cyclic_recovery"],
        "thermal_stability_signal": flags["thermal_stability"],
        "formulation_signal": flags["formulation"],
        "raw_curve_signal": flags["raw_curve"],
        "license_signal": flags["license"],
        "manifest_signal": manifest_signal,
        "machine_readable_signal": machine_readable,
        "existing_directed_targets": ";".join(sorted(coverage)),
        "existing_directed_row_count": sum(coverage.values()),
        "priority_score": score,
        "priority_class": priority,
        "audit_status": status,
        "next_action": next_action,
        "evidence_excerpt": " | ".join(_evidence_items(direct_items)),
        "inventory_fingerprint": _inventory_fingerprint(source_dir, files),
    }


def _build_queue(audit: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    target_columns = {
        "toughness": "toughness_signal",
        "cyclic_recovery": "cyclic_recovery_signal",
        "thermal_stability": "thermal_stability_signal",
    }
    for record in audit.to_dict(orient="records"):
        for target, column in target_columns.items():
            if not record[column]:
                continue
            existing_targets = set(str(record["existing_directed_targets"]).split(";"))
            rows.append(
                {
                    "release_id": RELEASE_ID,
                    "source_directory": record["source_directory"],
                    "target_family": target,
                    "priority_score": record["priority_score"],
                    "priority_class": record["priority_class"],
                    "audit_status": record["audit_status"],
                    "already_in_directed_target": target in existing_targets,
                    "machine_readable_file_count": record[
                        "machine_readable_file_count"
                    ],
                    "archive_file_count": record["archive_file_count"],
                    "formulation_signal": record["formulation_signal"],
                    "raw_curve_signal": record["raw_curve_signal"],
                    "next_action": (
                        "已接入；仅在新增独立数据或改进映射时更新"
                        if target in existing_targets
                        else record["next_action"]
                    ),
                    "evidence_excerpt": record["evidence_excerpt"],
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["priority_score", "source_directory", "target_family"],
        ascending=[False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)


def _readme(audit: pd.DataFrame, queue: pd.DataFrame) -> str:
    priority_counts = audit["priority_class"].value_counts().to_dict()
    target_counts = {
        target: int(queue["target_family"].eq(target).sum())
        for target in ("toughness", "cyclic_recovery", "thermal_stability")
    }
    top = queue.drop_duplicates("source_directory").head(20)
    top_rows = "\n".join(
        f"| {row.source_directory} | {row.priority_score} | {row.priority_class} | {row.audit_status} |"
        for row in top.itertuples(index=False)
    )
    return f"""# 本地三目标来源审计

- 版本：`{RELEASE_ID}`
- 来源目录：{len(audit)}
- 高优先级：{priority_counts.get('high', 0)}
- 中优先级：{priority_counts.get('medium', 0)}
- 低优先级：{priority_counts.get('low', 0)}
- 排除：{priority_counts.get('exclude', 0)}

目标队列行数：韧性{target_counts['toughness']}、循环恢复{target_counts['cyclic_recovery']}、热稳定{target_counts['thermal_stability']}。一个来源可同时进入多个目标，但来源只计一次。

## 审计边界

审计器只读取文件名、文本前64 KiB、XLSX工作表名和ZIP前2000个成员名，不解压或改写原件。优先级表示“值得进一步解析”，不是已经确认存在可训练标签。每个高优先级来源仍需核验配方、单位、协议、许可证和来源族去重。

## 首批优先来源

| 来源目录 | 分数 | 优先级 | 当前结论 |
|---|---:|---|---|
{top_rows}

机器表和原始曲线优先于图像OCR；已有定向记录的来源优先补配方/协议，不重复物化相同试样。完整结果见`本地来源审计.csv`与`本地扩库队列.csv`。
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _entry(path: Path) -> dict[str, object]:
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_outputs(audit: pd.DataFrame, queue: pd.DataFrame, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    audit.to_csv(
        directory / OUTPUTS["审计"].name,
        index=False,
        encoding="utf-8-sig",
        lineterminator="\n",
    )
    queue.to_csv(
        directory / OUTPUTS["队列"].name,
        index=False,
        encoding="utf-8-sig",
        lineterminator="\n",
    )
    (directory / OUTPUTS["说明"].name).write_text(
        _readme(audit, queue), encoding="utf-8"
    )


def _root_fingerprint(audit: pd.DataFrame) -> str:
    payload = "\n".join(
        f"{row.source_directory}|{row.total_file_count}|{row.total_bytes}|{row.inventory_fingerprint}"
        for row in audit.itertuples(index=False)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _manifest(audit: pd.DataFrame, queue: pd.DataFrame) -> dict[str, object]:
    return {
        "release_id": RELEASE_ID,
        "counts": {
            "source_directory_count": len(audit),
            "queued_source_target_rows": len(queue),
            "high_priority_source_count": int(
                audit["priority_class"].eq("high").sum()
            ),
            "medium_priority_source_count": int(
                audit["priority_class"].eq("medium").sum()
            ),
        },
        "source_root": str(SOURCE_ROOT.relative_to(ROOT)).replace("\\", "/"),
        "source_root_inventory_fingerprint": _root_fingerprint(audit),
        "outputs": {name: _entry(path) for name, path in OUTPUTS.items()},
        "scan_policy": {
            "raw_files_modified": False,
            "zip_member_limit_per_archive": 2000,
            "text_sample_bytes": 65536,
            "priority_is_confirmation": False,
        },
    }


def build_release() -> tuple[pd.DataFrame, pd.DataFrame, str]:
    coverage = _directed_coverage()
    rows = [
        _audit_source(source_dir, coverage)
        for source_dir in sorted(path for path in SOURCE_ROOT.iterdir() if path.is_dir())
    ]
    audit = pd.DataFrame(rows).sort_values(
        ["priority_score", "source_directory"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    queue = _build_queue(audit)
    return audit, queue, _readme(audit, queue)


def write_release(audit: pd.DataFrame, queue: pd.DataFrame) -> None:
    _write_outputs(audit, queue, DIRECTED)
    MANIFEST.write_text(
        json.dumps(_manifest(audit, queue), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def check_release(audit: pd.DataFrame, queue: pd.DataFrame) -> None:
    if not MANIFEST.is_file() or not all(path.is_file() for path in OUTPUTS.values()):
        raise SystemExit("缺少本地来源审计发布；请先运行生成模式")
    with tempfile.TemporaryDirectory(prefix="tpu-local-audit-check-") as directory:
        temporary = Path(directory)
        _write_outputs(audit, queue, temporary)
        for name, path in OUTPUTS.items():
            if _sha256(temporary / path.name) != _sha256(path):
                raise SystemExit(f"本地来源审计输出不一致：{name}")
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(audit, queue):
        raise SystemExit("本地来源审计发布清单不一致")
    print("本地三目标来源审计检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    audit, queue, _ = build_release()
    if args.检查:
        check_release(audit, queue)
    else:
        write_release(audit, queue)
        print(json.dumps(_manifest(audit, queue)["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
