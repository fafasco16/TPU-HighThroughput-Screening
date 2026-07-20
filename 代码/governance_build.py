"""Atomic, deterministic integration build for the provisional TPU v0.2 registry.

The builder is deliberately limited to a direct child of ``数据/临时/构建缓存``.  It
never writes into the raw vault or any formal data layer, and it does not admit
scientific observations or create a training split.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pandas as pd

from asset_registry import (
    DUPLICATE_COLUMNS,
    REGISTRY_COLUMNS,
    AssetRecord,
    AssetRegistryResult,
    ExactDuplicateGroup,
    build_asset_registry,
    load_asset_rules,
    write_duplicate_groups_csv,
    write_registry_csv,
)
from build_verification import (
    ASSET_OUTPUT_FILES,
    BuildVerificationError,
    verify_v01_baseline,
)
from computational_admission import (
    ComputationalAdmissionError,
    ComputationalAdmissionProfile,
    ExactStructureOverlapProfile,
    profile_adept_candidates,
    profile_dq_matimpute,
    profile_exact_structure_overlaps,
    profile_polygraphmt,
    profile_polyomics,
    profile_structure_candidates,
    render_computational_admission_markdown,
)
from contract import ContractBundle, load_contract_bundle
from logical_hash import ALGORITHM_VERSION as LOGICAL_HASH_ALGORITHM_VERSION
from logical_hash import snapshot_logical_hash, table_logical_hash
from record_identity import canonical_identity_json, content_sha256
from source_governance import (
    OUTPUT_FILENAMES,
    TABLE_COLUMNS,
    SourceGovernanceBuild,
    build_source_governance,
    load_source_scope_config,
    resolve_asset_scope,
    write_source_governance_outputs,
)


DEFAULT_ASSET_RULES = Path("配置/v0.2资产登记规则.yaml")
DEFAULT_SOURCE_SCOPE_CONFIG = Path("配置/v0.2来源范围.yaml")
DEFAULT_CONTRACT_SCHEMA = Path("配置/结构定义/v0.2来源治理合同.yaml")
DEFAULT_ENUMS = Path("配置/结构定义/v0.2枚举.yaml")
DEFAULT_QUALITY_RULES = Path("配置/结构定义/v0.2质量规则.yaml")
DEFAULT_V01_SNAPSHOT = Path("数据/快照/TPU数据库_v0.1_快照.json")
SAFE_BUILD_DIRECTORY = "数据/临时/构建缓存"

TABLE_OUTPUTS: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "asset_registry": (ASSET_OUTPUT_FILES[0], REGISTRY_COLUMNS),
    "exact_duplicate_occurrence": (ASSET_OUTPUT_FILES[2], DUPLICATE_COLUMNS),
    **{
        table_name: (OUTPUT_FILENAMES[table_name], TABLE_COLUMNS[table_name])
        for table_name in TABLE_COLUMNS
    },
}
DECLARED_OUTPUT_FILES = tuple(
    [filename for filename, _columns in TABLE_OUTPUTS.values()]
    + [ASSET_OUTPUT_FILES[3], ASSET_OUTPUT_FILES[4]]
)


class GovernanceBuildError(RuntimeError):
    """A structured fail-closed integration-build failure."""

    def __init__(self, code: str, message: str, **context: object) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.context = context

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, **self.context}


@dataclass(frozen=True)
class GovernanceBuildResult:
    output_root: Path
    report: Mapping[str, object]


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def validate_output_target(
    project_root: str | Path, output_root: str | Path
) -> tuple[Path, Path]:
    """Resolve a shallow, non-destructive build target under ``数据/临时/构建缓存``."""

    try:
        project = Path(project_root).resolve(strict=True)
    except OSError as error:
        raise GovernanceBuildError(
            "project_root_missing", "项目根目录不存在", path=str(project_root)
        ) from error
    if not project.is_dir() or _is_reparse_point(project):
        raise GovernanceBuildError(
            "unsafe_project_root", "项目根目录必须是真实目录且不能是重解析点", path=str(project)
        )

    candidate = Path(output_root)
    if not candidate.is_absolute():
        candidate = project / candidate
    target = candidate.resolve(strict=False)
    safe_parent_path = project / SAFE_BUILD_DIRECTORY
    current_parent = project
    for component in Path(SAFE_BUILD_DIRECTORY).parts:
        current_parent = current_parent / component
        if current_parent.exists() and (
            not current_parent.is_dir() or _is_reparse_point(current_parent)
        ):
            raise GovernanceBuildError(
                "unsafe_output_parent",
                "数据/临时/构建缓存路径包含不可用目录或重解析点",
                path=str(current_parent),
            )
    safe_parent = safe_parent_path.resolve(strict=False)
    if not safe_parent.is_relative_to(project):
        raise GovernanceBuildError(
            "unsafe_output_parent",
            "数据/临时/构建缓存目录必须位于项目内部",
            path=str(safe_parent),
        )
    if target == safe_parent or target.parent != safe_parent or not target.name.strip():
        raise GovernanceBuildError(
            "unsafe_output_root",
            "构建输出必须是项目“数据/临时/构建缓存”目录下的一个直接子目录",
            project_root=str(project),
            output_root=str(target),
        )
    if safe_parent.exists() and (
        not safe_parent.is_dir() or _is_reparse_point(safe_parent)
    ):
        raise GovernanceBuildError(
            "unsafe_output_parent", "数据/临时/构建缓存目录不是可用的真实目录", path=str(safe_parent)
        )
    if target.exists():
        raise GovernanceBuildError(
            "output_root_exists", "最终构建目标必须不存在，拒绝覆盖或删除既有目录", path=str(target)
        )
    return project, target


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise GovernanceBuildError(
            "artifact_unreadable", "文件无法读取并计算 SHA-256", path=str(path)
        ) from error
    return digest.hexdigest()


def _document_hashes(paths: Mapping[str, Path]) -> dict[str, str]:
    return {name: _sha256_file(path) for name, path in sorted(paths.items())}


def _project_file(project: Path, path: str | Path, *, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = project / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise GovernanceBuildError(
            "input_document_missing", "构建输入文档不存在", label=label, path=str(candidate)
        ) from error
    if not resolved.is_file() or not resolved.is_relative_to(project) or _is_reparse_point(resolved):
        raise GovernanceBuildError(
            "unsafe_input_document",
            "构建输入文档必须是项目内真实文件",
            label=label,
            path=str(resolved),
        )
    return resolved


def _asset_input_fingerprint(result: AssetRegistryResult) -> str:
    rows = [
        {
            "relative_path": record.relative_path,
            "size_bytes": record.size_bytes,
            "content_sha256": record.content_sha256,
        }
        for record in result.records
    ]
    rows.sort(key=lambda row: (str(row["relative_path"]).casefold(), str(row["relative_path"])))
    return content_sha256(
        {
            "algorithm": "tpu-asset-input-fingerprint/1",
            "rows": rows,
        }
    )


class _BuildLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> "_BuildLock":
        try:
            self._fd = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.write(self._fd, b"TPU v0.2 governance build lock\n")
        except FileExistsError as error:
            raise GovernanceBuildError(
                "build_lock_exists",
                "同名构建已有活动锁；请先确认没有运行中的构建",
                lock_path=str(self.path),
            ) from error
        except OSError as error:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            self.path.unlink(missing_ok=True)
            raise GovernanceBuildError(
                "build_lock_failed", "无法创建独占构建锁", lock_path=str(self.path)
            ) from error
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            if exc is None:
                raise GovernanceBuildError(
                    "build_lock_release_failed",
                    "构建完成但独占锁无法释放",
                    lock_path=str(self.path),
                ) from error


def _csv_logical_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return canonical_identity_json(value)
    return str(value)


def canonical_table_logical_hash(
    columns: Sequence[str], rows: Iterable[Mapping[str, object]]
) -> tuple[int, str]:
    """Hash fixed-column logical CSV values independently of input row order."""

    column_list = list(columns)
    if not column_list or len(column_list) != len(set(column_list)):
        raise GovernanceBuildError(
            "invalid_hash_schema", "逻辑哈希列必须非空且唯一"
        )
    normalized: list[dict[str, str]] = []
    expected = set(column_list)
    for index, row in enumerate(rows):
        if set(row) != expected:
            raise GovernanceBuildError(
                "invalid_hash_row",
                "逻辑哈希行与固定列不一致",
                row_index=index,
                missing=sorted(expected - set(row)),
                extra=sorted(set(row) - expected),
            )
        normalized.append({name: _csv_logical_value(row[name]) for name in column_list})
    normalized.sort(key=canonical_identity_json)
    frame = pd.DataFrame(normalized, columns=column_list)
    schema = [(name, "string") for name in column_list]
    return len(normalized), table_logical_hash(
        frame,
        schema=schema,
        sort_key=column_list,
    )


def _duplicate_rows(groups: Iterable[ExactDuplicateGroup]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for group in groups:
        for member_path, member_uid in zip(
            group.members, group.member_occurrence_uids, strict=True
        ):
            rows.append(
                {
                    "duplicate_group_uid": group.duplicate_group_uid,
                    "content_sha256": group.content_sha256,
                    "member_count": group.member_count,
                    "canonical_asset_occurrence_uid": group.canonical_asset_occurrence_uid,
                    "canonical_relative_path": group.canonical_relative_path,
                    "member_asset_occurrence_uid": member_uid,
                    "member_relative_path": member_path,
                    "is_canonical": member_uid == group.canonical_asset_occurrence_uid,
                }
            )
    return rows


def _asset_mappings(records: Iterable[AssetRecord]) -> list[dict[str, object]]:
    return [
        {
            "relative_path": record.relative_path,
            "source_file_id": record.source_file_uid,
            "content_sha256": record.content_sha256,
        }
        for record in records
    ]


def validate_asset_source_join(
    records: Iterable[AssetRecord], source_config: Mapping[str, object]
) -> None:
    """Require exact agreement between asset rules and source-scope routing."""

    mismatches: list[dict[str, str]] = []
    for record in records:
        expected = resolve_asset_scope(record.relative_path, source_config)
        if expected != record.source_scope_key:
            mismatches.append(
                {
                    "relative_path": record.relative_path,
                    "asset_scope": record.source_scope_key,
                    "resolved_scope": expected,
                }
            )
    if mismatches:
        raise GovernanceBuildError(
            "asset_source_scope_mismatch",
            "资产规则与来源范围路由不一致",
            mismatch_count=len(mismatches),
            examples=mismatches[:20],
        )


def validate_source_file_join(
    records: Iterable[AssetRecord], source_build: SourceGovernanceBuild
) -> None:
    """Require one and only one locator for every registered source-file UID."""

    expected = [record.source_file_uid for record in records]
    actual = [str(row["source_file_id"]) for row in source_build.tables["source_locator"]]
    if len(actual) != len(set(actual)):
        raise GovernanceBuildError(
            "source_locator_duplicate", "来源定位表含重复 source_file_id"
        )
    if set(actual) != set(expected) or len(actual) != len(expected):
        raise GovernanceBuildError(
            "source_locator_antijoin_failed",
            "资产登记与来源定位未实现逐文件一一对应",
            asset_count=len(expected),
            locator_count=len(actual),
            missing_count=len(set(expected) - set(actual)),
            unexpected_count=len(set(actual) - set(expected)),
        )


def _dq_matimpute_counts(records: Iterable[AssetRecord]) -> tuple[int, int]:
    derived = 0
    model = 0
    for record in records:
        folded = record.relative_path.casefold()
        if not (
            folded.startswith("代码仓库镜像/dq/")
            or folded.startswith("代码仓库镜像/matimpute/")
        ):
            continue
        pue_related = (
            record.material_scope_hint == "crosslinked_pue"
            or "pue" in Path(record.relative_path).name.casefold()
            or "/pue/" in folded
        )
        if not pue_related:
            continue
        if record.artifact_role == "derived_duplicate":
            derived += 1
        elif record.artifact_role == "model_output":
            model += 1
    return derived, model


def collect_computational_profiles(
    raw_root: str | Path, records: Sequence[AssetRecord]
) -> tuple[ComputationalAdmissionProfile, ...]:
    """Profile local computational assets without promoting any observation."""

    raw = Path(raw_root)
    adept_input_count = sum(
        record.artifact_role == "simulation_input"
        and record.relative_path.startswith("代码仓库镜像/ADEPT/")
        and record.relative_path != "代码仓库镜像/ADEPT/SMILES.csv"
        for record in records
    )
    derived_count, model_count = _dq_matimpute_counts(records)
    matimpute_experiment = raw / "代码仓库镜像/MatImpute/experiment"
    repository_model_output_paths = tuple(
        raw / record.relative_path
        for record in records
        if record.artifact_role == "model_output"
        and record.relative_path.startswith("代码仓库镜像/MatImpute/")
    )
    dq_profile = profile_dq_matimpute(
        raw / "代码仓库镜像/DQ/experiment/datasets/PUE.csv",
        matimpute_experiment / "dataset/PUE.csv",
        dq_projection_paths={
            "logTS": raw / "代码仓库镜像/DQ/experiment/datasets/PUE-bak.csv",
            "logYM": raw / "代码仓库镜像/DQ/experiment/processed_data/PUE.csv",
        },
        missing_variants_directory=matimpute_experiment
        / "dataset/miss_datasets/PUE",
        model_output_paths={
            "rdf_ratio": matimpute_experiment / "3ratio-rdf-PUE.npy",
            "rdf_type": matimpute_experiment / "3types-rdf-PUE.npy",
            "filled_metrics": matimpute_experiment / "dataset/filled_results/PUE.csv",
            "distance_metrics": matimpute_experiment / "Et-knn-PUE_dis.csv",
            "rmse_metrics": matimpute_experiment / "Et-knn-PUE_rmse.csv",
            "rmse_workbook": matimpute_experiment / "std-rmse-pue.xlsx",
        },
        repository_model_output_paths=repository_model_output_paths,
    )
    if (
        dq_profile.diagnostics["derived_container_file_count"] != derived_count
        or dq_profile.diagnostics["model_output_file_count"] != model_count
    ):
        raise GovernanceBuildError(
            "dq_matimpute_asset_content_mismatch",
            "DQ/MatImpute 资产角色计数与逐内容审计不一致",
            asset_derived_count=derived_count,
            content_derived_count=dq_profile.diagnostics[
                "derived_container_file_count"
            ],
            asset_model_output_count=model_count,
            content_model_output_count=dq_profile.diagnostics[
                "model_output_file_count"
            ],
        )
    profiles = (
        profile_adept_candidates(
            raw / "代码仓库镜像/ADEPT/SMILES.csv",
            simulation_input_file_count=adept_input_count,
        ),
        dq_profile,
        profile_structure_candidates(
            raw / "基础数据/PI1M_v2.csv",
            source_key="pi1m",
            identity_column="SMILES",
            evidence_class="virtual_polymer_structure_candidate",
        ),
        profile_polygraphmt(raw / "代码仓库镜像/PolyGraphMT/data/raw"),
        profile_polyomics(
            raw / "外部数据/PolyOmics_general.csv",
            raw / "外部数据/PolyOmics_PURT.csv",
        ),
        profile_structure_candidates(
            raw / "基础数据/smipoly_monomers.csv",
            source_key="smipoly",
            identity_column="comID",
            system_column="SMILES",
            require_unique_identity=True,
            evidence_class="monomer_and_reaction_rule_candidate",
        ),
    )
    if any(profile.admitted_observation_count != 0 for profile in profiles):
        raise GovernanceBuildError(
            "premature_computational_admission",
            "画像阶段不得产生已准入计算观测",
        )
    return tuple(sorted(profiles, key=lambda profile: profile.source_key))


def collect_exact_structure_overlap(
    raw_root: str | Path,
) -> ExactStructureOverlapProfile:
    """Compute the frozen, case-sensitive cross-source leakage lower bound."""

    raw = Path(raw_root)
    return profile_exact_structure_overlaps(
        raw / "基础数据/PI1M_v2.csv",
        raw / "代码仓库镜像/ADEPT/SMILES.csv",
        raw / "外部数据/PolyOmics_general.csv",
        raw / "代码仓库镜像/PolyGraphMT/data/raw",
    )


def _overlap_payload(
    profile: ExactStructureOverlapProfile,
) -> dict[str, object]:
    return {
        "algorithm": "tpu-exact-structure-overlap/1",
        "source_exact_structure_counts": dict(
            sorted(profile.source_exact_structure_counts.items())
        ),
        "pair_overlap_counts": dict(sorted(profile.pair_overlap_counts.items())),
        "diagnostics": dict(sorted(profile.diagnostics.items())),
    }


_PROFILE_COLUMNS = (
    "source_key",
    "evidence_class",
    "file_count",
    "source_record_candidate_count",
    "unique_system_candidate_count",
    "computational_activity_candidate_count",
    "computational_observation_candidate_count",
    "admitted_observation_count",
    "fidelity_counts",
    "diagnostics",
    "blocking_reason",
)


def _profile_rows(
    profiles: Iterable[ComputationalAdmissionProfile],
) -> list[dict[str, object]]:
    return [
        {column: getattr(profile, column) for column in _PROFILE_COLUMNS}
        for profile in profiles
    ]


def _in_memory_table_rows(
    asset_result: AssetRegistryResult, source_build: SourceGovernanceBuild
) -> dict[str, list[dict[str, object]]]:
    return {
        "asset_registry": [record.as_csv_row() for record in asset_result.records],
        "exact_duplicate_occurrence": _duplicate_rows(asset_result.duplicate_groups),
        **{table: list(rows) for table, rows in source_build.tables.items()},
    }


def _read_csv_table(path: Path, columns: Sequence[str]) -> list[dict[str, str]]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise GovernanceBuildError(
            "output_table_unreadable", "输出表无法读取", path=str(path)
        ) from error
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise GovernanceBuildError(
            "output_table_missing_bom", "输出 CSV 必须使用 UTF-8 BOM", path=str(path)
        )
    if b"\r" in raw:
        raise GovernanceBuildError(
            "output_table_non_lf", "输出 CSV 必须统一使用 LF 换行", path=str(path)
        )
    import csv

    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != tuple(columns):
                raise GovernanceBuildError(
                    "output_table_schema_mismatch",
                    "输出 CSV 表头与冻结列不一致",
                    path=str(path),
                    expected=list(columns),
                    actual=list(reader.fieldnames or ()),
                )
            rows: list[dict[str, str]] = []
            for index, row in enumerate(reader, start=2):
                if None in row or set(row) != set(columns):
                    raise GovernanceBuildError(
                        "output_table_row_malformed",
                        "输出 CSV 行含多余或缺失字段",
                        path=str(path),
                        line=index,
                    )
                rows.append({column: row[column] for column in columns})
            return rows
    except UnicodeError as error:
        raise GovernanceBuildError(
            "output_table_decode_failed", "输出 CSV 无法按 UTF-8 BOM 解码", path=str(path)
        ) from error


def _table_descriptors_from_rows(
    rows_by_table: Mapping[str, Iterable[Mapping[str, object]]]
) -> dict[str, tuple[int, str]]:
    if set(rows_by_table) != set(TABLE_OUTPUTS):
        raise GovernanceBuildError(
            "output_table_set_mismatch",
            "内存表集合与冻结输出集合不一致",
            missing=sorted(set(TABLE_OUTPUTS) - set(rows_by_table)),
            extra=sorted(set(rows_by_table) - set(TABLE_OUTPUTS)),
        )
    return {
        table: canonical_table_logical_hash(TABLE_OUTPUTS[table][1], rows_by_table[table])
        for table in TABLE_OUTPUTS
    }


def _read_written_table_descriptors(root: Path) -> dict[str, tuple[int, str]]:
    return {
        table: canonical_table_logical_hash(
            columns, _read_csv_table(root / filename, columns)
        )
        for table, (filename, columns) in TABLE_OUTPUTS.items()
    }


def _output_artifact_manifest(
    root: Path, table_descriptors: Mapping[str, tuple[int, str]]
) -> dict[str, dict[str, object]]:
    table_by_filename = {
        filename: table for table, (filename, _columns) in TABLE_OUTPUTS.items()
    }
    artifacts: dict[str, dict[str, object]] = {}
    for filename in sorted(set(DECLARED_OUTPUT_FILES) - {ASSET_OUTPUT_FILES[3]}):
        path = root / filename
        if not path.is_file():
            raise GovernanceBuildError(
                "output_artifact_missing", "构建缺少声明产物", artifact=filename
            )
        entry: dict[str, object] = {
            "byte_sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        table = table_by_filename.get(filename)
        if table is not None:
            entry.update(
                {
                    "kind": "table_csv",
                    "table_name": table,
                    "row_count": table_descriptors[table][0],
                    "logical_hash": table_descriptors[table][1],
                }
            )
        else:
            entry["kind"] = "computational_admission_report"
        artifacts[filename] = entry
    return artifacts


def _build_report(
    asset_result: AssetRegistryResult,
    source_build: SourceGovernanceBuild,
    profiles: Sequence[ComputationalAdmissionProfile],
    overlap_profile: ExactStructureOverlapProfile,
    *,
    table_descriptors: Mapping[str, tuple[int, str]],
    output_artifacts: Mapping[str, Mapping[str, object]],
    input_document_hashes: Mapping[str, str],
    contract_document_hashes: Mapping[str, str],
    raw_input_fingerprint: str,
    v01_baseline_pre: Mapping[str, object],
    v01_baseline_post: Mapping[str, object],
) -> dict[str, object]:
    table_hashes = {
        table: descriptor[1] for table, descriptor in sorted(table_descriptors.items())
    }
    table_rows = {
        table: descriptor[0] for table, descriptor in sorted(table_descriptors.items())
    }
    audit = dict(asset_result.audit)
    overlap_payload = _overlap_payload(overlap_profile)
    audit.update(
        {
            "status": "provisional_pass",
            "audit_scope": "integrated_asset_source_computational_profile",
            "unknown_scope_count": 0,
            "scope_mismatch_count": 0,
            "integration_pending": [],
            "logical_hash_algorithm": f"{LOGICAL_HASH_ALGORITHM_VERSION}:csv-string-domain",
            "table_row_counts": table_rows,
            "table_logical_hashes": table_hashes,
            "snapshot_logical_hash": snapshot_logical_hash(table_descriptors),
            "source_governance_logical_hash": source_build.logical_hash,
            "source_count": len(source_build.tables["source"]),
            "source_scope_count": len(source_build.tables["source_scope"]),
            "source_locator_count": len(source_build.tables["source_locator"]),
            "citation_count": len(source_build.tables["citation"]),
            "rights_action_candidate_count": len(
                source_build.tables["rights_action_candidate"]
            ),
            "computational_profile_count": len(profiles),
            "computational_profile_logical_hash": content_sha256(
                {
                    "algorithm": "tpu-computational-profile/1",
                    "rows": _profile_rows(profiles),
                }
            ),
            "computational_admitted_observation_count": sum(
                profile.admitted_observation_count for profile in profiles
            ),
            "exact_structure_overlap": overlap_payload,
            "exact_structure_overlap_logical_hash": content_sha256(overlap_payload),
            "training_split_created": False,
            "training_weight_configured": False,
            "raw_input_fingerprint_algorithm": "tpu-asset-input-fingerprint/1",
            "raw_input_fingerprint": raw_input_fingerprint,
            "input_document_sha256": dict(sorted(input_document_hashes.items())),
            "contract_document_hashes": dict(sorted(contract_document_hashes.items())),
            "declared_output_files": sorted(DECLARED_OUTPUT_FILES),
            "output_artifacts": {
                name: dict(value) for name, value in sorted(output_artifacts.items())
            },
            "audit_self_binding": "审计JSON不递归哈希自身；双构建直接比较其字节",
            "v01_baseline": {
                "pre": dict(v01_baseline_pre),
                "post": dict(v01_baseline_post),
            },
            "anti_join_counts": {
                "assets_missing_locator": 0,
                "locators_missing_asset": 0,
                "scope_mismatches": 0,
            },
        }
    )
    return audit


def _write_text(path: Path, text: str) -> None:
    path.write_text(text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8", newline="\n")


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def _load_audit_report(root: Path) -> dict[str, object]:
    path = root / ASSET_OUTPUT_FILES[3]
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GovernanceBuildError(
            "audit_report_invalid", "治理构建审计 JSON 无法读取", path=str(path)
        ) from error
    if not isinstance(payload, dict):
        raise GovernanceBuildError(
            "audit_report_invalid", "治理构建审计 JSON 顶层必须是对象", path=str(path)
        )
    return payload


def audit_governance_build(build_root: str | Path) -> dict[str, object]:
    """Recompute every declared table and artifact digest from a finished build."""

    try:
        root = Path(build_root).resolve(strict=True)
    except OSError as error:
        raise GovernanceBuildError(
            "build_root_missing", "治理构建目录不存在", path=str(build_root)
        ) from error
    if not root.is_dir() or _is_reparse_point(root):
        raise GovernanceBuildError(
            "unsafe_build_root", "治理构建根必须是真实目录", path=str(root)
        )
    actual_entries = list(root.iterdir())
    non_files = [path.name for path in actual_entries if not path.is_file()]
    if non_files:
        raise GovernanceBuildError(
            "output_file_set_mismatch",
            "治理构建根包含非文件或未声明目录",
            missing=[],
            extra=non_files,
        )
    actual_files = sorted(path.name for path in actual_entries)
    expected_files = sorted(DECLARED_OUTPUT_FILES)
    if actual_files != expected_files:
        raise GovernanceBuildError(
            "output_file_set_mismatch",
            "治理构建产物集合与冻结清单不一致",
            missing=sorted(set(expected_files) - set(actual_files)),
            extra=sorted(set(actual_files) - set(expected_files)),
        )
    report = _load_audit_report(root)
    if report.get("status") != "provisional_pass" or report.get("schema_version") != "v0.2":
        raise GovernanceBuildError(
            "audit_status_invalid", "治理构建尚未通过 provisional_pass v0.2 门"
        )
    if report.get("declared_output_files") != expected_files:
        raise GovernanceBuildError(
            "declared_output_set_mismatch", "审计 JSON 声明的产物集合不正确"
        )
    for field in (
        "unclassified_count",
        "ambiguous_count",
        "read_failure_count",
        "unknown_scope_count",
        "scope_mismatch_count",
        "missing_status_count",
        "computational_admitted_observation_count",
    ):
        value = report.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value != 0:
            raise GovernanceBuildError(
                "audit_blocking_count", "治理构建含非零或非法阻断计数", field=field, value=value
            )
    if report.get("training_split_created") is not False or report.get("training_weight_configured") is not False:
        raise GovernanceBuildError(
            "premature_training_state", "治理画像阶段不得创建训练拆分或权重"
        )
    overlap_payload = report.get("exact_structure_overlap")
    if not isinstance(overlap_payload, dict):
        raise GovernanceBuildError(
            "overlap_profile_invalid", "治理构建缺少结构重叠画像"
        )
    try:
        overlap_profile = ExactStructureOverlapProfile(
            source_exact_structure_counts=overlap_payload["source_exact_structure_counts"],
            pair_overlap_counts=overlap_payload["pair_overlap_counts"],
            diagnostics=overlap_payload["diagnostics"],
        )
    except (KeyError, TypeError, ComputationalAdmissionError) as error:
        raise GovernanceBuildError(
            "overlap_profile_invalid", "结构重叠画像不符合冻结合同"
        ) from error
    canonical_overlap = _overlap_payload(overlap_profile)
    if overlap_payload != canonical_overlap or report.get(
        "exact_structure_overlap_logical_hash"
    ) != content_sha256(canonical_overlap):
        raise GovernanceBuildError(
            "overlap_profile_hash_mismatch", "结构重叠画像逻辑哈希不一致"
        )

    descriptors = _read_written_table_descriptors(root)
    expected_hashes = report.get("table_logical_hashes")
    expected_rows = report.get("table_row_counts")
    actual_hashes = {name: value[1] for name, value in sorted(descriptors.items())}
    actual_rows = {name: value[0] for name, value in sorted(descriptors.items())}
    if expected_hashes != actual_hashes or expected_rows != actual_rows:
        raise GovernanceBuildError(
            "table_logical_hash_mismatch",
            "审计声明与落盘 CSV 的重算逻辑哈希或行数不一致",
        )
    actual_snapshot = snapshot_logical_hash(descriptors)
    if report.get("snapshot_logical_hash") != actual_snapshot:
        raise GovernanceBuildError(
            "snapshot_logical_hash_mismatch", "快照逻辑哈希重算不一致"
        )
    actual_artifacts = _output_artifact_manifest(root, descriptors)
    if report.get("output_artifacts") != actual_artifacts:
        raise GovernanceBuildError(
            "artifact_manifest_mismatch", "产物字节哈希、大小或表摘要重算不一致"
        )
    input_count = report.get("input_count")
    registered = report.get("registered_count")
    excluded = report.get("excluded_count")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (input_count, registered, excluded)):
        raise GovernanceBuildError(
            "asset_count_invalid", "资产发现、登记或排除计数非法"
        )
    if input_count != registered + excluded or actual_rows["asset_registry"] != input_count:
        raise GovernanceBuildError(
            "asset_count_not_reconciled", "资产计数与全量登记表未闭环"
        )
    return {
        "status": "provisional_pass",
        "schema_version": "v0.2",
        "artifact_count": len(actual_files),
        "table_count": len(descriptors),
        "input_count": input_count,
        "snapshot_logical_hash": actual_snapshot,
    }


def compare_governance_builds(
    left: str | Path,
    right: str | Path,
    *,
    require_byte_identical: bool = True,
    require_logical_identical: bool = True,
) -> dict[str, object]:
    """Strictly compare every output after independently auditing both builds."""

    left_root = Path(left).resolve(strict=True)
    right_root = Path(right).resolve(strict=True)
    left_audit = audit_governance_build(left_root)
    right_audit = audit_governance_build(right_root)
    byte_hashes = {
        filename: {
            "left": _sha256_file(left_root / filename),
            "right": _sha256_file(right_root / filename),
        }
        for filename in sorted(DECLARED_OUTPUT_FILES)
    }
    byte_identical = all(value["left"] == value["right"] for value in byte_hashes.values())
    logical_identical = (
        left_audit["snapshot_logical_hash"] == right_audit["snapshot_logical_hash"]
    )
    if require_byte_identical and not byte_identical:
        raise GovernanceBuildError(
            "governance_build_bytes_differ",
            "两个隔离治理构建并非全产物字节一致",
            differing_files=[
                name for name, value in byte_hashes.items() if value["left"] != value["right"]
            ],
        )
    if require_logical_identical and not logical_identical:
        raise GovernanceBuildError(
            "governance_build_logical_hashes_differ",
            "两个隔离治理构建的快照逻辑哈希不一致",
        )
    return {
        "status": "identical" if byte_identical and logical_identical else "different",
        "byte_identical": byte_identical,
        "logical_identical": logical_identical,
        "artifact_sha256": byte_hashes,
        "snapshot_logical_hash": {
            "left": left_audit["snapshot_logical_hash"],
            "right": right_audit["snapshot_logical_hash"],
        },
    }


def build_governance_database(
    project_root: str | Path,
    output_root: str | Path,
    *,
    asset_rules_path: str | Path = DEFAULT_ASSET_RULES,
    source_scope_path: str | Path = DEFAULT_SOURCE_SCOPE_CONFIG,
    contract_schema_path: str | Path = DEFAULT_CONTRACT_SCHEMA,
    enums_path: str | Path = DEFAULT_ENUMS,
    quality_rules_path: str | Path = DEFAULT_QUALITY_RULES,
    v01_snapshot_path: str | Path = DEFAULT_V01_SNAPSHOT,
) -> GovernanceBuildResult:
    """Build one isolated v0.2 governance snapshot and atomically publish it."""

    project, target = validate_output_target(project_root, output_root)
    documents = {
        "asset_rules": _project_file(project, asset_rules_path, label="asset_rules"),
        "source_scopes": _project_file(project, source_scope_path, label="source_scopes"),
        "contract_schema": _project_file(project, contract_schema_path, label="contract_schema"),
        "enums": _project_file(project, enums_path, label="enums"),
        "quality_rules": _project_file(project, quality_rules_path, label="quality_rules"),
        "v01_snapshot": _project_file(project, v01_snapshot_path, label="v01_snapshot"),
    }
    pre_document_hashes = _document_hashes(documents)
    contract_bundle: ContractBundle = load_contract_bundle(
        documents["contract_schema"], documents["enums"], documents["quality_rules"]
    )
    try:
        baseline_pre = verify_v01_baseline(project, documents["v01_snapshot"])
    except BuildVerificationError as error:
        raise GovernanceBuildError(
            "v01_baseline_pre_failed",
            "v0.1 冻结基线在治理构建前验证失败",
            baseline_error=error.as_dict(),
        ) from error

    asset_config = load_asset_rules(documents["asset_rules"])
    raw_root = (project / asset_config.root_hint).resolve(strict=True)
    if not raw_root.is_dir() or not raw_root.is_relative_to(project):
        raise GovernanceBuildError(
            "unsafe_discovery_root", "资产发现根必须是项目内真实目录", path=str(raw_root)
        )
    source_config = load_source_scope_config(documents["source_scopes"])

    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.parent.is_dir() or _is_reparse_point(target.parent):
        raise GovernanceBuildError(
            "unsafe_output_parent", "数据/临时/构建缓存目录创建后不是可用的真实目录", path=str(target.parent)
        )
    lock_path = target.parent / f".{target.name}.lock"
    with _BuildLock(lock_path):
        if target.exists():
            raise GovernanceBuildError(
                "output_root_exists", "加锁后发现最终构建目标已存在", path=str(target)
            )
        stage = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.构建中-", dir=target.parent)
        )
        try:
            asset_result = build_asset_registry(raw_root, asset_config)
            raw_input_fingerprint = _asset_input_fingerprint(asset_result)
            validate_asset_source_join(asset_result.records, source_config)
            source_build = build_source_governance(
                source_config, _asset_mappings(asset_result.records)
            )
            validate_source_file_join(asset_result.records, source_build)
            profiles = collect_computational_profiles(raw_root, asset_result.records)
            overlap_profile = collect_exact_structure_overlap(raw_root)

            write_registry_csv(stage / ASSET_OUTPUT_FILES[0], asset_result.records)
            write_duplicate_groups_csv(
                stage / ASSET_OUTPUT_FILES[2], asset_result.duplicate_groups
            )
            write_source_governance_outputs(source_build, stage)
            _write_text(
                stage / ASSET_OUTPUT_FILES[4],
                render_computational_admission_markdown(
                    profiles,
                    overlap_profile=overlap_profile,
                    ledger_link="../../文档/数据来源与参考文献.md",
                ),
            )

            in_memory_descriptors = _table_descriptors_from_rows(
                _in_memory_table_rows(asset_result, source_build)
            )
            written_descriptors = _read_written_table_descriptors(stage)
            if in_memory_descriptors != written_descriptors:
                raise GovernanceBuildError(
                    "memory_disk_logical_hash_mismatch",
                    "内存表与落盘 CSV 的逻辑哈希或行数不一致",
                )

            second_asset_result = build_asset_registry(raw_root, asset_config)
            if _asset_input_fingerprint(second_asset_result) != raw_input_fingerprint:
                raise GovernanceBuildError(
                    "raw_input_drift",
                    "原始资产在治理构建期间发生新增、删除或内容变化",
                )
            post_document_hashes = _document_hashes(documents)
            if post_document_hashes != pre_document_hashes:
                raise GovernanceBuildError(
                    "input_document_drift", "规则、合同或 v0.1 快照在构建期间发生变化"
                )
            try:
                baseline_post = verify_v01_baseline(project, documents["v01_snapshot"])
            except BuildVerificationError as error:
                raise GovernanceBuildError(
                    "v01_baseline_post_failed",
                    "v0.1 冻结基线在治理构建后验证失败",
                    baseline_error=error.as_dict(),
                ) from error
            if baseline_post != baseline_pre:
                raise GovernanceBuildError(
                    "v01_baseline_drift", "v0.1 基线前后验证证据不一致"
                )

            output_artifacts = _output_artifact_manifest(stage, written_descriptors)
            report = _build_report(
                asset_result,
                source_build,
                profiles,
                overlap_profile,
                table_descriptors=written_descriptors,
                output_artifacts=output_artifacts,
                input_document_hashes=pre_document_hashes,
                contract_document_hashes=contract_bundle.document_hashes,
                raw_input_fingerprint=raw_input_fingerprint,
                v01_baseline_pre=baseline_pre,
                v01_baseline_post=baseline_post,
            )
            _write_json(stage / ASSET_OUTPUT_FILES[3], report)
            audit_governance_build(stage)

            if target.exists():
                raise GovernanceBuildError(
                    "output_root_race", "发布前最终构建目标被其他进程创建", path=str(target)
                )
            os.rename(stage, target)
            return GovernanceBuildResult(output_root=target, report=report)
        except BaseException:
            expected_prefix = f".{target.name}.构建中-"
            if (
                stage.exists()
                and stage.parent == target.parent
                and stage.name.startswith(expected_prefix)
                and not _is_reparse_point(stage)
            ):
                shutil.rmtree(stage)
            raise


__all__ = [
    "DEFAULT_ASSET_RULES",
    "DEFAULT_CONTRACT_SCHEMA",
    "DEFAULT_ENUMS",
    "DEFAULT_QUALITY_RULES",
    "DEFAULT_SOURCE_SCOPE_CONFIG",
    "DEFAULT_V01_SNAPSHOT",
    "DECLARED_OUTPUT_FILES",
    "GovernanceBuildError",
    "GovernanceBuildResult",
    "SAFE_BUILD_DIRECTORY",
    "TABLE_OUTPUTS",
    "audit_governance_build",
    "build_governance_database",
    "canonical_table_logical_hash",
    "collect_computational_profiles",
    "collect_exact_structure_overlap",
    "compare_governance_builds",
    "validate_asset_source_join",
    "validate_output_target",
    "validate_source_file_join",
]
