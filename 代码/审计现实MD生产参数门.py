"""拆解GAFF2替代参数并探测RadonPy生产级RESP电荷运行门。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import math
import platform
import re
import sys
from pathlib import Path
from typing import Any, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MESSAGE_PATTERN = re.compile(
    r"Using alternate (?P<parameter_class>bond|angle|dihedral|improper) type "
    r"(?P<alternate_type>.+?) instead of (?P<requested_type>.+?)$"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def parse_alternate_message(message: str) -> dict[str, str]:
    match = MESSAGE_PATTERN.search(message.strip())
    if not match:
        raise ValueError(f"无法解析GAFF2替代参数消息: {message}")
    row = match.groupdict()
    requested_tokens = set(row["requested_type"].split(","))
    if "ns" in requested_tokens:
        family = "repeating_urethane_ns_substitution"
    elif requested_tokens.intersection({"cg", "ch"}):
        family = "terminal_isocyanate_conjugated_type_substitution"
    else:
        family = "other_requires_manual_review"
    row["validation_family"] = family
    return row


def analyze_alternates(
    audit: pd.DataFrame, plan: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    audit_required = {
        "formulation_id",
        "atom_count",
        "assignment_status",
        "alternate_parameter_line_count",
        "alternate_parameter_unique_count",
        "alternate_parameter_unique_messages",
        "production_md_permission",
    }
    plan_required = {"formulation_id", "estimated_urethane_bond_count"}
    missing_audit = sorted(audit_required.difference(audit.columns))
    missing_plan = sorted(plan_required.difference(plan.columns))
    if missing_audit or missing_plan:
        raise ValueError(
            f"输入缺字段: GAFF2={missing_audit}, 计量计划={missing_plan}"
        )
    if audit.empty or not audit["formulation_id"].is_unique:
        raise ValueError("GAFF2审计formulation_id必须非空唯一")
    if not audit["assignment_status"].astype(str).eq(
        "assigned_with_alternate_parameters"
    ).all():
        raise ValueError("当前生产门审计要求所有输入均明确携带替代参数")
    if not plan["formulation_id"].is_unique:
        raise ValueError("计量计划formulation_id必须唯一")

    formulation_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    plan_counts = plan.set_index("formulation_id")["estimated_urethane_bond_count"]
    for source in audit.sort_values("formulation_id", kind="stable").to_dict(
        orient="records"
    ):
        formulation_id = str(source["formulation_id"])
        if formulation_id not in plan_counts.index:
            raise ValueError(f"GAFF2审计配方未出现在计量计划: {formulation_id}")
        messages = [
            item.strip()
            for item in str(source["alternate_parameter_unique_messages"]).split(" | ")
            if item.strip()
        ]
        if len(messages) != int(source["alternate_parameter_unique_count"]):
            raise ValueError(f"{formulation_id}唯一消息数不闭合")
        parsed = [parse_alternate_message(message) for message in messages]
        family_counts = pd.Series(
            [item["validation_family"] for item in parsed], dtype="string"
        ).value_counts()
        urethane_bonds = int(plan_counts.loc[formulation_id])
        line_count = int(source["alternate_parameter_line_count"])
        formulation_rows.append(
            {
                "formulation_id": formulation_id,
                "atom_count": int(source["atom_count"]),
                "estimated_urethane_bond_count": urethane_bonds,
                "alternate_parameter_event_count": line_count,
                "alternate_parameter_unique_count": len(parsed),
                "events_per_estimated_urethane_bond": line_count / urethane_bonds,
                "repeating_urethane_ns_unique_count": int(
                    family_counts.get("repeating_urethane_ns_substitution", 0)
                ),
                "terminal_nco_unique_count": int(
                    family_counts.get(
                        "terminal_isocyanate_conjugated_type_substitution", 0
                    )
                ),
                "other_unique_count": int(
                    family_counts.get("other_requires_manual_review", 0)
                ),
                "parameter_validation_status": (
                    "blocked_repeating_urethane_and_terminal_nco_substitutions"
                ),
                "performance_claim_status": "no_performance_claim",
            }
        )
        for item in parsed:
            event_rows.append({"formulation_id": formulation_id, **item})

    event_frame = pd.DataFrame(event_rows)
    grouped = (
        event_frame.groupby(
            [
                "validation_family",
                "parameter_class",
                "requested_type",
                "alternate_type",
            ],
            sort=True,
            dropna=False,
        )
        .agg(formulation_count=("formulation_id", "nunique"))
        .reset_index()
    )
    grouped["formulation_fraction"] = grouped["formulation_count"] / len(audit)
    grouped["validation_priority"] = grouped["validation_family"].map(
        {
            "repeating_urethane_ns_substitution": "P0_repeating_backbone",
            "terminal_isocyanate_conjugated_type_substitution": "P1_end_group",
            "other_requires_manual_review": "P0_manual_review",
        }
    )
    grouped["production_md_permission"] = "blocked_pending_parameter_validation"
    grouped = grouped.sort_values(
        ["validation_priority", "parameter_class", "requested_type"], kind="stable"
    ).reset_index(drop=True)

    formulation_frame = pd.DataFrame(formulation_rows).sort_values(
        "formulation_id", kind="stable"
    ).reset_index(drop=True)
    correlation = float(
        formulation_frame["estimated_urethane_bond_count"].corr(
            formulation_frame["alternate_parameter_event_count"]
        )
    )
    family_counts = (
        grouped.groupby("validation_family")["requested_type"].count().to_dict()
    )
    summary = {
        "formulation_count": len(formulation_frame),
        "unique_substitution_count": len(grouped),
        "parameter_class_counts": grouped["parameter_class"].value_counts().to_dict(),
        "validation_family_counts": family_counts,
        "urethane_bond_vs_substitution_event_pearson_r": correlation,
        "all_formulations_blocked": bool(
            formulation_frame["parameter_validation_status"]
            .astype(str)
            .str.startswith("blocked_")
            .all()
        ),
    }
    return grouped, formulation_frame, summary


def probe_radonpy_charge_runtime() -> dict[str, Any]:
    module_names = ["radonpy", "psi4", "resp", "setuptools", "distutils"]
    availability: dict[str, bool] = {}
    for name in module_names:
        try:
            availability[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            availability[name] = False
    result: dict[str, Any] = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "module_availability": availability,
        "radonpy_resp_runtime_ready": False,
        "charge_gate_status": "blocked_radonpy_or_qm_runtime_missing",
    }
    if not availability["radonpy"]:
        return result

    try:
        result["radonpy_distribution_version"] = importlib.metadata.version(
            "radonpy-pypi"
        )
    except importlib.metadata.PackageNotFoundError:
        result["radonpy_distribution_version"] = "not_found"
    try:
        from radonpy.core import calc

        signature = inspect.signature(calc.assign_charges)
        result["radonpy_charge_signature"] = str(signature)
        result["radonpy_qm_wrapper_available"] = bool(
            getattr(calc, "qm_avail", False)
        )
        result["radonpy_resp_defaults"] = {
            name: signature.parameters[name].default
            for name in [
                "opt_method",
                "opt_basis",
                "charge_method",
                "charge_basis",
                "qm_solver",
            ]
        }
        source_path = Path(inspect.getsourcefile(calc.assign_charges) or "")
        if source_path.is_file():
            result["radonpy_charge_source"] = {
                "path": str(source_path),
                "bytes": source_path.stat().st_size,
                "sha256": sha256(source_path),
            }
        ready = bool(
            result["radonpy_qm_wrapper_available"]
            and availability["psi4"]
            and availability["resp"]
        )
        result["radonpy_resp_runtime_ready"] = ready
        result["charge_gate_status"] = (
            "ready_for_small_fragment_resp_validation"
            if ready
            else "blocked_missing_psi4_resp_or_wrapper_compatibility"
        )
    except Exception as exc:
        result["radonpy_import_error_type"] = type(exc).__name__
        result["radonpy_import_error"] = str(exc).encode(
            "utf-8", errors="backslashreplace"
        ).decode("utf-8")
        result["charge_gate_status"] = "blocked_radonpy_charge_import_error"
    return result


def write_release(
    audit_path: Path,
    plan_path: Path,
    environment_manifest_path: Path,
    output_root: Path,
    *,
    release_id: str,
    resp_smoke_path: Path | None = None,
    radonpy_resp_failure_path: Path | None = None,
    resp_sensitivity_path: Path | None = None,
    joint_resp_path: Path | None = None,
    resp_core_transfer_path: Path | None = None,
) -> dict[str, Any]:
    required_paths = [audit_path, plan_path, environment_manifest_path]
    optional_paths = [
        path
        for path in (
            resp_smoke_path,
            radonpy_resp_failure_path,
            resp_sensitivity_path,
            joint_resp_path,
            resp_core_transfer_path,
        )
        if path is not None
    ]
    for path in [*required_paths, *optional_paths]:
        if not path.is_file():
            raise ValueError(f"输入文件不存在: {path}")
    detail, formulations, summary = analyze_alternates(
        pd.read_csv(audit_path), pd.read_csv(plan_path)
    )
    runtime = probe_radonpy_charge_runtime()
    if resp_smoke_path is not None:
        resp_smoke = json.loads(resp_smoke_path.read_text(encoding="utf-8"))
        runtime["native_resp_smoke"] = {
            "status": resp_smoke.get("status"),
            "fragment_name": resp_smoke.get("fragment_name"),
            "method": resp_smoke.get("method"),
            "basis": resp_smoke.get("basis"),
            "vdw_point_density": resp_smoke.get("vdw_point_density"),
            "charge_metrics": resp_smoke.get("charge_metrics"),
            "path": str(resp_smoke_path),
            "sha256": sha256(resp_smoke_path),
        }
    if radonpy_resp_failure_path is not None:
        failure = json.loads(
            radonpy_resp_failure_path.read_text(encoding="utf-8")
        )
        runtime["radonpy_resp_wrapper_smoke"] = {
            "status": failure.get("status"),
            "attempt_count": len(failure.get("attempts", [])),
            "vdw_point_density": failure.get(
                "radonpy_hardcoded_vdw_point_density"
            ),
            "path": str(radonpy_resp_failure_path),
            "sha256": sha256(radonpy_resp_failure_path),
        }
    if resp_sensitivity_path is not None:
        sensitivity = json.loads(
            resp_sensitivity_path.read_text(encoding="utf-8")
        )
        runtime["resp_sensitivity_matrix"] = {
            "status": sensitivity.get("status"),
            "counts": sensitivity.get("counts"),
            "maximum_core_across_seed_sample_std_e": sensitivity.get(
                "maximum_core_across_seed_sample_std_e"
            ),
            "maximum_core_across_density_sample_std_e": sensitivity.get(
                "maximum_core_across_density_sample_std_e"
            ),
            "maximum_core_overall_range_e": sensitivity.get(
                "maximum_core_overall_range_e"
            ),
            "path": str(resp_sensitivity_path),
            "sha256": sha256(resp_sensitivity_path),
        }
    if joint_resp_path is not None:
        joint = json.loads(joint_resp_path.read_text(encoding="utf-8"))
        runtime["joint_multiconformer_resp"] = {
            "status": joint.get("status"),
            "counts": joint.get("counts"),
            "maximum_core_absolute_joint_minus_independent_mean_e": joint.get(
                "maximum_core_absolute_joint_minus_independent_mean_e"
            ),
            "maximum_raw_joint_charge_sum_error_e": joint.get(
                "maximum_raw_joint_charge_sum_error_e"
            ),
            "path": str(joint_resp_path),
            "sha256": sha256(joint_resp_path),
        }
    if resp_core_transfer_path is not None:
        transfer = json.loads(
            resp_core_transfer_path.read_text(encoding="utf-8")
        )
        runtime["resp_core_transfer"] = {
            "status": transfer.get("status"),
            "counts": transfer.get("counts"),
            "minimum_mapped_heavy_atom_fraction": transfer.get(
                "minimum_mapped_heavy_atom_fraction"
            ),
            "maximum_mapped_heavy_atom_fraction": transfer.get(
                "maximum_mapped_heavy_atom_fraction"
            ),
            "maximum_absolute_transfer_minus_gasteiger_e": transfer.get(
                "maximum_absolute_transfer_minus_gasteiger_e"
            ),
            "path": str(resp_core_transfer_path),
            "sha256": sha256(resp_core_transfer_path),
        }
    if (
        runtime.get("native_resp_smoke", {}).get("status")
        == "completed_native_two_stage_resp_smoke"
        and str(runtime.get("radonpy_resp_wrapper_smoke", {}).get("status", ""))
        .startswith("radonpy_resp_wrapper_blocked")
    ):
        runtime["charge_gate_status"] = (
            "native_two_stage_resp_ready_radonpy_wrapper_density20_blocked_"
            "fragment_transfer_validation_pending"
        )
    if (
        runtime.get("resp_core_transfer", {}).get("status")
        == "twelve_chain_core_mapping_completed_full_charge_assignment_pending"
    ):
        runtime["charge_gate_status"] = (
            "joint_fragment_core_mapping_completed_"
            "full_chain_charge_assignment_pending"
        )
    if (
        runtime.get("joint_multiconformer_resp", {}).get("status")
        == "four_family_joint_multiconformer_resp_completed_transfer_pending"
        and "resp_core_transfer" not in runtime
    ):
        runtime["charge_gate_status"] = (
            "joint_multiconformer_fragment_resp_ready_"
            "polymer_transfer_validation_pending"
        )
    environment_manifest = json.loads(
        environment_manifest_path.read_text(encoding="utf-8")
    )
    output_root.mkdir(parents=True, exist_ok=True)
    detail_path = output_root / "GAFF2替代参数逐类型.csv"
    formulation_path = output_root / "GAFF2替代参数逐配方.csv"
    gate_path = output_root / "生产参数门.json"
    report_path = output_root / "生产参数门说明.md"
    _atomic_text(detail_path, detail.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        formulation_path,
        formulations.to_csv(index=False, float_format="%.12g"),
    )
    gate = {
        "release_id": release_id,
        "status": "production_md_blocked_parameter_and_charge_validation",
        "forcefield_parameter_gate": {
            "status": "blocked_repeating_urethane_ns_substitution",
            **summary,
        },
        "charge_gate": runtime,
        "environment_identity": {
            key: environment_manifest.get(key)
            for key in [
                "python_version",
                "radonpy_commit",
                "radonpy_wheel_sha256",
                "lammps_runtime_version",
            ]
        },
        "production_md_permission": "blocked",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        gate_path,
        json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    family = summary["validation_family_counts"]
    if runtime["charge_gate_status"].startswith(
        "joint_fragment_core_mapping_completed"
    ):
        charge_note = (
            "四类三构象联合RESP核心已逐一映射到12条现实TPU低聚链，"
            "氨基甲酸酯与残余NCO计数全部闭合；但核心只覆盖约12%–19%"
            "重原子。当前放行到核心定位，不放行未覆盖原子电荷补全、整链"
            "总电荷/局部偶极验证或生产MD。"
        )
    elif runtime["charge_gate_status"].startswith(
        "joint_multiconformer_fragment_resp_ready"
    ):
        charge_note = (
            "四类局部化学家族已完成4×3×3构象/点密度敏感性矩阵，并在"
            "标准点密度1.0下以三个构象等权共同拟合原子电荷。当前放行到"
            "片段多构象联合RESP，不放行片段到完整TPU链的电荷转移或生产MD。"
        )
    elif runtime["charge_gate_status"].startswith("native_two_stage_resp_ready"):
        charge_note = (
            "独立Psi4/RESP环境已在一个含氨基甲酸酯键的13原子小片段上完成"
            "HF/6-31G(d)原生两阶段RESP；但RadonPy包装器硬编码点密度20的"
            "三次尝试均发生段错误。当前只放行原生片段级电荷验证，不放行"
            "整链转移或生产MD。"
        )
    else:
        charge_note = (
            "RadonPy源码的RESP路线默认先以`wb97m-d3bj/6-31G(d,p)`优化，"
            "再以`HF/6-31G(d)`拟合RESP。当前固定环境的Psi4/RESP或兼容"
            "包装未完成实算验证，不能把Gasteiger电荷升级命名为生产电荷。"
        )
    _atomic_text(
        report_path,
        "\n".join(
            [
                "# 现实TPU生产MD参数门说明",
                "",
                f"- 审计配方：{summary['formulation_count']}条；全部仍被生产MD参数门阻断。",
                f"- 唯一替代参数映射：{summary['unique_substitution_count']}类。",
                f"- 重复氨基甲酸酯`ns`映射：{family.get('repeating_urethane_ns_substitution', 0)}类，列为P0。",
                f"- 末端NCO共轭类型映射：{family.get('terminal_isocyanate_conjugated_type_substitution', 0)}类，列为P1。",
                "- 替代参数事件数与估计氨基甲酸酯键数的Pearson相关系数："
                f"{summary['urethane_bond_vs_substitution_event_pearson_r']:.6f}。",
                "",
                "强相关表明问题随主链氨基甲酸酯数增长，不是少量无关警告。优先验证重复主链的键、角、二面角和improper；末端NCO参数应在明确封端或残余NCO比例后单独验证。",
                "",
                charge_note,
                "",
                "下一步验证单元应至少覆盖脂肪族氨基甲酸酯、芳香族氨基甲酸酯和残余异氰酸酯端基；对P0二面角需做量化扫描并比较GAFF2替代势能曲线，不能只检查LAMMPS是否运行。",
                "",
            ]
        ),
    )
    files = [detail_path, formulation_path, gate_path, report_path]
    manifest = {
        "release_id": release_id,
        "status": gate["status"],
        "inputs": {
            path.name: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in [*required_paths, *optional_paths]
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "counts": summary,
        "production_md_permission": "blocked",
    }
    _atomic_text(
        output_root / "参数门发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--GAFF2审计",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "GAFF2审计" / "GAFF2参数覆盖审计.csv",
    )
    parser.add_argument(
        "--计量计划",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "低聚链计量计划.csv",
    )
    parser.add_argument(
        "--环境清单",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "环境" / "RadonPy环境清单.json",
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "参数验证",
    )
    parser.add_argument("--RESP烟雾清单", type=Path)
    parser.add_argument("--RadonPyRESP失败审计", type=Path)
    parser.add_argument("--RESP敏感性清单", type=Path)
    parser.add_argument("--RESP联合清单", type=Path)
    parser.add_argument("--RESP核心转移清单", type=Path)
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-production-parameter-gate-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.GAFF2审计,
        args.计量计划,
        args.环境清单,
        args.输出目录,
        release_id=args.发布ID,
        resp_smoke_path=args.RESP烟雾清单,
        radonpy_resp_failure_path=args.RadonPyRESP失败审计,
        resp_sensitivity_path=args.RESP敏感性清单,
        joint_resp_path=args.RESP联合清单,
        resp_core_transfer_path=args.RESP核心转移清单,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
