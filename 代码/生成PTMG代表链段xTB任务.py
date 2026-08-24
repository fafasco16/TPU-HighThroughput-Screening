"""生成五个商业 PTMG 牌号的单代表链段 xTB 代理任务。

这些任务只表征一个按名义 Mn 选取的确定性低聚链，不代表商品的 Mn/Mw/PDI
分布，也不替代完整 CREST 系综。所有输入通过既有量化任务的稳定 ID 和
SHA-256 连接，任一身份或哈希不闭合时整批停止发布。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from xTB系综任务 import atom_order_sha256


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_XTB_VERSION = "6.7.1"
METHOD = "GFN2-xTB"
REPRESENTATION_SCOPE = "single_oligomer_proxy_for_product_distribution"
_ELEMENT = re.compile(r"^[A-Z][a-z]?$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def validate_release_identity(version: str, binary_hash: str) -> None:
    if str(version) != EXPECTED_XTB_VERSION:
        raise ValueError(
            f"xTB版本必须是{EXPECTED_XTB_VERSION}，收到{version!r}"
        )
    if not _SHA256.fullmatch(str(binary_hash)):
        raise ValueError("xTB二进制SHA-256必须是64位小写十六进制")


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def normalize_single_xyz(path: Path, *, source_label: str) -> tuple[str, tuple[str, ...]]:
    """严格读取单帧 XYZ，并把注释改为可被现有 xTB 门禁解析的数值注释。"""

    if not path.is_file():
        raise ValueError(f"PTMG代表结构不存在: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"无法读取PTMG代表结构: {path}") from exc
    if len(lines) < 3:
        raise ValueError(f"PTMG代表结构不是完整XYZ: {path}")
    try:
        atom_count = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError(f"PTMG代表结构原子数无效: {path}") from exc
    if atom_count <= 0 or len(lines) != atom_count + 2:
        raise ValueError(f"PTMG代表结构不是严格单帧XYZ: {path}")
    elements: list[str] = []
    atom_lines: list[str] = []
    for number, line in enumerate(lines[2:], start=1):
        fields = line.split()
        if len(fields) < 4 or not _ELEMENT.fullmatch(fields[0]):
            raise ValueError(f"PTMG代表结构第{number}个原子行无效: {path}")
        try:
            coordinates = [float(value) for value in fields[1:4]]
        except ValueError as exc:
            raise ValueError(f"PTMG代表结构第{number}个坐标无效: {path}") from exc
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError(f"PTMG代表结构第{number}个坐标不是有限数: {path}")
        elements.append(fields[0])
        atom_lines.append(line.rstrip())
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_label).strip("_")
    comment = f"0.000000000000 representative_seed={safe_label}"
    text = "\n".join([str(atom_count), comment, *atom_lines]) + "\n"
    return text, tuple(elements)


def _required(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def _select_geometry(row: dict[str, Any], source_root: Path) -> tuple[Path, str, str]:
    pre_status = _text(row.get("preoptimization_status"))
    if pre_status == "completed":
        relative = _text(row.get("preoptimized_xyz_file"))
        expected_hash = _text(row.get("preoptimized_xyz_sha256"))
        method = _text(row.get("preoptimization_method")) or "preoptimized"
    else:
        geometry_status = _text(row.get("geometry_status"))
        if geometry_status != "ready":
            raise ValueError(
                f"{row['candidate_id']}没有可发布的预优化或ready初始结构"
            )
        relative = _text(row.get("initial_xyz_file"))
        expected_hash = _text(row.get("initial_xyz_sha256"))
        method = _text(row.get("initial_force_field")) or "initial_geometry"
    if not relative or Path(relative).is_absolute():
        raise ValueError(f"{row['candidate_id']}代表结构路径无效")
    source = (source_root / relative).resolve()
    resolved_root = source_root.resolve()
    if resolved_root not in source.parents:
        raise ValueError(f"{row['candidate_id']}代表结构越出量化根目录")
    if not _SHA256.fullmatch(expected_hash) or not source.is_file():
        raise ValueError(f"{row['candidate_id']}代表结构或登记SHA-256缺失")
    actual_hash = sha256(source)
    if actual_hash != expected_hash:
        raise ValueError(f"{row['candidate_id']}输入SHA-256不一致")
    return source, method, actual_hash


def build_release(
    models: pd.DataFrame,
    quantum_tasks: pd.DataFrame,
    source_root: Path,
    output_root: Path,
    *,
    release_id: str,
    xtb_version: str,
    xtb_binary_sha256: str,
    expected_count: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    validate_release_identity(xtb_version, xtb_binary_sha256)
    if not str(release_id).strip():
        raise ValueError("发布ID不能为空")
    _required(
        models,
        {
            "component_id",
            "nominal_mn_g_mol",
            "repeat_count",
            "representative_smiles",
            "approximation_status",
            "distribution_claim_status",
        },
        "PTMG代表模型",
    )
    _required(
        quantum_tasks,
        {
            "task_index",
            "candidate_id",
            "component_role",
            "task_slug",
            "charge",
            "uhf",
            "geometry_status",
            "initial_xyz_file",
            "initial_xyz_sha256",
            "preoptimization_status",
        },
        "现实构件量化任务",
    )
    if expected_count <= 0:
        raise ValueError("expected_count必须为正")
    if len(models) != expected_count:
        raise ValueError(f"PTMG代表模型必须为{expected_count}行")
    if not models["component_id"].is_unique:
        raise ValueError("PTMG代表模型component_id不唯一")
    quantum = quantum_tasks.loc[
        quantum_tasks["component_role"].astype(str).eq("macrodiol_representative")
    ].copy()
    if not quantum["candidate_id"].is_unique:
        raise ValueError("PTMG量化任务candidate_id不唯一")
    model_ids = set(models["component_id"].astype(str))
    quantum_ids = set(quantum["candidate_id"].astype(str))
    if model_ids != quantum_ids:
        raise ValueError(
            "PTMG模型与量化任务构件集合不一致: "
            f"only_model={sorted(model_ids - quantum_ids)}, "
            f"only_quantum={sorted(quantum_ids - model_ids)}"
        )
    if any(_text(value) != REPRESENTATION_SCOPE for value in models["approximation_status"]):
        raise ValueError("PTMG代表模型approximation_status不符合单链代理范围")
    if any(_text(value) != "no_distribution_claim" for value in models["distribution_claim_status"]):
        raise ValueError("PTMG代表模型不得声明商品分布")

    model_map = models.set_index("component_id", drop=False)
    task_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    for quantum_row in quantum.sort_values("task_index", kind="stable").to_dict(
        orient="records"
    ):
        candidate_id = str(quantum_row["candidate_id"])
        model = model_map.loc[candidate_id]
        if isinstance(model, pd.DataFrame):
            raise ValueError(f"PTMG代表模型身份不唯一: {candidate_id}")
        source, geometry_method, source_hash = _select_geometry(
            quantum_row, source_root
        )
        normalized, elements = normalize_single_xyz(
            source, source_label=f"{candidate_id}_{geometry_method}"
        )
        conformer_hash = _sha256_bytes(normalized.encode("utf-8"))
        identity = "\0".join(
            (candidate_id, source_hash, "1", conformer_hash)
        ).encode("utf-8")
        conformer_id = f"cf_{_sha256_bytes(identity)[:20]}"
        source_index = int(quantum_row["task_index"])
        xtb_task_index = source_index * 1_000_000
        xtb_task_slug = f"{source_index:04d}_000001_{conformer_id}"
        relative_input = Path("输入构象") / f"{xtb_task_slug}.xyz"
        _atomic_text(output_root / relative_input, normalized)
        source_slug = str(quantum_row["task_slug"])
        task_rows.append(
            {
                "descriptor_release_id": release_id,
                "xtb_task_index": xtb_task_index,
                "xtb_task_slug": xtb_task_slug,
                "source_task_index": source_index,
                "source_task_slug": source_slug,
                "candidate_id": candidate_id,
                "component_role": "macrodiol_proxy",
                "commercial_component_role": "macrodiol",
                "conformer_id": conformer_id,
                "crest_rank": 1,
                "crest_energy_hartree": 0.0,
                "crest_ensemble_sha256": source_hash,
                "conformer_xyz_file": relative_input.as_posix(),
                "conformer_xyz_sha256": conformer_hash,
                "atom_count": len(elements),
                "atom_order_sha256": atom_order_sha256(elements),
                "charge": int(quantum_row["charge"]),
                "uhf": int(quantum_row["uhf"]),
                "xtb_version": xtb_version,
                "xtb_binary_sha256": xtb_binary_sha256,
                "method": METHOD,
                "environment_model": "gas_phase",
                "electronic_temperature_k": 300.0,
                "ensemble_temperature_k": 298.15,
                "selection_status": "selected_single_representative_proxy",
                "result_storage_policy": "sharded_tar_gz_v1",
                "failed_workdir_policy": "retain_for_diagnosis",
                "representation_scope": REPRESENTATION_SCOPE,
                "distribution_claim_status": "no_distribution_claim",
                "source_geometry_method": geometry_method,
                "source_geometry_sha256": source_hash,
                "nominal_mn_g_mol": float(model["nominal_mn_g_mol"]),
                "repeat_count": int(model["repeat_count"]),
                "performance_claim_permission": "descriptor_proxy_only",
            }
        )
        source_rows.append(
            {
                "task_index": source_index,
                "task_slug": source_slug,
                "candidate_id": candidate_id,
                "component_role": "macrodiol_proxy",
                "canonical_smiles": str(model["representative_smiles"]),
                "initial_xyz_sha256": source_hash,
                "charge": int(quantum_row["charge"]),
                "uhf": int(quantum_row["uhf"]),
                "representation_scope": REPRESENTATION_SCOPE,
                "nominal_mn_g_mol": float(model["nominal_mn_g_mol"]),
                "repeat_count": int(model["repeat_count"]),
            }
        )
    tasks = pd.DataFrame(task_rows)
    sources = pd.DataFrame(source_rows)
    if len(tasks) != expected_count or not tasks["xtb_task_slug"].is_unique:
        raise ValueError("PTMG xTB任务发布数量或身份不闭合")
    manifest = {
        "release_id": release_id,
        "status": "ready",
        "counts": {"components": len(sources), "conformer_tasks": len(tasks)},
        "xtb_version": xtb_version,
        "xtb_binary_sha256": xtb_binary_sha256,
        "method": METHOD,
        "environment_model": "gas_phase",
        "representation_scope": REPRESENTATION_SCOPE,
        "interpretation_limit": (
            "single deterministic oligomer proxy; not a commercial Mn/Mw/PDI distribution "
            "and not a direct TPU performance label"
        ),
    }
    return tasks, sources, manifest


def write_release(
    models_path: Path,
    quantum_tasks_path: Path,
    source_root: Path,
    output_root: Path,
    *,
    release_id: str,
    xtb_version: str,
    xtb_binary_sha256: str,
    expected_count: int = 5,
) -> dict[str, Any]:
    tasks, sources, manifest = build_release(
        pd.read_csv(models_path),
        pd.read_csv(quantum_tasks_path),
        source_root.resolve(),
        output_root.resolve(),
        release_id=release_id,
        xtb_version=xtb_version,
        xtb_binary_sha256=xtb_binary_sha256,
        expected_count=expected_count,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    task_path = output_root / "xTB构象任务清单.csv"
    source_path = output_root / "PTMG源任务清单.csv"
    _atomic_text(task_path, tasks.to_csv(index=False, float_format="%.12g"))
    _atomic_text(source_path, sources.to_csv(index=False, float_format="%.12g"))
    manifest["files"] = {
        "xTB构象任务清单.csv": {
            "bytes": task_path.stat().st_size,
            "sha256": sha256(task_path),
        },
        "PTMG源任务清单.csv": {
            "bytes": source_path.stat().st_size,
            "sha256": sha256(source_path),
        },
    }
    manifest_path = output_root / "任务发布清单.json"
    _atomic_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--PTMG模型", type=Path, default=ROOT / "数据" / "现实库" / "PTMG代表模型.csv"
    )
    parser.add_argument(
        "--量化任务", type=Path, default=ROOT / "计算" / "现实构件" / "量化任务.csv"
    )
    parser.add_argument(
        "--结构根目录", type=Path, default=ROOT / "计算" / "现实构件"
    )
    parser.add_argument(
        "--输出目录", type=Path, default=ROOT / "计算" / "现实PTMG_xTB"
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-ptmg-xtb-proxy-20260825-v1"
    )
    parser.add_argument("--xTB版本", default=EXPECTED_XTB_VERSION)
    parser.add_argument("--xTB二进制SHA256", required=True)
    args = parser.parse_args(argv)
    manifest = write_release(
        args.PTMG模型,
        args.量化任务,
        args.结构根目录,
        args.输出目录,
        release_id=args.发布ID,
        xtb_version=args.xTB版本,
        xtb_binary_sha256=args.xTB二进制SHA256,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
