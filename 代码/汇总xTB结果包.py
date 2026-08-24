"""安全、流式汇总分片 xTB 结果包并生成构象与构件级描述符。

每次只打开一个归档，逐个读取白名单成员到受控临时目录；从不调用
``extractall``。一个构件中任一构象未完成或未通过身份、路径、哈希、解析与
反应位点门禁时，该构件的完整 Boltzmann 代理权重及加权统计全部关闭。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

import pandas as pd

from xTB系综任务 import atom_order_sha256
from xTB输出解析 import (
    DEFAULT_ENSEMBLE_TEMPERATURE_K,
    XtbOutputError,
    aggregate_component_ensemble,
    parse_conformer_directory,
)
from 反应位点描述符 import (
    ReactiveSiteDescriptorError,
    describe_reactive_sites,
    parse_xyz_conformers,
    prepare_reactive_site_model,
)


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SLUG_PATTERN = re.compile(r"\d{4}_\d{6}_cf_[0-9a-f]{20}")
ALLOWED_ARCHIVE_MEMBERS = frozenset(
    {"conformer.xyz", "xtbout.json", "xtb.out", "wbo", "charges", "xtbtopo.mol"}
)
REQUIRED_ARCHIVE_MEMBERS = frozenset(
    {"conformer.xyz", "xtbout.json", "xtb.out", "wbo"}
)
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_UNPACKED_BYTES = 128 * 1024 * 1024

IDENTITY_FIELDS = (
    "xtb_task_index",
    "xtb_task_slug",
    "candidate_id",
    "component_role",
    "conformer_id",
    "source_task_slug",
    "crest_rank",
)

ENSEMBLE_SCALAR_FIELDS = (
    "homo_ev",
    "lumo_ev",
    "homo_lumo_gap_ev",
    "dipole_magnitude_debye",
    "gfn2_d4_alpha0_au",
    "site_charge_e_mean",
    "site_charge_e_abs_difference",
    "site_atomic_alpha0_au_mean",
    "site_atomic_alpha0_au_abs_difference",
    "site_incident_wbo_sum_mean",
    "site_incident_wbo_sum_abs_difference",
    "site_sasa_a2_mean",
    "site_relative_sasa_mean",
    "site_nonbonded_net_gap_a_mean",
    "reactive_site_distance_a",
)

CONFORMER_COLUMNS = (
    *IDENTITY_FIELDS,
    "canonical_smiles",
    "run_status",
    "warning_codes",
    "failure_type",
    "failure_message",
    "archive_file",
    "archive_sha256",
    "method",
    "xtb_version",
    "xtb_version_full",
    "total_energy_hartree",
    "relative_energy_kcal_mol",
    "boltzmann_proxy_weight_298K",
    "homo_ev",
    "lumo_ev",
    "homo_lumo_gap_ev",
    "reported_homo_lumo_gap_ev",
    "dipole_x_au",
    "dipole_y_au",
    "dipole_z_au",
    "dipole_magnitude_debye",
    "partial_charge_sum_e",
    "gfn2_d4_alpha0_au",
    "reactive_site_kind",
    "reactive_site_count",
    "reactive_site_distance_a",
    "probe_radius_a",
    "sphere_point_count",
    "site_1_atom_index",
    "site_1_charge_e",
    "site_1_atomic_alpha0_au",
    "site_1_incident_wbo_count",
    "site_1_incident_wbo_sum",
    "site_1_incident_wbo_mean",
    "site_1_sasa_a2",
    "site_1_relative_sasa",
    "site_1_nonbonded_net_gap_a",
    "site_2_atom_index",
    "site_2_charge_e",
    "site_2_atomic_alpha0_au",
    "site_2_incident_wbo_count",
    "site_2_incident_wbo_sum",
    "site_2_incident_wbo_mean",
    "site_2_sasa_a2",
    "site_2_relative_sasa",
    "site_2_nonbonded_net_gap_a",
    "site_charge_e_mean",
    "site_charge_e_min",
    "site_charge_e_max",
    "site_charge_e_abs_difference",
    "site_atomic_alpha0_au_mean",
    "site_atomic_alpha0_au_min",
    "site_atomic_alpha0_au_max",
    "site_atomic_alpha0_au_abs_difference",
    "site_incident_wbo_sum_mean",
    "site_incident_wbo_sum_min",
    "site_incident_wbo_sum_max",
    "site_incident_wbo_sum_abs_difference",
    "site_sasa_a2_mean",
    "site_sasa_a2_min",
    "site_sasa_a2_max",
    "site_sasa_a2_abs_difference",
    "site_relative_sasa_mean",
    "site_relative_sasa_min",
    "site_relative_sasa_max",
    "site_relative_sasa_abs_difference",
    "site_nonbonded_net_gap_a_mean",
    "site_nonbonded_net_gap_a_min",
    "site_nonbonded_net_gap_a_max",
    "site_nonbonded_net_gap_a_abs_difference",
    "xtbout_json_sha256",
    "stdout_sha256",
    "wbo_sha256",
)

FAILURE_COLUMNS = (*IDENTITY_FIELDS, "failure_type", "failure_message")


class XtbArchiveAggregateError(ValueError):
    """输入表、状态或归档未通过安全/科学数据门。"""


@dataclass(frozen=True)
class ComponentResult:
    conformers: list[dict[str, Any]]
    component: dict[str, Any]
    failures: list[dict[str, Any]]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scalar(value: Any) -> Any:
    """把 NumPy/Pandas 标量规范化；大数组、映射和坐标不得进入发布行。"""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        value = value.item()
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
    raise XtbArchiveAggregateError(f"non-scalar output value: {type(value).__name__}")


def _aggregate_pair(prefix: str, values: Sequence[float | None]) -> dict[str, float | None]:
    if any(value is None for value in values):
        return {
            f"{prefix}_mean": None,
            f"{prefix}_min": None,
            f"{prefix}_max": None,
            f"{prefix}_abs_difference": None,
        }
    first, second = float(values[0]), float(values[1])
    return {
        f"{prefix}_mean": math.fsum((first, second)) / 2,
        f"{prefix}_min": min(first, second),
        f"{prefix}_max": max(first, second),
        f"{prefix}_abs_difference": abs(first - second),
    }


def _required_columns(table: pd.DataFrame, required: set[str], label: str) -> None:
    missing = required.difference(table.columns)
    if missing:
        raise XtbArchiveAggregateError(f"{label}缺少字段: {sorted(missing)}")


def _validate_inputs(
    xtb_tasks: pd.DataFrame, crest_tasks: pd.DataFrame
) -> dict[str, Mapping[str, Any]]:
    _required_columns(
        xtb_tasks,
        {
            "xtb_task_index", "xtb_task_slug", "source_task_slug", "candidate_id",
            "component_role", "conformer_id", "crest_rank", "conformer_xyz_sha256",
            "atom_count", "atom_order_sha256", "charge", "uhf", "xtb_version",
            "xtb_binary_sha256", "method", "environment_model",
        },
        "xTB任务表",
    )
    _required_columns(crest_tasks, {"task_slug", "canonical_smiles"}, "CREST源任务表")
    for field in ("xtb_task_index", "xtb_task_slug", "conformer_id"):
        if not xtb_tasks[field].is_unique:
            raise XtbArchiveAggregateError(f"xTB任务表{field}不唯一")
    if not crest_tasks["task_slug"].is_unique:
        raise XtbArchiveAggregateError("CREST源任务表task_slug不唯一")
    source_map = {
        str(row["task_slug"]): row
        for row in crest_tasks.to_dict(orient="records")
    }
    missing_sources = sorted(set(map(str, xtb_tasks["source_task_slug"])) - source_map.keys())
    if missing_sources:
        raise XtbArchiveAggregateError(f"xTB任务缺少CREST源记录: {missing_sources[:3]}")
    if any(not str(source_map[str(slug)]["canonical_smiles"]).strip() for slug in xtb_tasks["source_task_slug"]):
        raise XtbArchiveAggregateError("CREST源记录canonical_smiles为空")
    return source_map


def _task_shard(conformer_id: Any) -> str:
    value = str(conformer_id)
    if not re.fullmatch(r"cf_[0-9a-f]{20}", value):
        raise XtbArchiveAggregateError("invalid conformer_id")
    return value[3:5]


def _layout(root: Path, task: Mapping[str, Any]) -> tuple[Path, Path, str]:
    slug = str(task["xtb_task_slug"])
    if not SLUG_PATTERN.fullmatch(slug):
        raise XtbArchiveAggregateError("invalid xtb_task_slug")
    shard = _task_shard(task["conformer_id"])
    relative_archive = f"结果包/{shard}/{slug}.tar.gz"
    return (
        root / "状态" / shard / f"{slug}.json",
        root / PurePosixPath(relative_archive),
        relative_archive,
    )


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise XtbArchiveAggregateError(f"missing {label}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise XtbArchiveAggregateError(f"invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise XtbArchiveAggregateError(f"{label} root must be an object")
    return value


def _validate_state(
    root: Path, task: Mapping[str, Any]
) -> tuple[dict[str, Any], Path, dict[str, str]]:
    state_path, expected_archive, expected_relative = _layout(root, task)
    state = _read_json_object(state_path, "xTB state")
    if state.get("status") != "completed":
        raise XtbArchiveAggregateError(f"state_not_completed:{state.get('status', 'missing')}")
    expected_identity = {
        "xtb_task_index": int(task["xtb_task_index"]),
        "xtb_task_slug": str(task["xtb_task_slug"]),
        "candidate_id": str(task["candidate_id"]),
        "component_role": str(task["component_role"]),
        "conformer_id": str(task["conformer_id"]),
        "input_sha256": str(task["conformer_xyz_sha256"]),
        "atom_order_sha256": str(task["atom_order_sha256"]),
        "charge": int(task["charge"]),
        "uhf": int(task["uhf"]),
        "xtb_version": str(task["xtb_version"]),
        "xtb_binary_sha256": str(task["xtb_binary_sha256"]),
        "method": str(task["method"]),
        "environment_model": str(task["environment_model"]),
    }
    for field, expected in expected_identity.items():
        if state.get(field) != expected:
            raise XtbArchiveAggregateError(f"state identity mismatch:{field}")
    if state.get("archive_file") != expected_relative:
        raise XtbArchiveAggregateError("archive path mismatch or traversal")
    if not expected_archive.is_file():
        raise XtbArchiveAggregateError("completed state points to missing archive")
    expected_archive_hash = state.get("archive_sha256")
    if not isinstance(expected_archive_hash, str) or not SHA256_PATTERN.fullmatch(expected_archive_hash):
        raise XtbArchiveAggregateError("invalid archive_sha256")
    if sha256(expected_archive) != expected_archive_hash:
        raise XtbArchiveAggregateError("archive_sha256 mismatch")
    member_hashes = state.get("archive_member_sha256")
    if not isinstance(member_hashes, dict):
        raise XtbArchiveAggregateError("missing archive_member_sha256")
    if not REQUIRED_ARCHIVE_MEMBERS.issubset(member_hashes):
        raise XtbArchiveAggregateError("archive member manifest missing required member")
    if not set(member_hashes).issubset(ALLOWED_ARCHIVE_MEMBERS):
        raise XtbArchiveAggregateError("archive member manifest contains unknown member")
    if any(not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value) for value in member_hashes.values()):
        raise XtbArchiveAggregateError("invalid archive member SHA-256")
    if member_hashes["conformer.xyz"] != str(task["conformer_xyz_sha256"]):
        raise XtbArchiveAggregateError("conformer member SHA-256 differs from task input")
    output_hashes = state.get("output_sha256")
    if not isinstance(output_hashes, dict) or any(member_hashes.get(name) != value for name, value in output_hashes.items()):
        raise XtbArchiveAggregateError("output/member SHA-256 mismatch")
    return state, expected_archive, {str(key): str(value) for key, value in member_hashes.items()}


@contextmanager
def _verified_archive_directory(
    archive_path: Path,
    member_hashes: Mapping[str, str],
    temporary_parent: Path | None,
) -> Iterator[Path]:
    """逐成员复制并验哈希；禁止路径、链接、重复项和压缩炸弹。"""

    if temporary_parent is not None:
        temporary_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="xtb_aggregate_", dir=temporary_parent) as name:
        destination = Path(name).resolve()
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                members = archive.getmembers()
                names = [member.name for member in members]
                if len(names) != len(set(names)):
                    raise XtbArchiveAggregateError("archive contains duplicate members")
                if set(names) != set(member_hashes):
                    raise XtbArchiveAggregateError("archive members differ from manifest")
                total = 0
                for member in members:
                    posix = PurePosixPath(member.name)
                    if (
                        not member.isfile()
                        or member.name not in ALLOWED_ARCHIVE_MEMBERS
                        or posix.is_absolute()
                        or len(posix.parts) != 1
                        or posix.name in {"", ".", ".."}
                    ):
                        raise XtbArchiveAggregateError("unsafe archive member")
                    if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                        raise XtbArchiveAggregateError("archive member exceeds size limit")
                    total += member.size
                    if total > MAX_ARCHIVE_UNPACKED_BYTES:
                        raise XtbArchiveAggregateError("archive exceeds unpacked size limit")
                    source = archive.extractfile(member)
                    if source is None:
                        raise XtbArchiveAggregateError("cannot stream archive member")
                    target = destination / member.name
                    digest = hashlib.sha256()
                    written = 0
                    with target.open("wb") as output:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > member.size or written > MAX_MEMBER_BYTES:
                                raise XtbArchiveAggregateError("archive member expanded beyond declared size")
                            digest.update(chunk)
                            output.write(chunk)
                    if written != member.size or digest.hexdigest() != member_hashes[member.name]:
                        raise XtbArchiveAggregateError(f"archive member SHA-256 mismatch:{member.name}")
        except (OSError, tarfile.TarError) as exc:
            raise XtbArchiveAggregateError(f"invalid archive:{archive_path}") from exc
        yield destination


def _incident_wbo(model: Any, wbo: Mapping[tuple[int, int], float], site_index: int) -> tuple[int, float, float]:
    values: list[float] = []
    for neighbor in model.molecule.GetAtomWithIdx(site_index).GetNeighbors():
        key = tuple(sorted((site_index + 1, neighbor.GetIdx() + 1)))
        if key not in wbo:
            raise XtbArchiveAggregateError(f"missing incident WBO:{key}")
        values.append(float(wbo[key]))
    if not values:
        raise XtbArchiveAggregateError("reactive site has no incident WBO")
    return len(values), math.fsum(values), math.fsum(values) / len(values)


def _task_identity(task: Mapping[str, Any], canonical_smiles: str) -> dict[str, Any]:
    return {
        "xtb_task_index": int(task["xtb_task_index"]),
        "xtb_task_slug": str(task["xtb_task_slug"]),
        "candidate_id": str(task["candidate_id"]),
        "component_role": str(task["component_role"]),
        "conformer_id": str(task["conformer_id"]),
        "source_task_slug": str(task["source_task_slug"]),
        "crest_rank": int(task["crest_rank"]),
        "canonical_smiles": canonical_smiles,
    }


def process_archive_task(
    task: Mapping[str, Any],
    result_root: Path,
    canonical_smiles: str,
    *,
    temporary_parent: Path | None = None,
) -> dict[str, Any]:
    """解析单个任务；返回值只含标量，不保留电荷/WBO列表或坐标。"""

    state, archive_path, member_hashes = _validate_state(result_root, task)
    with _verified_archive_directory(archive_path, member_hashes, temporary_parent) as directory:
        parsed = parse_conformer_directory(
            directory,
            expected_total_charge=float(task["charge"]),
            expected_atom_count=int(task["atom_count"]),
        )
        frames = parse_xyz_conformers(directory / "conformer.xyz")
        if len(frames) != 1:
            raise XtbArchiveAggregateError("conformer archive must contain exactly one XYZ frame")
        frame = frames[0]
        if len(frame.element_symbols) != int(task["atom_count"]):
            raise XtbArchiveAggregateError("conformer atom count mismatch")
        if atom_order_sha256(frame.element_symbols) != str(task["atom_order_sha256"]):
            raise XtbArchiveAggregateError("conformer atom order mismatch")
        model = prepare_reactive_site_model(canonical_smiles, str(task["component_role"]))
        geometry = describe_reactive_sites(model, frame.element_symbols, frame.coordinates_a)

        charges = parsed.pop("partial_charges")
        atomic_alpha = parsed.pop("gfn2_d4_atomic_alpha0_au")
        wbo = parsed.pop("wbo")
        if len(charges) != len(frame.element_symbols):
            raise XtbArchiveAggregateError("partial-charge atom count mismatch")
        if atomic_alpha and len(atomic_alpha) != len(frame.element_symbols):
            raise XtbArchiveAggregateError("atomic-alpha atom count mismatch")
        if not atomic_alpha:
            parsed["run_status"] = "partial_property"
            warnings = list(parsed.get("warning_codes", []))
            warnings.append("missing_atomic_alpha_output")
            parsed["warning_codes"] = sorted(set(warnings))

        charge_values: list[float] = []
        alpha_values: list[float | None] = []
        wbo_sums: list[float] = []
        electronic_sites: dict[str, Any] = {}
        for position, site_index in enumerate(model.site_atom_indices, start=1):
            count, wbo_sum, wbo_mean = _incident_wbo(model, wbo, site_index)
            charge = float(charges[site_index])
            alpha = float(atomic_alpha[site_index]) if atomic_alpha else None
            charge_values.append(charge)
            alpha_values.append(alpha)
            wbo_sums.append(wbo_sum)
            electronic_sites.update(
                {
                    f"site_{position}_charge_e": charge,
                    f"site_{position}_atomic_alpha0_au": alpha,
                    f"site_{position}_incident_wbo_count": count,
                    f"site_{position}_incident_wbo_sum": wbo_sum,
                    f"site_{position}_incident_wbo_mean": wbo_mean,
                }
            )
        warning_codes = parsed.pop("warning_codes", [])
        result = {
            **_task_identity(task, canonical_smiles),
            **parsed,
            **geometry,
            **electronic_sites,
            **_aggregate_pair("site_charge_e", charge_values),
            **_aggregate_pair("site_atomic_alpha0_au", alpha_values),
            **_aggregate_pair("site_incident_wbo_sum", wbo_sums),
            "warning_codes": ";".join(map(str, warning_codes)),
            "failure_type": "",
            "failure_message": "",
            "archive_file": str(state["archive_file"]),
            "archive_sha256": str(state["archive_sha256"]),
            "relative_energy_kcal_mol": None,
            "boltzmann_proxy_weight_298K": None,
        }
    return {column: _scalar(result.get(column)) for column in CONFORMER_COLUMNS}


def _failed_row(task: Mapping[str, Any], canonical_smiles: str, exc: Exception) -> dict[str, Any]:
    row = {
        **_task_identity(task, canonical_smiles),
        "run_status": "failed",
        "warning_codes": "",
        "failure_type": type(exc).__name__,
        "failure_message": str(exc),
    }
    return {column: _scalar(row.get(column)) for column in CONFORMER_COLUMNS}


def _component_summary(
    task_rows: Sequence[Mapping[str, Any]],
    conformers: list[dict[str, Any]],
    canonical_smiles: str,
    temperature_k: float,
) -> dict[str, Any]:
    first = task_rows[0]
    aggregate = aggregate_component_ensemble(
        conformers,
        expected_conformer_count=len(task_rows),
        temperature_k=temperature_k,
        scalar_fields=ENSEMBLE_SCALAR_FIELDS,
    )
    aggregate.pop("conformers")
    hard_failures = sum(row["run_status"] == "failed" for row in conformers)
    aggregate.update(
        source_task_slug=str(first["source_task_slug"]),
        candidate_id=str(first["candidate_id"]),
        component_role=str(first["component_role"]),
        canonical_smiles=canonical_smiles,
        failure_count=hard_failures,
        complete_weighted_release=hard_failures == 0,
    )
    return {key: _scalar(value) for key, value in aggregate.items()}


def iter_component_results(
    xtb_tasks: pd.DataFrame,
    result_root: Path,
    crest_tasks: pd.DataFrame,
    *,
    temperature_k: float = DEFAULT_ENSEMBLE_TEMPERATURE_K,
    temporary_parent: Path | None = None,
) -> Iterator[ComponentResult]:
    """按构件流式处理；内存不随全批次归档或坐标总量增长。"""

    source_map = _validate_inputs(xtb_tasks, crest_tasks)
    ordered = xtb_tasks.sort_values(
        ["source_task_slug", "crest_rank", "xtb_task_index"], kind="stable"
    )
    for source_slug, group in ordered.groupby("source_task_slug", sort=False):
        task_rows = group.to_dict(orient="records")
        canonical_smiles = str(source_map[str(source_slug)]["canonical_smiles"])
        conformers: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for task in task_rows:
            try:
                row = process_archive_task(
                    task,
                    result_root.resolve(),
                    canonical_smiles,
                    temporary_parent=temporary_parent,
                )
            except (
                XtbArchiveAggregateError,
                XtbOutputError,
                ReactiveSiteDescriptorError,
                OSError,
                ValueError,
            ) as exc:
                row = _failed_row(task, canonical_smiles, exc)
                failures.append({column: row.get(column) for column in FAILURE_COLUMNS})
            conformers.append(row)
        component = _component_summary(task_rows, conformers, canonical_smiles, temperature_k)
        yield ComponentResult(conformers, component, failures)


def build_summaries(
    xtb_tasks: pd.DataFrame,
    result_root: Path,
    crest_tasks: pd.DataFrame,
    **kwargs: Any,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """便于测试/交互分析的内存接口；生产CLI使用逐构件CSV写入。"""

    conformers: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for result in iter_component_results(xtb_tasks, result_root, crest_tasks, **kwargs):
        conformers.extend(result.conformers)
        components.append(result.component)
        failures.extend(result.failures)
    return (
        pd.DataFrame(conformers, columns=CONFORMER_COLUMNS),
        pd.DataFrame(components),
        pd.DataFrame(failures, columns=FAILURE_COLUMNS),
    )


def _atomic_csv_writers(paths: Sequence[Path]) -> tuple[list[Path], list[Any]]:
    temporary = [path.with_name(path.name + ".tmp") for path in paths]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    streams = [path.open("w", encoding="utf-8", newline="") for path in temporary]
    return temporary, streams


def write_aggregate_outputs(
    xtb_tasks: pd.DataFrame,
    result_root: Path,
    crest_tasks: pd.DataFrame,
    conformer_output: Path,
    component_output: Path,
    failure_output: Path,
    **kwargs: Any,
) -> dict[str, int]:
    """逐构件写CSV并原子替换，适用于数万构象。"""

    outputs = [conformer_output, component_output, failure_output]
    temporary, streams = _atomic_csv_writers(outputs)
    counts = {"conformers": 0, "components": 0, "failures": 0}
    component_writer = None
    try:
        conformer_writer = csv.DictWriter(streams[0], fieldnames=CONFORMER_COLUMNS)
        failure_writer = csv.DictWriter(streams[2], fieldnames=FAILURE_COLUMNS)
        conformer_writer.writeheader()
        failure_writer.writeheader()
        for result in iter_component_results(xtb_tasks, result_root, crest_tasks, **kwargs):
            conformer_writer.writerows(result.conformers)
            failure_writer.writerows(result.failures)
            if component_writer is None:
                component_writer = csv.DictWriter(streams[1], fieldnames=list(result.component))
                component_writer.writeheader()
            component_writer.writerow(result.component)
            counts["conformers"] += len(result.conformers)
            counts["components"] += 1
            counts["failures"] += len(result.failures)
        if component_writer is None:
            raise XtbArchiveAggregateError("xTB任务表为空")
    except Exception:
        for stream in streams:
            stream.close()
        for path in temporary:
            path.unlink(missing_ok=True)
        raise
    else:
        for stream in streams:
            stream.close()
        for source, target in zip(temporary, outputs):
            source.replace(target)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xTB任务清单", type=Path, required=True)
    parser.add_argument("--结果目录", type=Path, required=True)
    parser.add_argument("--CREST任务清单", type=Path, required=True)
    parser.add_argument("--逐构象输出", type=Path, required=True)
    parser.add_argument("--构件输出", type=Path, required=True)
    parser.add_argument("--失败输出", type=Path, required=True)
    parser.add_argument("--临时目录", type=Path)
    parser.add_argument("--温度", type=float, default=DEFAULT_ENSEMBLE_TEMPERATURE_K)
    args = parser.parse_args()
    counts = write_aggregate_outputs(
        pd.read_csv(args.xTB任务清单),
        args.结果目录.resolve(),
        pd.read_csv(args.CREST任务清单),
        args.逐构象输出,
        args.构件输出,
        args.失败输出,
        temperature_k=args.温度,
        temporary_parent=args.临时目录,
    )
    print(counts)


if __name__ == "__main__":
    main()
