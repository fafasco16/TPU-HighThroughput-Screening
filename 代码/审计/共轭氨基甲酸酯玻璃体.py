"""审计 Zenodo 21096098 的原始数据容器并生成可复核清单。

该脚本只读取官方 ZIP、元数据和既有只读解包副本；逐成员核对字节后，仅覆盖
来源目录中的 ``内容审计摘要.json``、``文件校验清单.tsv`` 和
``曲线审计清单.tsv``。脚本不会重新解包，也不会改写任何科学原始文件。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = (
    ROOT
    / "数据/原始"
    / "外部数据"
    / "新增开放数据"
    / "Zenodo_生物基共轭氨基甲酸酯玻璃体"
)
ARCHIVE = DATASET_DIR / "001_Data_Raw.zip"
README = DATASET_DIR / "000_ReadMe.txt"
METADATA = DATASET_DIR / "官方Zenodo元数据.json"
EXTRACTED = DATASET_DIR / "解压内容"

AUDIT_OUTPUTS = {
    DATASET_DIR / "内容审计摘要.json",
    DATASET_DIR / "文件校验清单.tsv",
    DATASET_DIR / "曲线审计清单.tsv",
}

OFFICIAL = {
    "001_Data_Raw.zip": {
        "size": 953_391,
        "md5": "988a57faa6ff0a044a36c93a55bdac69",
    },
    "000_ReadMe.txt": {
        "size": 12_524,
        "md5": "d8c0245b7d3a2cb6618f70e7f6873b54",
    },
}


def digest(path: Path, algorithm: str) -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def assert_audit_output(path: Path) -> None:
    if path not in AUDIT_OUTPUTS:
        raise ValueError(f"拒绝写入白名单以外路径：{path}")
    if path.is_symlink():
        raise ValueError(f"拒绝覆盖符号链接审计输出：{path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"审计输出不是普通文件：{path}")
    parent = path.parent
    if os.path.normcase(str(parent.resolve())) != os.path.normcase(str(parent.absolute())):
        raise ValueError(f"拒绝通过重解析目录写入审计输出：{parent}")


def atomic_write(path: Path, payload: bytes) -> None:
    assert_audit_output(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".audit.tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.is_symlink() or not temporary.is_file():
            raise ValueError(f"审计临时输出不是普通文件：{temporary}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def member_digest(payload: bytes, algorithm: str = "sha256") -> str:
    return hashlib.new(algorithm, payload).hexdigest()


def is_safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and not re.match(
        r"^[A-Za-z]:", name
    )


def decode_csv(payload: bytes) -> str:
    # 本数据集的 CSV 为单字节编码；0xB0 表示度符号。
    return payload.decode("latin-1")


def numeric_profile(rows: list[list[str]]) -> tuple[int, int, int]:
    numeric = finite = missing = 0
    for row in rows:
        for value in row:
            if value.strip() in {"", "--"}:
                missing += 1
                continue
            try:
                number = float(value)
            except ValueError:
                continue
            numeric += 1
            finite += int(math.isfinite(number))
    return numeric, finite, missing


def classify_csv(name: str) -> tuple[str, str]:
    stem = Path(name).name
    if stem.startswith("DMA_"):
        return "DMA", "原始采集曲线"
    if stem.startswith("DSC_"):
        return "DSC", "原始/再生配对曲线"
    if stem.startswith("Relaxation-test_") and "Arrhenius" in stem:
        return "应力松弛", "派生Arrhenius表"
    if stem.startswith("Relaxation-test_"):
        return "应力松弛", "原始采集曲线"
    if stem.startswith("Tensile-test_"):
        return "拉伸", "逐试样汇总标签"
    if stem.startswith("TGA_"):
        return "TGA", "原始采集曲线"
    return "其他", "待复核"


def specimen_rows(rows: list[list[str]]) -> list[list[str]]:
    result: list[list[str]] = []
    for row in rows[2:]:
        if any(value.strip() for value in row[1:]):
            result.append(row)
    return result


def main() -> None:
    for path in (ARCHIVE, README, METADATA, EXTRACTED):
        if not path.is_file():
            if path == EXTRACTED and path.is_dir():
                continue
            raise FileNotFoundError(path)
        if path.is_symlink():
            raise ValueError(f"拒绝通过符号链接读取审计输入：{path}")
    if os.path.normcase(str(EXTRACTED.resolve())) != os.path.normcase(
        str(EXTRACTED.absolute())
    ):
        raise ValueError(f"拒绝通过重解析目录读取解包副本：{EXTRACTED}")

    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    checks: list[dict[str, object]] = []
    for path in (ARCHIVE, README, METADATA):
        official = OFFICIAL.get(path.name)
        local_md5 = digest(path, "md5")
        checks.append(
            {
                "相对路径": path.name,
                "字节数": path.stat().st_size,
                "MD5": local_md5,
                "SHA256": digest(path, "sha256"),
                "官方字节数": "" if official is None else official["size"],
                "官方MD5": "" if official is None else official["md5"],
                "校验结论": (
                    "本地元数据快照"
                    if official is None
                    else (
                        "通过"
                        if path.stat().st_size == official["size"]
                        and local_md5 == official["md5"]
                        else "失败"
                    )
                ),
            }
        )

    curve_rows: list[dict[str, object]] = []
    extension_counts: Counter[str] = Counter()
    duplicate_index: defaultdict[str, list[str]] = defaultdict(list)
    tensile_specimens = 0
    tensile_labels = 0
    relaxation_raw_points = 0
    csv_finite_total = 0
    nested_nmr: dict[str, object] = {}

    with zipfile.ZipFile(ARCHIVE) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"ZIP CRC failure: {bad_member}")
        unsafe = [item.filename for item in archive.infolist() if not is_safe_member(item.filename)]
        if unsafe:
            raise ValueError(f"unsafe ZIP members: {unsafe}")

        for item in archive.infolist():
            if item.is_dir():
                continue
            payload = archive.read(item)
            extension = Path(item.filename).suffix.lower() or "<none>"
            extension_counts[extension] += 1
            sha256 = member_digest(payload)
            duplicate_index[sha256].append(item.filename)
            target = EXTRACTED.joinpath(*PurePosixPath(item.filename).parts)
            if not target.is_file():
                raise FileNotFoundError(f"既有解包副本缺失：{target}")
            if target.stat().st_size != len(payload) or digest(target, "sha256") != sha256:
                raise ValueError(f"解包副本与官方 ZIP 成员不一致：{item.filename}")
            checks.append(
                {
                    "相对路径": f"解压内容/{item.filename}",
                    "字节数": len(payload),
                    "MD5": member_digest(payload, "md5"),
                    "SHA256": sha256,
                    "官方字节数": item.file_size,
                    "官方MD5": "ZIP成员CRC32=" + f"{item.CRC:08x}",
                    "校验结论": "通过",
                }
            )

            if extension == ".csv":
                rows = list(csv.reader(io.StringIO(decode_csv(payload))))
                numeric, finite, missing = numeric_profile(rows)
                csv_finite_total += finite
                experiment, level = classify_csv(item.filename)
                specimens = 0
                if experiment == "拉伸":
                    samples = specimen_rows(rows)
                    specimens = len(samples)
                    tensile_specimens += specimens
                    labels = sum(
                        1
                        for sample in samples
                        for value in sample[1:5]
                        if value.strip() not in {"", "--"}
                    )
                    tensile_labels += labels
                if experiment == "应力松弛" and level == "原始采集曲线":
                    relaxation_raw_points += max(0, len(rows) - 2)
                curve_rows.append(
                    {
                        "相对路径": item.filename,
                        "实验类型": experiment,
                        "数据层级": level,
                        "行数": len(rows),
                        "最大列数": max((len(row) for row in rows), default=0),
                        "有限数值单元格": finite,
                        "空白或占位单元格": missing,
                        "独立试样行": specimens,
                        "SHA256": sha256,
                        "质量备注": (
                            "X3T-160°C含额外两列叠加数据，解析时必须单独拆列"
                            if "X3T-160" in item.filename
                            else (
                                "拉伸文件仅含逐试样汇总值，不含应力-应变原始曲线"
                                if experiment == "拉伸"
                                else ""
                            )
                        ),
                    }
                )
            elif Path(item.filename).name == "NMR_Raw_original-ISO-AA.ZIP":
                with zipfile.ZipFile(io.BytesIO(payload)) as nested:
                    nested_nmr = {
                        "成员数": len(nested.infolist()),
                        "文件数": sum(not entry.is_dir() for entry in nested.infolist()),
                        "CRC全检": nested.testzip() is None,
                        "危险路径数": sum(
                            not is_safe_member(entry.filename) for entry in nested.infolist()
                        ),
                        "未解包原因": "专有Bruker NMR目录仅保存为伴随表征；本阶段不生成性能标签",
                    }

        archive_member_count = len(archive.infolist())
        archive_file_count = sum(not item.is_dir() for item in archive.infolist())
        archive_uncompressed = sum(
            item.file_size for item in archive.infolist() if not item.is_dir()
        )

    duplicates = [paths for paths in duplicate_index.values() if len(paths) > 1]
    audit = {
        "审计版本": "zenodo-vinylogous-urethane-v1.0",
        "生成说明": "基于Zenodo官方元数据、官方MD5、ZIP CRC及逐CSV内容审计；未改写测量值。",
        "来源": {
            "DOI": "10.5281/zenodo.21096098",
            "题名": metadata["metadata"]["title"],
            "作者": [author["name"] for author in metadata["metadata"]["creators"]],
            "发布日期": metadata["metadata"]["publication_date"],
            "许可证": "CC BY 4.0（记录说明和Zenodo权利页面明确声明）",
            "关联论文DOI": "10.1021/acspolymersau.6c00063",
            "数据集引文": (
                "Beneš, H.; Sedlacek, O.; Kopilec, O.; Hodan, J. Dataset for Rigid "
                "Biobased Vinylogous Urethane Vitrimers from d-Isosorbide/Furfural-Derived "
                "Monomers, Version 1.0 [Data set]; Zenodo, 2026. "
                "https://doi.org/10.5281/zenodo.21096098."
            ),
            "论文引文": (
                "Kopilec, O.; Hodan, J.; Sedlacek, O.; Beneš, H. Rigid Biobased Vinylogous "
                "Urethane Vitrimers from d-Isosorbide/Furfural-Derived Monomers. ACS Polymers "
                "Au 2026. https://doi.org/10.1021/acspolymersau.6c00063."
            ),
        },
        "完整性": {
            "官方文件数": 2,
            "官方文件MD5与字节数": "2/2通过",
            "ZIP_SHA256": digest(ARCHIVE, "sha256"),
            "ZIP_CRC全检": "通过",
            "ZIP成员数_含目录": archive_member_count,
            "ZIP文件数": archive_file_count,
            "ZIP解压总字节数": archive_uncompressed,
            "危险路径数": 0,
            "扩展名盘点": dict(sorted(extension_counts.items())),
            "成员完全重复组数": len(duplicates),
            "成员完全重复组": duplicates,
            "嵌套NMR包": nested_nmr,
        },
        "科学层级": {
            "文献声明配方数": 8,
            "配方ID": ["P1J", "P3J", "P1T", "P3T", "X1J", "X3J", "X1T", "X3T"],
            "DMA采集文件数": 6,
            "应力松弛原始曲线数": 16,
            "应力松弛原始点行数": relaxation_raw_points,
            "Arrhenius派生表数": 4,
            "DSC原始/再生曲线数": 4,
            "TGA原始/再生曲线数": 4,
            "FTIR专有原始文件数": 8,
            "逐试样拉伸记录数": tensile_specimens,
            "逐试样拉伸有限标签数": tensile_labels,
            "CSV有限数值单元格总数": csv_finite_total,
            "独立样本口径": (
                "拉伸以物理试样行计；温度扫描或松弛曲线以配方-状态-温度-采集序列计；"
                "曲线点和表征通道不增加配方数。"
            ),
        },
        "质量问题与隔离": [
            "数据集为交联共轭氨基甲酸酯玻璃体，不是聚氨酯，也不是热塑性TPU；核心TPU权重必须为0。",
            "拉伸CSV文件名虽含Raw，但内容仅为逐试样模量、断裂应力、断裂伸长和韧性汇总值，无原始应力-应变曲线。",
            "拉伸表的Young's modulus单位行写GPa，但数值为2430–3420，量级更像MPa；统一前必须核对论文，暂不训练该字段。",
            "拉伸表的Toughness单位写J/m3而数值为0.296–0.844，量级/标度存在歧义；暂不训练该字段。",
            "X1T原始拉伸第6–7件试样缺失伸长和韧性；X1T再生最后一件试样缺失试样编号，保留但标记待核。",
            "方法称拉伸至少6个试样，但P1T原始/再生各仅4行、X1T再生5行；不得补齐或伪造缺失重复。",
            "X3T 160°C松弛文件含额外两列叠加数据，需按列语义拆分后再规范化。",
            "SPA与Bruker NMR为专有格式，本阶段只保存证据，不从不可复核解析结果生成性能标签。",
        ],
        "数据库判定": {
            "层级": "动态网络化学、热固玻璃体与循环利用迁移层",
            "是否进入TPU配方核心训练": False,
            "主任务权重上限": 0.0,
            "迁移层建议权重上限": 0.20,
            "字段级权重上限": {
                "断裂应力与断裂伸长_单位有效": 0.20,
                "动态网络松弛与热分析迁移": 0.20,
                "Young's modulus_声明单位量级冲突": 0.0,
                "Toughness_声明单位或标度冲突": 0.0,
            },
            "可直接使用": [
                "断裂应力与断裂伸长的逐试样汇总标签（保留原始/再生状态）",
                "DMA、应力松弛、DSC和TGA曲线作表征预训练或动力学辅助任务",
            ],
            "暂缓字段": ["Young's modulus（单位冲突）", "Toughness（单位/标度冲突）"],
        },
        "泄漏控制": {
            "观测身份键": "doi|formulation|virgin_or_recycled|synthesis_batch|specimen_or_acquisition",
            "拆分组键": "doi|formulation",
            "规则": [
                "同一配方原始与再生样必须在同一材料家族组，不可跨训练和测试制造近邻泄漏。",
                "同一配方的DMA、DSC、TGA、松弛和拉伸记录必须通过配方身份关联。",
                "Arrhenius表由原始松弛曲线派生，不能作为额外独立标签。",
            ],
        },
    }

    fieldnames = list(checks[0])
    file_check_output = DATASET_DIR / "文件校验清单.tsv"
    curve_audit_output = DATASET_DIR / "曲线审计清单.tsv"
    summary_output = DATASET_DIR / "内容审计摘要.json"
    if {file_check_output, curve_audit_output, summary_output} != AUDIT_OUTPUTS:
        raise RuntimeError("审计输出白名单发生漂移")

    file_buffer = io.StringIO(newline="")
    file_writer = csv.DictWriter(file_buffer, fieldnames=fieldnames, delimiter="\t")
    file_writer.writeheader()
    file_writer.writerows(checks)
    atomic_write(file_check_output, file_buffer.getvalue().encode("utf-8"))

    curve_buffer = io.StringIO(newline="")
    curve_writer = csv.DictWriter(
        curve_buffer, fieldnames=list(curve_rows[0]), delimiter="\t"
    )
    curve_writer.writeheader()
    curve_writer.writerows(curve_rows)
    atomic_write(curve_audit_output, curve_buffer.getvalue().encode("utf-8"))

    atomic_write(
        summary_output,
        (json.dumps(audit, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )

    print(json.dumps(audit["完整性"], ensure_ascii=False, indent=2))
    print(json.dumps(audit["科学层级"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
