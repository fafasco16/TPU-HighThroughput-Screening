"""生成现实配方的非计算特征和训练前任务门，不运行模型或量化计算。"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DIRECTED = ROOT / "结果" / "定向筛选"
INPUTS = {
    "现实配方": DIRECTED / "现实配方候选.csv",
    "现实构件": DIRECTED / "现实构件约束.csv",
    "筛选任务": DIRECTED / "筛选任务清单.csv",
    "TGA端点": DIRECTED / "TGA热稳定端点.csv",
    "实验标签": DIRECTED / "三目标实验标签.csv.gz",
    "计算证据": DIRECTED / "三目标计算证据.csv.gz",
}
FEATURES = DIRECTED / "三目标配方特征.csv.gz"
TASKS = DIRECTED / "训练前任务清单.csv"
MANIFEST = DIRECTED / "训练前发布清单.json"
RELEASE_ID = "tpu-directed-pretraining-inputs-2026-08-30-v1"


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


def _component_complete(
    formulations: pd.DataFrame,
    components: pd.DataFrame,
    value_column: str,
) -> pd.Series:
    indexed = components.set_index("stable_component_id")[value_column]
    values = []
    for role in ("diisocyanate_id", "macrodiol_id", "chain_extender_id"):
        values.append(formulations[role].map(indexed))
    return pd.concat(values, axis=1).notna().all(axis=1)


def build_formulation_features(
    formulations: pd.DataFrame,
    components: pd.DataFrame,
) -> pd.DataFrame:
    """保留已有配方字段并增加计算前门；不生成量化描述符。"""

    features = formulations.copy()
    known = set(components["stable_component_id"])
    identity_columns = ["diisocyanate_id", "macrodiol_id", "chain_extender_id"]
    features["component_identity_complete"] = features[identity_columns].apply(
        lambda row: row.notna().all() and all(value in known for value in row),
        axis=1,
    )
    macro_structure = features["macrodiol_smiles"].fillna("").astype(str).str.strip()
    macro_repeat = features["macrodiol_repeat_unit"].fillna("").astype(str).str.strip()
    features["component_structure_context_complete"] = (
        features["diisocyanate_smiles"].fillna("").astype(str).str.strip().ne("")
        & features["chain_extender_smiles"].fillna("").astype(str).str.strip().ne("")
        & (macro_structure.ne("") | macro_repeat.ne(""))
    )
    features["stoichiometry_context_complete"] = features[
        [
            "macrodiol_nominal_mn_g_mol",
            "hard_segment_mass_fraction_target",
            "nco_oh_ratio_target",
        ]
    ].notna().all(axis=1)
    features["commercial_evidence_complete"] = features[
        "procurement_review_status"
    ].eq("catalog_evidence_found_quote_required")
    features["cost_inputs_complete"] = _component_complete(
        features, components, "price_per_kg"
    )
    renewable_complete = _component_complete(
        features, components, "renewable_carbon_fraction"
    )
    hazard_complete = _component_complete(features, components, "ghs_hazard_score")
    features["environment_inputs_complete"] = renewable_complete & hazard_complete
    features["precalculation_rule_ready"] = (
        features["component_identity_complete"]
        & features["component_structure_context_complete"]
        & features["stoichiometry_context_complete"]
        & features["commercial_evidence_complete"]
    )
    features["existing_quantum_descriptor_status"] = (
        "not_materialized_in_clean_pretraining_release"
    )
    features["model_prediction_available"] = False
    features["model_prediction_status"] = "not_trained"
    features["calculation_allowed"] = False
    features["calculation_queue_status"] = (
        "deferred_by_user_until_model_and_rule_prefilter"
    )
    features["pareto_status"] = "not_scored"
    features["feature_release_status"] = features["precalculation_rule_ready"].map(
        {
            True: "ready_for_noncomputational_training_input_design",
            False: "blocked_missing_identity_or_stoichiometry",
        }
    )
    return features.sort_values("formulation_id").reset_index(drop=True)


def build_training_tasks(
    directed_tasks: pd.DataFrame,
    endpoints: pd.DataFrame,
) -> pd.DataFrame:
    tasks = directed_tasks.copy()
    tasks["tga_endpoint_curve_count"] = 0
    tasks["tga_identity_resolved_curve_count"] = 0
    thermal = tasks["objective_id"].eq("thermal_stability")
    tasks.loc[thermal, "tga_endpoint_curve_count"] = len(endpoints)
    tasks.loc[thermal, "tga_identity_resolved_curve_count"] = int(
        endpoints["formulation_id"].notna().sum()
    )
    tasks["model_training_status"] = "not_started_by_user_instruction"
    tasks["new_calculation_status"] = "not_started_by_user_instruction"
    tasks["training_ready"] = False
    next_steps = {
        "toughness": "补齐主韧性标签的组分—配方—协议映射",
        "cyclic_recovery": "补齐固定应变和循环数的直接标签；计算代理只作特征",
        "thermal_stability": "核验4条已识别TGA端点的组分映射和测试气氛/升温速率",
        "cost": "录入24种构件同地区同日期报价",
        "environment": "结构化24种构件的SDS/GHS和可再生碳证据",
    }
    tasks["next_step_before_calculation"] = tasks["objective_id"].map(next_steps)
    return tasks


def build_release() -> tuple[pd.DataFrame, pd.DataFrame]:
    formulations = pd.read_csv(INPUTS["现实配方"], low_memory=False)
    components = pd.read_csv(INPUTS["现实构件"], low_memory=False)
    directed_tasks = pd.read_csv(INPUTS["筛选任务"], low_memory=False)
    endpoints = pd.read_csv(INPUTS["TGA端点"], low_memory=False)
    return (
        build_formulation_features(formulations, components),
        build_training_tasks(directed_tasks, endpoints),
    )


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    if path.suffix == ".gz":
        frame.to_csv(
            path,
            index=False,
            encoding="utf-8",
            lineterminator="\n",
            compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
        )
    else:
        frame.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")


def _counts(features: pd.DataFrame, tasks: pd.DataFrame) -> dict[str, int]:
    return {
        "formulation_feature_rows": len(features),
        "objective_rows": len(tasks),
        "precalculation_rule_ready_rows": int(
            features["precalculation_rule_ready"].sum()
        ),
        "calculation_allowed_rows": int(features["calculation_allowed"].sum()),
        "model_prediction_available_rows": int(
            features["model_prediction_available"].sum()
        ),
    }


def _manifest(
    features: pd.DataFrame,
    tasks: pd.DataFrame,
    feature_path: Path,
    task_path: Path,
) -> dict[str, object]:
    return {
        "release_id": RELEASE_ID,
        "counts": _counts(features, tasks),
        "input_files": {name: _entry(path) for name, path in INPUTS.items()},
        "output_files": {
            "配方特征": _entry(feature_path),
            "训练前任务": _entry(task_path),
        },
        "stop_gate": {
            "new_quantum_or_md_calculation": False,
            "model_training": False,
            "prediction": False,
            "reason": "user_requested_stop_before_calculation",
        },
    }


def write_release(features: pd.DataFrame, tasks: pd.DataFrame) -> None:
    _write_csv(features, FEATURES)
    _write_csv(tasks, TASKS)
    MANIFEST.write_text(
        json.dumps(
            _manifest(features, tasks, FEATURES, TASKS),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def check_release(features: pd.DataFrame, tasks: pd.DataFrame) -> None:
    if not all(path.is_file() for path in (FEATURES, TASKS, MANIFEST)):
        raise SystemExit("缺少训练前发布；请先运行生成模式")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="tpu-pretraining-check-") as directory:
        temporary = Path(directory)
        feature_path = temporary / FEATURES.name
        task_path = temporary / TASKS.name
        _write_csv(features, feature_path)
        _write_csv(tasks, task_path)
        if _sha256(feature_path) != _sha256(FEATURES):
            raise SystemExit("三目标配方特征与当前输入不一致")
        if _sha256(task_path) != _sha256(TASKS):
            raise SystemExit("训练前任务清单与当前输入不一致")
    if manifest != _manifest(features, tasks, FEATURES, TASKS):
        raise SystemExit("训练前发布清单不一致")
    print("三目标训练前数据检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    features, tasks = build_release()
    if args.检查:
        check_release(features, tasks)
    else:
        write_release(features, tasks)
        print(json.dumps(_counts(features, tasks), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
