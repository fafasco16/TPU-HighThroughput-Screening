"""解析CREST多帧XYZ，并在严格状态门后生成构件级系综指标。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

HARTREE_TO_KCAL_MOL = 627.5094740631
GAS_CONSTANT_J_MOL_K = 8.31446261815324
GAS_CONSTANT_KCAL_MOL_K = GAS_CONSTANT_J_MOL_K / 4184.0
DEFAULT_TEMPERATURE_K = 298.15
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?"
_ENERGY_PATTERNS = (
    re.compile(rf"^\s*({_NUMBER})(?:\s|$)"),
    re.compile(rf"(?:energy|etot|e)\s*[:=]\s*({_NUMBER})", re.I),
)


class CrestEnsembleError(ValueError):
    """系综不能通过科学数据门时抛出。"""


@dataclass(frozen=True)
class ConformerFrame:
    frame_index: int
    atom_count: int
    energy_hartree: float
    comment: str


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _comment_energy(comment: str, frame_index: int) -> float:
    for pattern in _ENERGY_PATTERNS:
        match = pattern.search(comment)
        if match:
            value = float(match.group(1).replace("D", "E").replace("d", "e"))
            if math.isfinite(value):
                return value
    raise CrestEnsembleError(f"frame {frame_index}: missing or non-finite energy")


def parse_crest_xyz(path: Path) -> list[ConformerFrame]:
    """解析多帧XYZ；每帧必须有能量、有限坐标及相同原子数。"""

    if not path.is_file():
        raise CrestEnsembleError(f"missing conformer file: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CrestEnsembleError(f"cannot read conformer file: {path}") from exc
    position, frames, expected = 0, [], None
    while position < len(lines):
        while position < len(lines) and not lines[position].strip():
            position += 1
        if position >= len(lines):
            break
        frame_index = len(frames) + 1
        try:
            atom_count = int(lines[position].strip())
        except ValueError as exc:
            raise CrestEnsembleError(f"frame {frame_index}: invalid atom count") from exc
        if atom_count <= 0:
            raise CrestEnsembleError(f"frame {frame_index}: atom count must be positive")
        if expected is None:
            expected = atom_count
        elif atom_count != expected:
            raise CrestEnsembleError(f"frame {frame_index}: atom count {atom_count} != {expected}")
        if position + atom_count + 1 >= len(lines):
            raise CrestEnsembleError(f"frame {frame_index}: truncated XYZ frame")
        comment = lines[position + 1]
        energy = _comment_energy(comment, frame_index)
        for line_index in range(position + 2, position + atom_count + 2):
            fields = lines[line_index].split()
            if len(fields) < 4:
                raise CrestEnsembleError(f"frame {frame_index}: invalid atom row")
            try:
                coordinates = [float(value) for value in fields[1:4]]
            except ValueError as exc:
                raise CrestEnsembleError(f"frame {frame_index}: invalid coordinate") from exc
            if not all(math.isfinite(value) for value in coordinates):
                raise CrestEnsembleError(f"frame {frame_index}: non-finite coordinate")
        frames.append(ConformerFrame(frame_index, atom_count, energy, comment))
        position += atom_count + 2
    if not frames:
        raise CrestEnsembleError("conformer file contains no XYZ frames")
    return frames


def boltzmann_statistics(
    energies_hartree: Iterable[float], temperature_k: float = DEFAULT_TEMPERATURE_K
) -> dict[str, Any]:
    """从Hartree绝对能量计算数值稳定的Boltzmann系综统计。"""

    energies = [float(value) for value in energies_hartree]
    if not energies or not all(math.isfinite(value) for value in energies):
        raise CrestEnsembleError("energies must be a non-empty finite sequence")
    if not math.isfinite(temperature_k) or temperature_k <= 0:
        raise CrestEnsembleError("temperature must be finite and positive")
    minimum = min(energies)
    relative = [(value - minimum) * HARTREE_TO_KCAL_MOL for value in energies]
    rt = GAS_CONSTANT_KCAL_MOL_K * temperature_k
    factors = [math.exp(-value / rt) for value in relative]
    partition = math.fsum(factors)
    if not math.isfinite(partition) or partition <= 0:
        raise CrestEnsembleError("invalid Boltzmann partition function")
    weights = [value / partition for value in factors]
    weights = [value / math.fsum(weights) for value in weights]
    shannon = -math.fsum(weight * math.log(weight) for weight in weights if weight > 0)
    dominant = max(range(len(weights)), key=weights.__getitem__)
    return {
        "temperature_K": float(temperature_k),
        "minimum_energy_hartree": minimum,
        "relative_energies_kcal_mol": relative,
        "boltzmann_weights": weights,
        "boltzmann_weight_sum": math.fsum(weights),
        "energy_span_kcal_mol": max(relative),
        "effective_conformer_count": math.exp(shannon),
        "conformational_entropy_J_mol_K": GAS_CONSTANT_J_MOL_K * shannon,
        "dominant_conformer_index": dominant + 1,
        "dominant_conformer_weight": weights[dominant],
        "conformer_count_1kcal": sum(value <= 1.0 + 1e-12 for value in relative),
        "conformer_count_3kcal": sum(value <= 3.0 + 1e-12 for value in relative),
        "conformer_count_6kcal": sum(value <= 6.0 + 1e-12 for value in relative),
    }


def analyze_crest_xyz(path: Path, temperature_k: float = DEFAULT_TEMPERATURE_K) -> dict[str, Any]:
    frames = parse_crest_xyz(path)
    result = boltzmann_statistics((frame.energy_hartree for frame in frames), temperature_k)
    result.update(conformer_count=len(frames), atom_count=frames[0].atom_count, energy_unit="hartree")
    result.pop("relative_energies_kcal_mol")
    result.pop("boltzmann_weights")
    return result


_METRICS = (
    "temperature_K", "conformer_count", "atom_count", "energy_unit",
    "minimum_energy_hartree", "energy_span_kcal_mol", "effective_conformer_count",
    "conformational_entropy_J_mol_K", "dominant_conformer_index",
    "dominant_conformer_weight", "boltzmann_weight_sum", "conformer_count_1kcal",
    "conformer_count_3kcal", "conformer_count_6kcal",
)


def _read_state(path: Path) -> tuple[str, dict[str, Any], str]:
    if not path.is_file():
        return "pending", {}, "missing_state_file"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "invalid_state", {}, "invalid_state_json"
    if not isinstance(value, dict):
        return "invalid_state", {}, "state_is_not_an_object"
    status = value.get("status")
    if not isinstance(status, str) or not status:
        return "invalid_state", value, "missing_state_status"
    return status, value, ""


def _safe_output_path(task_root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise CrestEnsembleError("completed state is missing conformer_output")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise CrestEnsembleError("conformer_output must be relative")
    root, output = task_root.resolve(), (task_root / candidate).resolve()
    if output != root and root not in output.parents:
        raise CrestEnsembleError("conformer_output escapes the task directory")
    return output


def analyze_task(
    task: Mapping[str, Any], result_root: Path, temperature_k: float = DEFAULT_TEMPERATURE_K
) -> dict[str, Any]:
    for field in ("task_index", "task_slug", "candidate_id", "component_role"):
        if field not in task:
            raise CrestEnsembleError(f"task table is missing required field: {field}")
    task_root = result_root / str(task["task_slug"])
    run_status, state, issue = _read_state(task_root / "运行状态.json")
    row = {
        "task_index": int(task["task_index"]), "task_slug": str(task["task_slug"]),
        "candidate_id": str(task["candidate_id"]), "component_role": str(task["component_role"]),
        "run_status": run_status, "analysis_status": "not_analyzed",
        "analysis_issue": issue, "conformer_file": "", "output_sha256": "",
        **{column: None for column in _METRICS},
    }
    if run_status == "blocked_input_geometry":
        row.update(analysis_status="blocked_input_geometry", analysis_issue=str(state.get("failure_reason", "blocked input geometry")))
        return row
    if run_status != "completed":
        row["analysis_issue"] = issue or f"run_status_not_completed:{run_status}"
        return row
    try:
        for field in ("task_slug", "candidate_id", "component_role"):
            if field in state and str(state[field]) != str(task[field]):
                raise CrestEnsembleError(f"state identity mismatch: {field}")
        expected_input = task.get("initial_xyz_sha256")
        if expected_input and state.get("input_sha256") != str(expected_input):
            raise CrestEnsembleError("input_sha256 mismatch")
        output = _safe_output_path(task_root, state.get("conformer_output"))
        if not output.is_file():
            raise CrestEnsembleError("completed state points to a missing conformer file")
        actual_hash = sha256(output)
        if not state.get("output_sha256"):
            raise CrestEnsembleError("completed state is missing output_sha256")
        if state["output_sha256"] != actual_hash:
            raise CrestEnsembleError("output_sha256 mismatch")
        metrics = analyze_crest_xyz(output, temperature_k)
    except (CrestEnsembleError, OSError, TypeError, ValueError) as exc:
        row.update(analysis_status="rejected", analysis_issue=str(exc))
        return row
    row.update(metrics)
    row.update(analysis_status="analyzed", analysis_issue="", conformer_file=str(output), output_sha256=actual_hash)
    return row


def build_component_summary(
    tasks: pd.DataFrame, result_root: Path, temperature_k: float = DEFAULT_TEMPERATURE_K
) -> pd.DataFrame:
    required = {"task_index", "task_slug", "candidate_id", "component_role", "initial_xyz_sha256"}
    missing = required.difference(tasks.columns)
    if missing:
        raise CrestEnsembleError(f"task table is missing required fields: {sorted(missing)}")
    if not tasks["task_slug"].is_unique:
        raise CrestEnsembleError("task_slug is not unique")
    rows = [analyze_task(task, result_root, temperature_k) for task in tasks.to_dict(orient="records")]
    return pd.DataFrame(rows).sort_values("task_index", kind="stable").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--任务清单", type=Path, required=True)
    parser.add_argument("--结果目录", type=Path, required=True)
    parser.add_argument("--输出", type=Path, required=True)
    parser.add_argument("--温度", type=float, default=DEFAULT_TEMPERATURE_K)
    args = parser.parse_args()
    summary = build_component_summary(pd.read_csv(args.任务清单), args.结果目录.resolve(), args.温度)
    args.输出.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.输出, index=False, float_format="%.12g")
    print(summary["analysis_status"].value_counts(dropna=False).to_dict())


if __name__ == "__main__":
    main()
