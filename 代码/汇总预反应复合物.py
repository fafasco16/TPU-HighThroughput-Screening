"""汇总预反应复合物逐任务状态和逐配对缔合能代理。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd


_GEOMETRY_CONVERGED = re.compile(
    r"GEOMETRY OPTIMIZATION CONVERGED AFTER\s+\d+\s+ITERATIONS", re.IGNORECASE
)
_NORMAL_TERMINATION = re.compile(r"normal termination of xtb", re.IGNORECASE)
_REQUIRED_OUTPUTS = ("xtbopt.xyz", "xtbout.json", "xtb.out", "wbo")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def _state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"status": "invalid_state_json"}
    return value if isinstance(value, dict) else {"status": "invalid_state_json"}


def _audit_completed_output(state: dict[str, Any], root: Path) -> str:
    relative = state.get("attempt_directory")
    output_hashes = state.get("output_sha256")
    if not isinstance(relative, str) or not isinstance(output_hashes, dict):
        return "completed_state_missing_output_identity"
    candidate = Path(relative)
    if candidate.is_absolute():
        return "completed_state_attempt_path_invalid"
    resolved_root = root.resolve()
    attempt_root = (root / candidate).resolve()
    if resolved_root not in attempt_root.parents:
        return "completed_state_attempt_path_escape"
    if set(output_hashes) != set(_REQUIRED_OUTPUTS):
        return "completed_state_output_set_invalid"
    for name in _REQUIRED_OUTPUTS:
        path = attempt_root / name
        if not path.is_file() or sha256(path) != str(output_hashes[name]):
            return f"completed_state_output_hash_invalid:{name}"
    if (attempt_root / ".sccnotconverged").exists():
        return "completed_state_scc_not_converged"
    log_text = (attempt_root / "xtb.out").read_text(
        encoding="utf-8", errors="replace"
    )
    if not _NORMAL_TERMINATION.search(log_text):
        return "completed_state_missing_normal_termination"
    if not _GEOMETRY_CONVERGED.search(log_text):
        return "completed_state_geometry_not_converged"
    return ""


def collect_task_states(tasks: pd.DataFrame, root: Path) -> pd.DataFrame:
    _required(
        tasks,
        {
            "task_index",
            "task_slug",
            "pair_id",
            "pair_type",
            "diisocyanate_id",
            "oh_component_id",
            "start_index",
            "geometry_status",
            "execution_permission",
        },
        "预反应任务表",
    )
    if not tasks["task_slug"].is_unique:
        raise ValueError("预反应任务task_slug不唯一")
    rows: list[dict[str, Any]] = []
    for task in tasks.sort_values("task_index", kind="stable").to_dict(
        orient="records"
    ):
        state_path = root / "状态" / f"{task['task_slug']}.json"
        state = _state(state_path)
        if state is None:
            run_status = "pending"
            issue = "missing_state"
            state = {}
        elif state.get("status") == "invalid_state_json":
            run_status = "invalid_state_json"
            issue = "invalid_state_json"
        elif any(
            state.get(field) != task[field]
            for field in ("task_index", "task_slug", "pair_id")
        ):
            run_status = "invalid_state_identity"
            issue = "state_identity_mismatch"
        else:
            run_status = str(state.get("status", "invalid_state_status"))
            issue = str(state.get("failure_reason", ""))
            if run_status == "completed":
                required_values = (
                    "association_energy_proxy_kcal_mol",
                    "complex_total_energy_hartree",
                    "final_reactive_distance_a",
                )
                try:
                    values = [float(state[name]) for name in required_values]
                except (KeyError, TypeError, ValueError):
                    run_status = "invalid_completed_state"
                    issue = "completed_state_missing_numeric_output"
                else:
                    if not all(math.isfinite(value) for value in values):
                        run_status = "invalid_completed_state"
                        issue = "completed_state_nonfinite_output"
                if run_status == "completed":
                    output_issue = _audit_completed_output(state, root)
                    if output_issue:
                        run_status = "invalid_completed_state"
                        issue = output_issue
        rows.append(
            {
                **task,
                "run_status": run_status,
                "run_issue": issue,
                "association_energy_proxy_kcal_mol": state.get(
                    "association_energy_proxy_kcal_mol"
                ),
                "complex_total_energy_hartree": state.get(
                    "complex_total_energy_hartree"
                ),
                "final_reactive_distance_a": state.get(
                    "final_reactive_distance_a"
                ),
                "runtime_seconds": state.get("runtime_seconds"),
                "state_file": state_path.relative_to(root).as_posix(),
            }
        )
    return pd.DataFrame(rows)


def aggregate_pair_results(statuses: pd.DataFrame) -> pd.DataFrame:
    _required(
        statuses,
        {
            "pair_id",
            "pair_type",
            "diisocyanate_id",
            "oh_component_id",
            "task_slug",
            "geometry_status",
            "run_status",
            "association_energy_proxy_kcal_mol",
        },
        "预反应逐任务结果",
    )
    rows: list[dict[str, Any]] = []
    for pair_id, group in statuses.groupby("pair_id", sort=True):
        identity = group[
            ["pair_type", "diisocyanate_id", "oh_component_id"]
        ].drop_duplicates()
        if len(identity) != 1:
            raise ValueError(f"{pair_id}配对身份不唯一")
        completed = group.loc[group["run_status"].eq("completed")].copy()
        energies = pd.to_numeric(
            completed["association_energy_proxy_kcal_mol"], errors="coerce"
        )
        valid_energy = energies.notna() & np.isfinite(energies)
        completed = completed.loc[valid_energy].copy()
        energies = energies.loc[valid_energy]
        blocked_expected = group["geometry_status"].ne("ready")
        blocked_observed = group["run_status"].eq("blocked_input_geometry")
        nonconverged = (
            group["run_status"].eq("invalid_completed_state")
            & group["run_issue"].eq("completed_state_geometry_not_converged")
        ) | (
            group["run_status"].eq("failed")
            & group["run_issue"].eq("geometry_optimization_not_converged")
        )
        unexpected = ~group["run_status"].isin(
            ["completed", "blocked_input_geometry"]
        ) & ~nonconverged
        blocked_mismatch = bool((blocked_expected != blocked_observed).any())
        if unexpected.any() or blocked_mismatch:
            pair_status = "incomplete"
            quality_tier = "blocked"
        elif nonconverged.any() and len(completed) >= 2:
            pair_status = "conditional_nonconverged_starts"
            quality_tier = "conditional_reference"
        elif blocked_expected.any():
            pair_status = "complete_with_blocked_starts"
            quality_tier = "conditional_reference"
        else:
            pair_status = "complete"
            quality_tier = "admitted_reference"
        eligible = pair_status in {
            "complete",
            "complete_with_blocked_starts",
            "conditional_nonconverged_starts",
        } and len(completed) >= 2
        if completed.empty:
            best_slug = ""
            best_energy = np.nan
            median_energy = np.nan
            energy_span = np.nan
            best_distance = np.nan
        else:
            best_index = energies.idxmin()
            best_slug = str(statuses.loc[best_index, "task_slug"])
            best_energy = float(energies.loc[best_index])
            median_energy = float(energies.median())
            energy_span = float(energies.max() - energies.min())
            best_distance = float(statuses.loc[best_index, "final_reactive_distance_a"])
        first = identity.iloc[0]
        rows.append(
            {
                "pair_id": pair_id,
                "pair_type": first["pair_type"],
                "diisocyanate_id": first["diisocyanate_id"],
                "oh_component_id": first["oh_component_id"],
                "planned_starts": len(group),
                "ready_starts": int(group["geometry_status"].eq("ready").sum()),
                "blocked_starts": int(blocked_expected.sum()),
                "completed_starts": len(completed),
                "nonconverged_starts": int(nonconverged.sum()),
                "failed_or_pending_starts": int(unexpected.sum()),
                "pair_status": pair_status,
                "pair_quality_tier": quality_tier,
                "pair_release_eligible": bool(eligible),
                "best_task_slug": best_slug,
                "best_association_energy_proxy_kcal_mol": best_energy,
                "median_association_energy_proxy_kcal_mol": median_energy,
                "association_energy_start_span_kcal_mol": energy_span,
                "best_final_reactive_distance_a": best_distance,
                "interpretation_limit": (
                    "constrained_GFN2-xTB_prereaction_association_proxy_not_DFT_barrier"
                ),
                "quality_warning": (
                    "one_or_more_multistart_geometries_not_converged"
                    if nonconverged.any()
                    else "blocked_initial_orientation_retained"
                    if blocked_expected.any()
                    else ""
                ),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["pair_type", "diisocyanate_id", "oh_component_id"], kind="stable"
    ).reset_index(drop=True)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def write_outputs(root: Path) -> dict[str, Any]:
    task_path = root / "预反应复合物任务.csv"
    tasks = pd.read_csv(task_path)
    statuses = collect_task_states(tasks, root)
    pairs = aggregate_pair_results(statuses)
    output_root = root / "聚合"
    task_output = output_root / "逐任务结果.csv"
    pair_output = output_root / "逐配对结果.csv"
    _atomic_text(task_output, statuses.to_csv(index=False, float_format="%.12g"))
    _atomic_text(pair_output, pairs.to_csv(index=False, float_format="%.12g"))
    manifest = {
        "status": "completed" if pairs["pair_release_eligible"].all() else "incomplete",
        "counts": {
            "tasks": len(statuses),
            "pairs": len(pairs),
            "completed_tasks": int(statuses["run_status"].eq("completed").sum()),
            "eligible_pairs": int(pairs["pair_release_eligible"].sum()),
        },
        "status_counts": statuses["run_status"].value_counts().astype(int).to_dict(),
        "pair_status_counts": pairs["pair_status"].value_counts().astype(int).to_dict(),
        "files": {
            task_output.name: {
                "bytes": task_output.stat().st_size,
                "sha256": sha256(task_output),
            },
            pair_output.name: {
                "bytes": pair_output.stat().st_size,
                "sha256": sha256(pair_output),
            },
        },
    }
    _atomic_text(
        output_root / "聚合发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--根目录", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = write_outputs(args.根目录.resolve())
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
