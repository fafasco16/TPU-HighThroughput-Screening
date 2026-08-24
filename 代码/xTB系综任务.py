"""把通过审计的 CREST 多构象文件拆分为可独立运行的 xTB 单点任务。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


METHOD = "GFN2-xTB"
ENVIRONMENT_MODEL = "gas_phase"
ELECTRONIC_TEMPERATURE_K = 300.0
ENSEMBLE_TEMPERATURE_K = 298.15
EXPECTED_XTB_VERSION = "6.7.1"
_ELEMENT = re.compile(r"^[A-Z][a-z]?$")


class XtbTaskError(ValueError):
    """CREST 输入或 xTB 任务发布不能通过门禁。"""


@dataclass(frozen=True)
class XyzFrame:
    rank: int
    atom_count: int
    comment: str
    energy_hartree: float
    elements: tuple[str, ...]
    text: str


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _energy_from_comment(comment: str, rank: int) -> float:
    fields = comment.split()
    if not fields:
        raise XtbTaskError(f"frame {rank}: missing CREST energy")
    try:
        value = float(fields[0].replace("D", "E").replace("d", "e"))
    except ValueError as exc:
        raise XtbTaskError(f"frame {rank}: invalid CREST energy") from exc
    if not math.isfinite(value):
        raise XtbTaskError(f"frame {rank}: non-finite CREST energy")
    return value


def split_crest_xyz(path: Path) -> list[XyzFrame]:
    """严格拆分 CREST XYZ；保留注释和原子行，同时验证全系综原子序。"""

    if not path.is_file():
        raise XtbTaskError(f"missing CREST ensemble: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise XtbTaskError(f"cannot read CREST ensemble: {path}") from exc

    frames: list[XyzFrame] = []
    expected_elements: tuple[str, ...] | None = None
    position = 0
    while position < len(lines):
        while position < len(lines) and not lines[position].strip():
            position += 1
        if position >= len(lines):
            break
        rank = len(frames) + 1
        try:
            atom_count = int(lines[position].strip())
        except ValueError as exc:
            raise XtbTaskError(f"frame {rank}: invalid atom count") from exc
        if atom_count <= 0:
            raise XtbTaskError(f"frame {rank}: atom count must be positive")
        end = position + atom_count + 2
        if end > len(lines):
            raise XtbTaskError(f"frame {rank}: truncated XYZ frame")
        comment = lines[position + 1]
        energy = _energy_from_comment(comment, rank)
        atom_lines = lines[position + 2 : end]
        elements: list[str] = []
        for atom_line in atom_lines:
            fields = atom_line.split()
            if len(fields) < 4 or not _ELEMENT.fullmatch(fields[0]):
                raise XtbTaskError(f"frame {rank}: invalid atom row")
            try:
                coordinates = tuple(float(value) for value in fields[1:4])
            except ValueError as exc:
                raise XtbTaskError(f"frame {rank}: invalid coordinate") from exc
            if not all(math.isfinite(value) for value in coordinates):
                raise XtbTaskError(f"frame {rank}: non-finite coordinate")
            elements.append(fields[0])
        element_tuple = tuple(elements)
        if expected_elements is None:
            expected_elements = element_tuple
        elif element_tuple != expected_elements:
            raise XtbTaskError(f"frame {rank}: atom order mismatch")
        frame_text = "\n".join([str(atom_count), comment, *atom_lines]) + "\n"
        frames.append(
            XyzFrame(
                rank=rank,
                atom_count=atom_count,
                comment=comment,
                energy_hartree=energy,
                elements=element_tuple,
                text=frame_text,
            )
        )
        position = end
    if not frames:
        raise XtbTaskError("CREST ensemble contains no XYZ frames")
    return frames


def atom_order_sha256(elements: tuple[str, ...]) -> str:
    return _sha256_bytes("\0".join(elements).encode("ascii"))


def _read_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise XtbTaskError(f"invalid CREST state: {path}") from exc
    if not isinstance(value, dict):
        raise XtbTaskError(f"CREST state is not an object: {path}")
    return value


def _safe_relative_output(task_root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise XtbTaskError("completed CREST state is missing conformer_output")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise XtbTaskError("CREST conformer_output must be relative")
    root = task_root.resolve()
    output = (task_root / candidate).resolve()
    if output != root and root not in output.parents:
        raise XtbTaskError("CREST conformer_output escapes task directory")
    return output


def _validate_release_identity(version: str, binary_sha256: str) -> None:
    if version != EXPECTED_XTB_VERSION:
        raise XtbTaskError(
            f"xTB version must be {EXPECTED_XTB_VERSION}, got {version!r}"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", binary_sha256):
        raise XtbTaskError("xtb_binary_sha256 must be a lowercase SHA-256")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def build_conformer_tasks(
    crest_tasks: pd.DataFrame,
    crest_result_root: Path,
    output_root: Path,
    *,
    descriptor_release_id: str,
    xtb_version: str,
    xtb_binary_sha256: str,
) -> pd.DataFrame:
    """物化全部已完成 CREST 构件的单帧输入并返回确定性任务清单。

    尚未完成及 ``blocked_input_geometry`` 的构件不生成伪任务。任何声称
    ``completed`` 的构件若身份、输入哈希、输出哈希或 XYZ 不闭合则整体失败。
    """

    _validate_release_identity(xtb_version, xtb_binary_sha256)
    required = {
        "task_index",
        "task_slug",
        "candidate_id",
        "component_role",
        "initial_xyz_sha256",
        "charge",
        "uhf",
    }
    missing = required.difference(crest_tasks.columns)
    if missing:
        raise XtbTaskError(f"CREST task table missing fields: {sorted(missing)}")
    if not crest_tasks["task_slug"].is_unique:
        raise XtbTaskError("CREST task_slug is not unique")
    if not descriptor_release_id.strip():
        raise XtbTaskError("descriptor_release_id must not be empty")

    rows: list[dict[str, Any]] = []
    for source in crest_tasks.sort_values("task_index", kind="stable").to_dict(
        orient="records"
    ):
        source_root = crest_result_root / str(source["task_slug"])
        state = _read_state(source_root / "运行状态.json")
        if state is None or state.get("status") != "completed":
            continue
        for field in ("task_slug", "candidate_id", "component_role"):
            if str(state.get(field, "")) != str(source[field]):
                raise XtbTaskError(f"CREST state identity mismatch: {field}")
        expected_input_hash = str(source["initial_xyz_sha256"])
        if state.get("input_sha256") != expected_input_hash:
            raise XtbTaskError("CREST input_sha256 mismatch")
        ensemble_path = _safe_relative_output(source_root, state.get("conformer_output"))
        if not ensemble_path.is_file():
            raise XtbTaskError("completed CREST state points to missing ensemble")
        ensemble_hash = sha256(ensemble_path)
        if state.get("output_sha256") != ensemble_hash:
            raise XtbTaskError("CREST ensemble SHA-256 mismatch")

        frames = split_crest_xyz(ensemble_path)
        order_hash = atom_order_sha256(frames[0].elements)
        for frame in frames:
            conformer_hash = _sha256_bytes(frame.text.encode("utf-8"))
            identity_payload = "\0".join(
                (
                    str(source["candidate_id"]),
                    ensemble_hash,
                    str(frame.rank),
                    conformer_hash,
                )
            ).encode("utf-8")
            conformer_id = f"cf_{_sha256_bytes(identity_payload)[:20]}"
            xtb_task_index = len(rows)
            xtb_task_slug = f"{xtb_task_index:06d}_{conformer_id}"
            relative_input = Path("输入构象") / f"{xtb_task_slug}.xyz"
            input_path = output_root / relative_input
            _write_atomic(input_path, frame.text)
            input_hash = sha256(input_path)
            if input_hash != conformer_hash:
                raise XtbTaskError("materialized conformer hash is not deterministic")
            rows.append(
                {
                    "descriptor_release_id": descriptor_release_id,
                    "xtb_task_index": xtb_task_index,
                    "xtb_task_slug": xtb_task_slug,
                    "source_task_index": int(source["task_index"]),
                    "source_task_slug": str(source["task_slug"]),
                    "candidate_id": str(source["candidate_id"]),
                    "component_role": str(source["component_role"]),
                    "conformer_id": conformer_id,
                    "crest_rank": int(frame.rank),
                    "crest_energy_hartree": frame.energy_hartree,
                    "crest_ensemble_sha256": ensemble_hash,
                    "conformer_xyz_file": relative_input.as_posix(),
                    "conformer_xyz_sha256": input_hash,
                    "atom_count": int(frame.atom_count),
                    "atom_order_sha256": order_hash,
                    "charge": int(source["charge"]),
                    "uhf": int(source["uhf"]),
                    "xtb_version": xtb_version,
                    "xtb_binary_sha256": xtb_binary_sha256,
                    "method": METHOD,
                    "environment_model": ENVIRONMENT_MODEL,
                    "electronic_temperature_k": ELECTRONIC_TEMPERATURE_K,
                    "ensemble_temperature_k": ENSEMBLE_TEMPERATURE_K,
                    "selection_status": "selected_all_crest_conformers",
                    "result_storage_policy": "sharded_tar_gz_v1",
                    "failed_workdir_policy": "retain_for_diagnosis",
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=[
                "descriptor_release_id",
                "xtb_task_index",
                "xtb_task_slug",
                "source_task_index",
                "source_task_slug",
                "candidate_id",
                "component_role",
                "conformer_id",
                "crest_rank",
                "crest_energy_hartree",
                "crest_ensemble_sha256",
                "conformer_xyz_file",
                "conformer_xyz_sha256",
                "atom_count",
                "atom_order_sha256",
                "charge",
                "uhf",
                "xtb_version",
                "xtb_binary_sha256",
                "method",
                "environment_model",
                "electronic_temperature_k",
                "ensemble_temperature_k",
                "selection_status",
                "result_storage_policy",
                "failed_workdir_policy",
            ]
        )
    result = pd.DataFrame(rows)
    if not result["conformer_id"].is_unique or not result["xtb_task_slug"].is_unique:
        raise XtbTaskError("generated conformer identity collision")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--CREST任务清单", type=Path, required=True)
    parser.add_argument("--CREST结果目录", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--发布ID", required=True)
    parser.add_argument("--xTB版本", default=EXPECTED_XTB_VERSION)
    parser.add_argument("--xTB二进制SHA256", required=True)
    args = parser.parse_args()
    tasks = build_conformer_tasks(
        pd.read_csv(args.CREST任务清单),
        args.CREST结果目录.resolve(),
        args.输出目录.resolve(),
        descriptor_release_id=args.发布ID,
        xtb_version=args.xTB版本,
        xtb_binary_sha256=args.xTB二进制SHA256,
    )
    output = args.输出目录.resolve() / "xTB构象任务清单.csv"
    _write_atomic(output, tasks.to_csv(index=False, float_format="%.12g"))
    print({"xtb_conformer_tasks": len(tasks), "components": tasks["candidate_id"].nunique()})


if __name__ == "__main__":
    main()
