"""四类 TPU 来源到分层数据库快照的确定性构建编排。

本模块只消费来源清单中已经登记的四个文件。适配器输出先完整落入暂存层，
随后仅做有证据支撑的字段对齐；缺失化学、配方和工艺事实不会被推断或填补。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from adapter_hbond import adapt_hbond
from adapter_pue326 import EXPECTED_COLUMNS as PUE_COLUMNS
from adapter_pue326 import adapt_pue326
from adapter_smipoly import adapt_smipoly
from adapter_viscosity import adapt_viscosity
from curves import ALGORITHM_VERSION as CURVE_ALGORITHM_VERSION
from curves import tensile_metrics
from ids import stable_id
from licensing import may_publish
from qc import (
    QualityIssue,
    check_finite_values,
    check_foreign_key,
    check_lineage_split,
    check_primary_key,
    check_provenance,
    check_public_release,
    issues_frame,
    check_unresolved_units,
)
from schema import load_enums
from snapshot import (
    build_duckdb,
    sha256_file,
    write_parquet_deterministic,
    write_snapshot_manifest,
)


SCHEMA_VERSION = "v0.1"
PIPELINE_VERSION = "tpu-db/0.1.1"
SOURCE_SMIPOLY = "ds_smipoly_monomers"
SOURCE_PUE = "ds_pue326_dq"
SOURCE_HBOND = "ds_eom_hbond_2021"
SOURCE_VISCOSITY = "ds_prepolymer_viscosity"
REQUIRED_SOURCES = (
    SOURCE_SMIPOLY,
    SOURCE_PUE,
    SOURCE_HBOND,
    SOURCE_VISCOSITY,
)

_MANIFEST_REQUIRED = (
    "source_id",
    "source_file_id",
    "raw_path",
    "sha256",
    "license_spdx",
    "derivatives_allowed",
    "redistribution_allowed",
    "access_restriction",
    "material_scope",
    "status",
)
_PUBLIC_SOURCE_STATUSES = frozenset({"available"})
_PUBLIC_ACCESS_RESTRICTIONS = frozenset({"open"})
_SHA256_PATTERN = re.compile(r"^[0-9A-Fa-f]{64}$")
_POLICY_FIELDS = (
    "license_spdx",
    "derivatives_allowed",
    "redistribution_allowed",
    "access_restriction",
    "material_scope",
    "status",
)
_ALLOWED_ADAPTER_OPTIONS = {
    SOURCE_SMIPOLY: frozenset(),
    SOURCE_PUE: frozenset(),
    SOURCE_HBOND: frozenset({"expected_sheets"}),
    SOURCE_VISCOSITY: frozenset({"expected_sheets"}),
}
_SNAPSHOT_SIGNATURE_FIELDS = (
    "source_id",
    "source_file_id",
    "raw_path",
    "sha256",
    "doi",
    "url",
    "accessed_at",
    "license_spdx",
    "derivatives_allowed",
    "redistribution_allowed",
    "access_restriction",
    "evidence_grade",
    "material_scope",
    "status",
    "notes",
)
_PIPELINE_CODE_FILES = (
    "adapter_hbond.py",
    "adapter_pue326.py",
    "adapter_smipoly.py",
    "adapter_viscosity.py",
    "curves.py",
    "database_build.py",
    "ids.py",
    "licensing.py",
    "qc.py",
    "schema.py",
    "snapshot.py",
    "units.py",
)
_PIPELINE_PROJECT_FILES = (
    "pyproject.toml",
    "uv.lock",
    "配置/结构定义/v0.1字段字典.yaml",
    "配置/结构定义/v0.1枚举.yaml",
    "配置/数据源.yaml",
)


@dataclass(frozen=True)
class DatabaseBuildResult:
    """机器可读的构建结果，不把大型 DataFrame 留在返回对象中。"""

    snapshot_id: str
    row_counts: Mapping[str, int]
    outputs: Mapping[str, Mapping[str, Any]]
    issues: tuple[QualityIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "row_counts": dict(self.row_counts),
            "outputs": {name: dict(value) for name, value in self.outputs.items()},
            "issues": [asdict(issue) for issue in self.issues],
            "has_errors": self.has_errors,
        }


def _manifest_frame(
    manifest_rows: Iterable[Mapping[str, Any]] | pd.DataFrame,
) -> pd.DataFrame:
    if isinstance(manifest_rows, pd.DataFrame):
        frame = manifest_rows.copy()
    else:
        frame = pd.DataFrame(list(manifest_rows))
    missing_columns = [
        column for column in _MANIFEST_REQUIRED if column not in frame.columns
    ]
    if missing_columns:
        raise ValueError(f"manifest 缺少必需字段: {missing_columns}")
    if frame.empty:
        raise ValueError("manifest 不得为空")
    source_file_ids = frame["source_file_id"].astype("string")
    if (
        source_file_ids.isna().any()
        or source_file_ids.str.strip().eq("").any()
        or source_file_ids.duplicated().any()
    ):
        raise ValueError("manifest source_file_id 必须非空且唯一")
    for column in ("source_id", "raw_path"):
        values = frame[column].astype("string")
        if values.isna().any() or values.str.strip().eq("").any():
            raise ValueError(f"manifest {column} 必须是非空字符串")
    invalid_hash = ~frame["sha256"].astype("string").str.fullmatch(
        _SHA256_PATTERN.pattern, na=False
    )
    if invalid_hash.any():
        raise ValueError("manifest sha256 必须是64位十六进制字符串")
    return frame.sort_values(
        ["source_id", "raw_path", "source_file_id"], kind="mergesort"
    ).reset_index(drop=True)


def _selected_manifest(frame: pd.DataFrame) -> pd.DataFrame:
    selected = frame[frame["source_id"].isin(REQUIRED_SOURCES)].copy()
    missing_sources = sorted(set(REQUIRED_SOURCES) - set(selected["source_id"]))
    if missing_sources:
        raise ValueError(f"四源构建缺少来源: {missing_sources}")
    return selected.reset_index(drop=True)


def _resolve_raw_file(root: Path, row: Mapping[str, Any]) -> Path:
    raw_path = Path(str(row["raw_path"]))
    if raw_path.is_absolute():
        raise ValueError("manifest raw_path 必须是项目相对路径")
    candidate = (root / raw_path).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"原始文件越出项目目录: {raw_path}") from exc
    if not candidate.is_file():
        raise ValueError(f"原始来源不是文件: {raw_path}")
    expected_hash = str(row["sha256"]).strip().casefold()
    actual_hash = sha256_file(candidate).casefold()
    if expected_hash != actual_hash:
        raise ValueError(f"原始文件哈希不匹配: {raw_path}")
    return candidate


def _concat_frames(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def _signature_value(value: Any) -> Any:
    """Convert manifest scalars into deterministic JSON-compatible values."""

    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _canonical_build_value(value: Any, *, path: str) -> Any:
    """Convert build parameters to deterministic JSON or reject ambiguity."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} 不得包含非有限数值")
        return value
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item)):
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{path} 的键必须是非空字符串")
            output[key] = _canonical_build_value(
                value[key], path=f"{path}.{key}"
            )
        return output
    if isinstance(value, (list, tuple)):
        return [
            _canonical_build_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{path} 包含不支持的构建参数类型: {type(value).__name__}")


def _normalize_adapter_options(
    adapter_options: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if adapter_options is None:
        return {}
    if not isinstance(adapter_options, Mapping):
        raise ValueError("adapter_options 必须是来源到参数的映射")
    unknown_sources = sorted(set(adapter_options) - set(_ALLOWED_ADAPTER_OPTIONS))
    if unknown_sources:
        raise ValueError(f"adapter_options 包含未知来源: {unknown_sources}")
    normalized: dict[str, dict[str, Any]] = {}
    for source_id in sorted(adapter_options):
        values = adapter_options[source_id]
        if not isinstance(values, Mapping):
            raise ValueError(f"adapter_options.{source_id} 必须是映射")
        unknown_options = sorted(
            set(values) - set(_ALLOWED_ADAPTER_OPTIONS[source_id])
        )
        if unknown_options:
            raise ValueError(
                f"adapter_options.{source_id} 包含未知参数: {unknown_options}"
            )
        normalized[source_id] = _canonical_build_value(
            values, path=f"adapter_options.{source_id}"
        )
    return normalized


def _validate_manifest_enums(root: Path, selected: pd.DataFrame) -> None:
    enums = load_enums(root / "配置/结构定义" / "v0.1枚举.yaml")["enums"]
    for column, enum_name in (
        ("status", "source_status"),
        ("access_restriction", "access_restriction"),
        ("material_scope", "material_scope"),
        ("evidence_grade", "evidence_grade"),
    ):
        allowed = set(enums[enum_name])
        invalid = sorted(
            {
                str(value)
                for value in selected[column]
                if pd.isna(value) or str(value) not in allowed
            }
        )
        if invalid:
            raise ValueError(
                f"manifest {column} 包含未声明的 {enum_name} 值: {invalid}"
            )


def _normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


def pipeline_signature(project_root: str | Path) -> dict[str, Any]:
    """Hash transformation code, schemas, registry and environment lock."""

    root = Path(project_root).resolve(strict=True)
    code_dir = Path(__file__).resolve().parent
    files: list[dict[str, str]] = []
    for filename in _PIPELINE_CODE_FILES:
        path = (code_dir / filename).resolve(strict=True)
        files.append(
            {
                "path": f"代码/{filename}",
                "sha256_text_lf": _normalized_text_sha256(path),
            }
        )
    for relative in _PIPELINE_PROJECT_FILES:
        path = root / relative
        if path.is_file():
            files.append(
                {
                    "path": relative,
                    "sha256_text_lf": _normalized_text_sha256(path),
                }
            )
    files.sort(key=lambda row: row["path"].casefold())
    pipeline_id = stable_id(
        "pipeline",
        PIPELINE_VERSION,
        CURVE_ALGORITHM_VERSION,
        files,
    )
    return {
        "pipeline_id": pipeline_id,
        "pipeline_version": PIPELINE_VERSION,
        "curve_algorithm_version": CURVE_ALGORITHM_VERSION,
        "files": files,
    }


def extract_staging_tables(
    project_root: str | Path,
    manifest_rows: Iterable[Mapping[str, Any]] | pd.DataFrame,
    *,
    adapter_options: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """按 ``source_id/source_file_id`` 调用适配器并保留完整适配器输出。"""

    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project_root 必须是目录")
    selected = _selected_manifest(_manifest_frame(manifest_rows))
    _validate_manifest_enums(root, selected)
    options = _normalize_adapter_options(adapter_options)
    collected: dict[str, list[pd.DataFrame]] = {}

    def append(table_name: str, frame: pd.DataFrame) -> None:
        collected.setdefault(table_name, []).append(frame)

    for row in selected.to_dict(orient="records"):
        path = _resolve_raw_file(root, row)
        source_id = str(row["source_id"])
        source_file_id = str(row["source_file_id"])
        kwargs = dict(options.get(source_id, {}))
        if source_id == SOURCE_SMIPOLY:
            append(
                "smipoly_chemical",
                adapt_smipoly(
                    path,
                    source_id=source_id,
                    source_file_id=source_file_id,
                ),
            )
        elif source_id == SOURCE_PUE:
            append(
                "pue_transformed",
                adapt_pue326(
                    path,
                    source_id=source_id,
                    source_file_id=source_file_id,
                ),
            )
        elif source_id == SOURCE_HBOND:
            result = adapt_hbond(
                path,
                source_id=source_id,
                source_file_id=source_file_id,
                **kwargs,
            )
            for name, frame in result.items():
                append(f"hbond_{name}", frame)
        elif source_id == SOURCE_VISCOSITY:
            result = adapt_viscosity(
                path,
                source_id=source_id,
                source_file_id=source_file_id,
                **kwargs,
            )
            for name, frame in result.items():
                append(f"viscosity_{name}", frame)

    staging = {
        name: _concat_frames(frames)
        for name, frames in sorted(collected.items())
    }
    return staging, selected


def _policy_columns(selected: pd.DataFrame) -> pd.DataFrame:
    policy = selected[
        [
            "source_id",
            "source_file_id",
            "license_spdx",
            "derivatives_allowed",
            "redistribution_allowed",
            "access_restriction",
            "material_scope",
            "status",
        ]
    ].copy()
    for source_id, group in policy.groupby("source_id", sort=True, dropna=False):
        if len(group[list(_POLICY_FIELDS)].drop_duplicates()) != 1:
            raise ValueError(
                f"同一 source_id 的许可策略必须一致；请拆分来源: {source_id}"
            )
    policy["may_publish"] = [
        may_publish(license_id, derivatives, redistribution)
        and str(access).strip().casefold() in _PUBLIC_ACCESS_RESTRICTIONS
        and str(status).strip().casefold() in _PUBLIC_SOURCE_STATUSES
        for license_id, derivatives, redistribution, access, status in zip(
            policy["license_spdx"],
            policy["derivatives_allowed"],
            policy["redistribution_allowed"],
            policy["access_restriction"],
            policy["status"],
            strict=True,
        )
    ]
    policy = policy.rename(columns={"status": "source_status"})
    return policy


def _attach_policy(frame: pd.DataFrame, policy: pd.DataFrame) -> pd.DataFrame:
    policy_columns = [
        "source_id",
        "source_file_id",
        "license_spdx",
        "derivatives_allowed",
        "redistribution_allowed",
        "access_restriction",
        "material_scope",
        "source_status",
        "may_publish",
    ]
    if frame.empty:
        output = frame.copy()
        for column in policy_columns[2:]:
            if column not in output.columns:
                output[column] = pd.Series(dtype="object")
        return output
    output = frame.merge(
        policy[policy_columns],
        on=["source_id", "source_file_id"],
        how="left",
        validate="many_to_one",
    )
    if output["may_publish"].isna().any():
        raise ValueError("规范化记录无法关联来源许可策略")
    output["may_publish"] = output["may_publish"].astype(bool)
    return output


def _source_tables(selected: pd.DataFrame, policy: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    source_file = selected.copy()
    source_file = source_file.merge(
        policy[
            [
                "source_id",
                "source_file_id",
                "source_status",
                "may_publish",
            ]
        ],
        on=["source_id", "source_file_id"],
        how="left",
        validate="one_to_one",
    )
    source_file["schema_version"] = SCHEMA_VERSION
    source_file["status"] = source_file["source_status"]
    source_columns = [
        column
        for column in (
            "source_id",
            "doi",
            "url",
            "accessed_at",
            "license_spdx",
            "derivatives_allowed",
            "redistribution_allowed",
            "evidence_grade",
            "material_scope",
            "status",
            "source_status",
            "access_restriction",
            "may_publish",
            "notes",
        )
        if column in source_file.columns
    ]
    source = source_file[source_columns].drop_duplicates("source_id", keep="first")
    return source.reset_index(drop=True), source_file.reset_index(drop=True)


def _normalize_chemical_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse structures while conservatively inheriting every contributor policy."""

    rows: list[dict[str, Any]] = []
    for chemical_id, group in frame.sort_values(
        ["chemical_id", "source_id", "source_file_id", "source_record_id"],
        kind="mergesort",
    ).groupby("chemical_id", sort=True, dropna=False):
        row = group.iloc[0].to_dict()
        names = sorted(
            {
                str(name)
                for name in group["iupac_name_raw"].dropna()
                if str(name).strip()
            }
        )
        row["record_id"] = chemical_id
        row["smiles_raw"] = row["raw_smiles"]
        row["preferred_name"] = names[0] if len(names) == 1 else pd.NA
        row["iupac_names_raw_json"] = json.dumps(
            names, ensure_ascii=False, separators=(",", ":")
        )
        row["source_record_ids_json"] = json.dumps(
            sorted(group["source_record_id"].astype(str)),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        row["source_ids_json"] = json.dumps(
            sorted(set(group["source_id"].astype(str))),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        row["source_file_ids_json"] = json.dumps(
            sorted(set(group["source_file_id"].astype(str))),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        row["source_locators_json"] = json.dumps(
            sorted(set(group["source_locator"].astype(str))),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        row["source_record_count"] = len(group)
        aggregate_policy_fields = (
            "license_spdx",
            "derivatives_allowed",
            "redistribution_allowed",
            "access_restriction",
            "material_scope",
            "source_status",
        )
        policy_values = {
            json.dumps(
                [_signature_value(value) for value in values],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for values in group[list(aggregate_policy_fields)].itertuples(
                index=False, name=None
            )
        }
        uniform_policy = len(policy_values) == 1
        all_publishable = bool(group["may_publish"].eq(True).all())  # noqa: E712
        if uniform_policy:
            row["license_resolution_status"] = (
                "uniform_publishable" if all_publishable else "uniform_blocked"
            )
            row["may_publish"] = all_publishable
        else:
            row.update(
                {
                    "license_spdx": "NOASSERTION",
                    "derivatives_allowed": False,
                    "redistribution_allowed": False,
                    "access_restriction": "restricted",
                    "material_scope": "unknown",
                    "source_status": "review_required",
                    "may_publish": False,
                    "license_resolution_status": "mixed_or_blocked",
                }
            )
        row["table_role"] = "virtual_candidate_structure"
        row["extraction_method_raw"] = row["extraction_method"]
        row["extraction_method"] = "direct_table"
        row["fidelity_raw"] = row["fidelity"]
        row["fidelity"] = "candidate_structure"
        row["schema_version"] = SCHEMA_VERSION
        rows.append(row)
    return pd.DataFrame(rows)


def normalize_staging_tables(
    staging: Mapping[str, pd.DataFrame], selected: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """生成保守规范层；只对适配器已声明单位的数值建立规范字段。"""

    policy = _policy_columns(selected)
    source, source_file = _source_tables(selected, policy)

    chemical = _attach_policy(staging["smipoly_chemical"], policy)
    chemical = _normalize_chemical_candidates(chemical)

    pue = staging["pue_transformed"].copy()
    pue["table_role"] = "transformed_feature_auxiliary"
    pue["inverse_transformation_status"] = (
        "not_attempted_missing_scaling_metadata"
    )
    pue["extraction_method_raw"] = pue["extraction_method"]
    pue["extraction_method"] = "direct_table"
    pue["schema_version"] = SCHEMA_VERSION
    pue = _attach_policy(pue, policy)

    hbond_curves = staging["hbond_curves"].copy()
    hbond_curves["curve_type_raw"] = hbond_curves["curve_type"]
    hbond_curves["curve_type"] = hbond_curves["curve_type_raw"].map(
        {
            "tensile_stress_strain": "tensile_monotonic",
            "temperature_dependent_tensile_stress_strain": "tensile_monotonic",
            "rheology_frequency_sweep_storage_modulus": "rheology",
            "rheology_frequency_sweep_loss_modulus": "rheology",
            "ftir_absorbance_spectrum": "other",
        }
    ).fillna("other")
    hbond_curves["unit_status_raw"] = hbond_curves["unit_status"]
    hbond_curves["unit_status"] = hbond_curves["unit_status_raw"].map(
        {"resolved": "converted", "unresolved": "unresolved"}
    )
    hbond_curves["extraction_method_raw"] = hbond_curves["extraction_method"]
    hbond_curves["extraction_method"] = "direct_curve"
    hbond_curves["schema_version"] = SCHEMA_VERSION
    hbond_curves["x_name"] = hbond_curves["x_quantity"]
    hbond_curves["y_name"] = hbond_curves["y_quantity"]
    hbond_curves["x_unit_canonical"] = hbond_curves["x_unit"]
    hbond_curves["y_unit_canonical"] = hbond_curves["y_unit"]
    hbond_curves["source_domain"] = "hbond_tpu_experiment"
    hbond_curves["test_link_status"] = "source_sheet_derived_no_specimen_chain"

    viscosity_curves = staging["viscosity_curves"].copy()
    viscosity_curves["test_id"] = pd.NA
    viscosity_curves["curve_type_raw"] = viscosity_curves["curve_type"]
    viscosity_curves["curve_type"] = "viscosity_temperature"
    viscosity_curves["unit_status_raw"] = "resolved"
    viscosity_curves["extraction_method_raw"] = viscosity_curves[
        "extraction_method"
    ]
    viscosity_curves["extraction_method"] = "direct_curve"
    viscosity_curves["schema_version"] = SCHEMA_VERSION
    viscosity_curves["x_name"] = "temperature"
    viscosity_curves["y_name"] = "viscosity"
    viscosity_curves["x_unit_canonical"] = viscosity_curves["temperature_unit"]
    viscosity_curves["y_unit_canonical"] = viscosity_curves["viscosity_unit"]
    viscosity_curves["unit_status"] = "converted"
    viscosity_curves["source_domain"] = "prepolymer_process_property"
    viscosity_curves["test_link_status"] = (
        "source_sheet_auxiliary_no_specimen_test_chain"
    )
    curve = _attach_policy(
        _concat_frames([hbond_curves, viscosity_curves]), policy
    )

    hbond_points = staging["hbond_curve_points"].copy()
    hbond_points["unit_status_raw"] = hbond_points["unit_status"]
    hbond_points["unit_status"] = hbond_points["unit_status_raw"].map(
        {"resolved": "converted", "unresolved": "unresolved"}
    )
    hbond_points["extraction_method_raw"] = hbond_points["extraction_method"]
    hbond_points["extraction_method"] = "direct_curve"
    hbond_points["schema_version"] = SCHEMA_VERSION
    hbond_points["x_canonical"] = hbond_points["x_value"]
    hbond_points["x_unit_canonical"] = hbond_points["x_unit"]
    hbond_points["y_canonical"] = hbond_points["y_value"]
    hbond_points["y_unit_canonical"] = hbond_points["y_unit"]
    hbond_points["source_domain"] = "hbond_tpu_experiment"

    viscosity_points = staging["viscosity_curve_points"].copy()
    viscosity_points["unit_status_raw"] = "resolved"
    viscosity_points["extraction_method_raw"] = viscosity_points[
        "extraction_method"
    ]
    viscosity_points["extraction_method"] = "direct_curve"
    viscosity_points["schema_version"] = SCHEMA_VERSION
    viscosity_points["x_raw"] = viscosity_points["temperature_raw"]
    viscosity_points["x_unit_raw"] = viscosity_points["temperature_unit_raw"]
    viscosity_points["x_canonical"] = viscosity_points["temperature_k"]
    viscosity_points["x_unit_canonical"] = viscosity_points["temperature_unit"]
    viscosity_points["y_raw"] = viscosity_points["viscosity_raw"]
    viscosity_points["y_unit_raw"] = viscosity_points["viscosity_unit_raw"]
    viscosity_points["y_canonical"] = viscosity_points["viscosity_pa_s"]
    viscosity_points["y_unit_canonical"] = viscosity_points["viscosity_unit"]
    viscosity_points["unit_status"] = "converted"
    viscosity_points["source_domain"] = "prepolymer_process_property"
    curve_point = _attach_policy(
        _concat_frames([hbond_points, viscosity_points]), policy
    )

    properties = staging["hbond_properties"].copy()
    properties["test_id"] = [
        stable_id(
            "test",
            source_file_id,
            figure,
            material,
            condition,
            "reported_property",
        )
        for source_file_id, figure, material, condition in zip(
            properties["source_file_id"],
            properties["figure"],
            properties["material_name"],
            properties["condition_label"],
            strict=True,
        )
    ]
    properties["value_raw"] = properties["raw_value"]
    properties["unit_raw"] = properties["raw_unit"]
    properties["value_canonical"] = properties["normalized_value"]
    properties["unit_canonical"] = properties["normalized_unit"]
    properties["unit_status_raw"] = properties["unit_status"]
    properties["unit_status"] = properties["unit_status_raw"].map(
        {"resolved": "converted", "unresolved": "unresolved"}
    )
    properties["extraction_method_raw"] = properties["extraction_method"]
    properties["extraction_method"] = "direct_table"
    properties["schema_version"] = SCHEMA_VERSION
    properties["test_identity_status"] = "derived_from_source_grouping"
    property_value = _attach_policy(properties, policy)

    return {
        "source": source,
        "source_file": source_file,
        "chemical_candidate": chemical,
        "pue_transformed_auxiliary": pue,
        "curve": curve,
        "curve_point": curve_point,
        "property_value": property_value,
    }


def derive_curve_metrics(
    curve: pd.DataFrame, curve_point: pd.DataFrame
) -> tuple[pd.DataFrame, list[QualityIssue]]:
    """只为单位已解析的拉伸曲线计算可复算指标。"""

    columns = [
        "derived_id",
        "curve_id",
        "source_id",
        "source_file_id",
        "property_name",
        "algorithm_version",
        "parameters",
        "value",
        "unit",
        "point_count",
        "warnings",
        "schema_version",
        "license_spdx",
        "derivatives_allowed",
        "redistribution_allowed",
        "access_restriction",
        "material_scope",
        "source_status",
        "may_publish",
    ]
    records: list[dict[str, Any]] = []
    issues: list[QualityIssue] = []
    eligible = curve[
        curve["curve_type"].eq("tensile_monotonic")
        & curve["x_unit_canonical"].eq("1")
        & curve["y_unit_canonical"].eq("MPa")
        & curve["unit_status"].eq("converted")
    ]
    for curve_row in eligible.sort_values("curve_id", kind="mergesort").to_dict(
        orient="records"
    ):
        curve_id = str(curve_row["curve_id"])
        points = curve_point[curve_point["curve_id"] == curve_id].sort_values(
            "point_index", kind="mergesort"
        )
        if len(points) < 2:
            issues.append(
                QualityIssue(
                    "curve.insufficient_points",
                    "error",
                    "curve",
                    curve_id,
                    "拉伸曲线少于2个点，不能计算派生指标",
                )
            )
            continue
        try:
            metrics = tensile_metrics(
                points["x_canonical"].to_numpy(),
                points["y_canonical"].to_numpy(),
            )
        except ValueError as exc:
            issues.append(
                QualityIssue(
                    "derived.tensile_failed",
                    "error",
                    "derived_property",
                    curve_id,
                    str(exc),
                )
            )
            continue
        for property_name in (
            "toughness_mj_m3",
            "tensile_strength_mpa",
            "elongation_at_break",
        ):
            records.append(
                {
                    "derived_id": stable_id(
                        "derived_property",
                        curve_id,
                        property_name,
                        metrics["algorithm_version"],
                    ),
                    "curve_id": curve_id,
                    "source_id": curve_row["source_id"],
                    "source_file_id": curve_row["source_file_id"],
                    "property_name": property_name,
                    "algorithm_version": metrics["algorithm_version"],
                    "parameters": json.dumps(
                        {"integration": "signed_trapezoid_source_order"},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "value": metrics[property_name],
                    "unit": metrics["units"][property_name],
                    "point_count": metrics["integration_points"],
                    "warnings": json.dumps(
                        metrics["warnings"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "schema_version": SCHEMA_VERSION,
                    "license_spdx": curve_row["license_spdx"],
                    "derivatives_allowed": curve_row["derivatives_allowed"],
                    "redistribution_allowed": curve_row["redistribution_allowed"],
                    "access_restriction": curve_row["access_restriction"],
                    "material_scope": curve_row["material_scope"],
                    "source_status": curve_row["source_status"],
                    "may_publish": bool(curve_row["may_publish"]),
                }
            )
    return pd.DataFrame(records, columns=columns), issues


def _public_view(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "license_spdx",
        "derivatives_allowed",
        "redistribution_allowed",
        "access_restriction",
        "source_status",
        "material_scope",
        "may_publish",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"公开视图缺少许可门控字段: {missing}")
    license_gate = pd.Series(
        [
            may_publish(license_id, derivatives, redistribution)
            for license_id, derivatives, redistribution in zip(
                frame["license_spdx"],
                frame["derivatives_allowed"],
                frame["redistribution_allowed"],
                strict=True,
            )
        ],
        index=frame.index,
        dtype=bool,
    )
    allowed = (
        frame["may_publish"].eq(True)  # noqa: E712
        & license_gate
        & frame["source_status"].astype(str).str.strip().str.casefold().isin(
            _PUBLIC_SOURCE_STATUSES
        )
        & frame["access_restriction"]
        .astype(str)
        .str.strip()
        .str.casefold()
        .isin(_PUBLIC_ACCESS_RESTRICTIONS)
        & frame["material_scope"].notna()
        & frame["material_scope"].astype(str).str.strip().ne("")
    )
    return frame.loc[allowed].reset_index(drop=True)


def _curve_count_issues(
    curve: pd.DataFrame, curve_point: pd.DataFrame
) -> list[QualityIssue]:
    issues = check_foreign_key(
        curve_point, curve, "curve_point", "curve_id", "curve_id"
    )
    actual_counts = curve_point.groupby("curve_id", dropna=False).size()
    for row in curve[["curve_id", "point_count"]].to_dict(orient="records"):
        curve_id = str(row["curve_id"])
        actual = int(actual_counts.get(row["curve_id"], 0))
        try:
            declared = int(row["point_count"])
        except (TypeError, ValueError):
            declared = -1
        if declared != actual:
            issues.append(
                QualityIssue(
                    "curve.point_count_mismatch",
                    "error",
                    "curve",
                    curve_id,
                    f"声明点数 {declared} 与实际点数 {actual} 不一致",
                )
            )
    return issues


def run_quality_checks(
    staging: Mapping[str, pd.DataFrame],
    normalized: Mapping[str, pd.DataFrame],
    derived: pd.DataFrame,
    public_views: Mapping[str, pd.DataFrame],
    derivation_issues: Iterable[QualityIssue] = (),
) -> list[QualityIssue]:
    """运行主键、追溯、有限值、母数据分组、曲线和公开许可检查。"""

    issues = list(derivation_issues)
    for table_name, frame in sorted(staging.items()):
        issues.extend(check_primary_key(frame, table_name, ["record_id"]))
        issues.extend(check_provenance(frame, table_name))

    primary_keys = {
        "source": ["source_id"],
        "source_file": ["source_file_id"],
        "chemical_candidate": ["chemical_id"],
        "pue_transformed_auxiliary": ["record_id"],
        "curve": ["curve_id"],
        "curve_point": ["curve_id", "point_index"],
        "property_value": ["property_id"],
    }
    for table_name, frame in sorted(normalized.items()):
        issues.extend(check_primary_key(frame, table_name, primary_keys[table_name]))
        if table_name not in {"source", "source_file"}:
            issues.extend(check_provenance(frame, table_name))

    issues.extend(
        check_finite_values(
            staging["smipoly_chemical"],
            "smipoly_chemical",
            ["molecular_weight_raw"],
        )
    )
    issues.extend(
        check_finite_values(
            staging["pue_transformed"],
            "pue_transformed",
            list(PUE_COLUMNS[1:]),
        )
    )
    issues.extend(
        check_finite_values(
            normalized["curve_point"],
            "curve_point",
            ["x_raw", "y_raw", "x_canonical", "y_canonical"],
        )
    )
    issues.extend(
        check_finite_values(
            normalized["property_value"],
            "property_value",
            ["value_raw", "value_canonical"],
        )
    )
    issues.extend(
        check_finite_values(
            derived,
            "derived_property",
            ["value", "point_count"],
        )
    )
    issues.extend(
        check_lineage_split(
            normalized["pue_transformed_auxiliary"],
            "pue_transformed_auxiliary",
        )
    )
    issues.extend(
        _curve_count_issues(normalized["curve"], normalized["curve_point"])
    )
    issues.extend(check_unresolved_units(normalized["curve"], "curve"))
    issues.extend(
        check_unresolved_units(normalized["curve_point"], "curve_point")
    )
    chemical = normalized["chemical_candidate"]
    if "license_resolution_status" in chemical.columns:
        for row in chemical.loc[
            chemical["license_resolution_status"].eq("mixed_or_blocked")
        ].to_dict(orient="records"):
            issues.append(
                QualityIssue(
                    "license.mixed_provenance_requires_review",
                    "warning",
                    "chemical_candidate",
                    str(row.get("chemical_id", "")),
                    "同一候选结构汇集了不一致的来源许可策略，已从公开视图阻断",
                )
            )
    issues.extend(check_primary_key(derived, "derived_property", ["derived_id"]))

    eligible_ids = set(
        normalized["curve"].loc[
            normalized["curve"]["curve_type"].eq("tensile_monotonic")
            & normalized["curve"]["unit_status"].eq("converted")
            & normalized["curve"]["point_count"].ge(2),
            "curve_id",
        ]
    )
    derived_counts = derived.groupby("curve_id").size()
    for curve_id in sorted(eligible_ids):
        if int(derived_counts.get(curve_id, 0)) != 3:
            issues.append(
                QualityIssue(
                    "derived.coverage",
                    "error",
                    "derived_property",
                    str(curve_id),
                    "可派生拉伸曲线应具有3个标准指标",
                )
            )

    for table_name, frame in sorted(public_views.items()):
        issues.extend(check_public_release(frame, table_name))
    return sorted(
        issues,
        key=lambda item: (
            item.severity,
            item.rule_id,
            item.table_name,
            item.record_id,
            item.message,
        ),
    )


def _relative_metadata(metadata: Mapping[str, Any], root: Path) -> dict[str, Any]:
    output = dict(metadata)
    output["path"] = Path(str(metadata["path"])).resolve().relative_to(root).as_posix()
    return output


def _file_metadata(path: Path, root: Path, *, rows: int | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "path": path.resolve().relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }
    if rows is not None:
        metadata["rows"] = rows
    return metadata


def _write_qc(
    root: Path,
    snapshot_id: str,
    issues: Sequence[QualityIssue],
) -> dict[str, dict[str, Any]]:
    report_dir = root / "结果"
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_path = report_dir / "质量问题.csv"
    json_path = report_dir / "质量问题.json"
    frame = issues_frame(issues).sort_values(
        ["severity", "rule_id", "table_name", "record_id"], kind="mergesort"
    )
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig", lineterminator="\n")
    payload = {
        "snapshot_id": snapshot_id,
        "checks": [
            "primary_key",
            "provenance",
            "finite_values",
            "lineage_split",
            "curve_point_count",
            "unit_status",
            "derived_coverage",
            "public_release_license",
        ],
        "issue_counts": {
            severity: int((frame["severity"] == severity).sum())
            for severity in sorted(set(frame["severity"]))
        },
        "issues": frame.to_dict(orient="records"),
    }
    write_snapshot_manifest(json_path, payload)
    return {
        "qc_csv": _file_metadata(csv_path, root, rows=len(frame)),
        "qc_json": _file_metadata(json_path, root, rows=len(frame)),
    }


def field_coverage_frame(
    tables: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Profile non-null/non-blank coverage without turning absence into facts."""

    columns = [
        "table_name",
        "column_name",
        "dtype",
        "row_count",
        "present_count",
        "missing_count",
        "missing_fraction",
    ]
    records: list[dict[str, Any]] = []
    for table_name, frame in sorted(tables.items()):
        row_count = len(frame)
        for column_name in sorted(frame.columns, key=str.casefold):
            values = frame[column_name]
            present = values.notna()
            if values.dtype == object or isinstance(values.dtype, pd.StringDtype):
                present &= ~values.astype("string").str.strip().eq("").fillna(False)
            present_count = int(present.sum())
            missing_count = row_count - present_count
            records.append(
                {
                    "table_name": table_name,
                    "column_name": column_name,
                    "dtype": str(values.dtype),
                    "row_count": row_count,
                    "present_count": present_count,
                    "missing_count": missing_count,
                    "missing_fraction": (
                        float(missing_count / row_count) if row_count else 0.0
                    ),
                }
            )
    return pd.DataFrame(records, columns=columns)


def _write_field_coverage(
    root: Path,
    tables: Mapping[str, pd.DataFrame],
) -> dict[str, Any]:
    path = root / "结果" / "字段覆盖.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = field_coverage_frame(tables)
    frame.to_csv(
        path,
        index=False,
        encoding="utf-8-sig",
        lineterminator="\n",
        float_format="%.8f",
    )
    return _file_metadata(path, root, rows=len(frame))


def build_database(
    project_root: str | Path,
    manifest_rows: Iterable[Mapping[str, Any]] | pd.DataFrame,
    *,
    adapter_options: Mapping[str, Mapping[str, Any]] | None = None,
) -> DatabaseBuildResult:
    """构建四源分层数据库并返回行数、输出元数据和结构化问题。"""

    root = Path(project_root).resolve(strict=True)
    normalized_adapter_options = _normalize_adapter_options(adapter_options)
    staging, selected = extract_staging_tables(
        root, manifest_rows, adapter_options=normalized_adapter_options
    )
    normalized = normalize_staging_tables(staging, selected)
    derived, derivation_issues = derive_curve_metrics(
        normalized["curve"], normalized["curve_point"]
    )

    public_views = {
        f"public_{name}": _public_view(frame)
        for name, frame in normalized.items()
        if "may_publish" in frame.columns and "material_scope" in frame.columns
    }
    public_views["public_derived_property"] = _public_view(derived)
    issues = run_quality_checks(
        staging,
        normalized,
        derived,
        public_views,
        derivation_issues,
    )

    input_signature = []
    for row in selected.sort_values(
        ["source_id", "source_file_id"], kind="mergesort"
    ).to_dict(orient="records"):
        signature_row = {
            field: _signature_value(row.get(field))
            for field in _SNAPSHOT_SIGNATURE_FIELDS
        }
        signature_row["sha256"] = str(signature_row["sha256"]).upper()
        input_signature.append(signature_row)
    transformation_signature = pipeline_signature(root)
    build_parameters = {"adapter_options": normalized_adapter_options}
    snapshot_id = stable_id(
        "snapshot",
        SCHEMA_VERSION,
        transformation_signature,
        build_parameters,
        input_signature,
    )

    layer_frames: dict[str, tuple[Path, pd.DataFrame, Sequence[str]]] = {}
    for name, frame in staging.items():
        layer_frames[f"staging_{name}"] = (
            root / "数据/暂存" / f"{name}.parquet",
            frame,
            ["record_id"],
        )
    for name, frame in normalized.items():
        sort_keys = {
            "source": ["source_id"],
            "source_file": ["source_file_id"],
            "chemical_candidate": ["chemical_id"],
            "pue_transformed_auxiliary": ["record_id"],
            "curve": ["curve_id"],
            "curve_point": ["curve_id", "point_index"],
            "property_value": ["property_id"],
        }[name]
        layer_frames[f"normalized_{name}"] = (
            root / "数据/规范" / f"{name}.parquet",
            frame,
            sort_keys,
        )
    layer_frames["derived_property"] = (
        root / "数据/派生" / "derived_property.parquet",
        derived,
        ["derived_id"],
    )
    for name, frame in public_views.items():
        key = (
            ["derived_id"]
            if name == "public_derived_property"
            else {
                "public_source": ["source_id"],
                "public_source_file": ["source_file_id"],
                "public_chemical_candidate": ["chemical_id"],
                "public_pue_transformed_auxiliary": ["record_id"],
                "public_curve": ["curve_id"],
                "public_curve_point": ["curve_id", "point_index"],
                "public_property_value": ["property_id"],
            }[name]
        )
        layer_frames[name] = (
            root / "数据/派生" / f"{name}.parquet",
            frame,
            key,
        )

    outputs: dict[str, dict[str, Any]] = {}
    row_counts: dict[str, int] = {}
    snapshot_tables: dict[str, Path] = {}
    snapshot_dir = root / "数据/快照"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for table_name, (layer_path, frame, sort_keys) in sorted(layer_frames.items()):
        layer_meta = write_parquet_deterministic(frame, layer_path, sort_keys)
        outputs[table_name] = _relative_metadata(layer_meta, root)
        row_counts[table_name] = len(frame)

        snapshot_path = snapshot_dir / f"{table_name}.parquet"
        snapshot_meta = write_parquet_deterministic(frame, snapshot_path, sort_keys)
        outputs[f"snapshot_{table_name}"] = _relative_metadata(snapshot_meta, root)
        snapshot_tables[table_name] = snapshot_path

    database_path = snapshot_dir / "TPU数据库_v0.1.duckdb"
    build_duckdb(database_path, snapshot_tables)
    # DuckDB embeds storage-level metadata that is not guaranteed to be byte
    # identical across rebuilds even when every table is identical.  Treat it
    # as a rebuildable query cache; the deterministic integrity basis is the
    # sorted Parquet table set recorded above.
    outputs["duckdb"] = {
        "path": database_path.resolve().relative_to(root).as_posix(),
        "byte_reproducible": False,
        "content_basis": "snapshot_parquet_sha256_and_row_counts",
        "table_count": len(snapshot_tables),
    }
    outputs.update(_write_qc(root, snapshot_id, issues))
    outputs["field_coverage_csv"] = _write_field_coverage(
        root,
        {**normalized, "derived_property": derived},
    )

    snapshot_path = snapshot_dir / "TPU数据库_v0.1_快照.json"
    snapshot_payload = {
        "snapshot_id": snapshot_id,
        "schema_version": SCHEMA_VERSION,
        "pipeline": transformation_signature,
        "build_parameters": build_parameters,
        "input_hashes": input_signature,
        "row_counts": dict(sorted(row_counts.items())),
        "outputs": {name: outputs[name] for name in sorted(outputs)},
        "quality": {
            "errors": sum(issue.severity == "error" for issue in issues),
            "warnings": sum(issue.severity == "warning" for issue in issues),
        },
        "reproducibility": {
            "row_order": "stable_keys_mergesort",
            "parquet_compression": "zstd",
            "wall_clock_time_embedded": False,
        },
    }
    write_snapshot_manifest(snapshot_path, snapshot_payload)
    outputs["snapshot_json"] = _file_metadata(snapshot_path, root)

    return DatabaseBuildResult(
        snapshot_id=snapshot_id,
        row_counts=dict(sorted(row_counts.items())),
        outputs={name: outputs[name] for name in sorted(outputs)},
        issues=tuple(issues),
    )


__all__ = [
    "DatabaseBuildResult",
    "PIPELINE_VERSION",
    "REQUIRED_SOURCES",
    "SCHEMA_VERSION",
    "build_database",
    "derive_curve_metrics",
    "extract_staging_tables",
    "field_coverage_frame",
    "normalize_staging_tables",
    "pipeline_signature",
    "run_quality_checks",
]
