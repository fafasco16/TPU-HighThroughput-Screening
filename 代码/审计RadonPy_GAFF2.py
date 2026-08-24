"""审计RadonPy GAFF2对现实TPU低聚链图的参数覆盖与替代参数使用。"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import time
from pathlib import Path
from typing import Any, Sequence

import pandas as pd


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def radonpy_available() -> bool:
    return importlib.util.find_spec("radonpy") is not None


def summarize_alternate_messages(text: str) -> dict[str, Any]:
    lines = sorted(
        line.strip()
        for line in text.splitlines()
        if "Using alternate" in line
    )
    unique = sorted(set(lines))
    return {
        "alternate_parameter_line_count": len(lines),
        "alternate_parameter_unique_count": len(unique),
        "alternate_parameter_unique_sha256": hashlib.sha256(
            "\n".join(unique).encode("utf-8")
        ).hexdigest(),
        "alternate_parameter_unique_messages": " | ".join(unique),
    }


def audit_graph_table(graphs: pd.DataFrame | None) -> pd.DataFrame:
    if not radonpy_available():
        raise RuntimeError("当前Python环境未安装RadonPy")
    if graphs is None:
        raise ValueError("低聚链化学图不能为空")
    required = {
        "formulation_id",
        "canonical_smiles",
        "atom_count",
        "chemical_graph_status",
        "performance_claim_status",
    }
    missing = sorted(required.difference(graphs.columns))
    if missing:
        raise ValueError(f"低聚链化学图缺少字段: {missing}")
    if graphs.empty or not graphs["formulation_id"].is_unique:
        raise ValueError("低聚链化学图formulation_id必须非空唯一")
    if not graphs["chemical_graph_status"].astype(str).eq("completed").all():
        raise ValueError("只有completed化学图可审计GAFF2")
    if not graphs["performance_claim_status"].astype(str).eq(
        "no_performance_claim"
    ).all():
        raise ValueError("GAFF2审计输入不得包含性能宣称")

    from rdkit import Chem
    from radonpy.ff.gaff2 import GAFF2

    rows: list[dict[str, Any]] = []
    for source in graphs.sort_values("formulation_id", kind="stable").to_dict(
        orient="records"
    ):
        formulation_id = str(source["formulation_id"])
        molecule = Chem.MolFromSmiles(str(source["canonical_smiles"]))
        if molecule is None:
            raise ValueError(f"{formulation_id}规范SMILES无法解析")
        molecule = Chem.AddHs(molecule)
        if molecule.GetNumAtoms() != int(source["atom_count"]):
            raise ValueError(f"{formulation_id}原子数与化学图不一致")
        output = io.StringIO()
        started = time.monotonic()
        with contextlib.redirect_stdout(output):
            assigned = bool(GAFF2().ff_assign(molecule, charge="gasteiger"))
        elapsed = round(time.monotonic() - started, 3)
        alternate = summarize_alternate_messages(output.getvalue())
        typed_atoms = sum(atom.HasProp("ff_type") for atom in molecule.GetAtoms())
        charged_atoms = sum(
            atom.HasProp("AtomicCharge") for atom in molecule.GetAtoms()
        )
        charges = [
            atom.GetDoubleProp("AtomicCharge")
            for atom in molecule.GetAtoms()
            if atom.HasProp("AtomicCharge")
        ]
        charge_sum = float(math.fsum(charges)) if charges else math.nan
        if not assigned:
            status = "assignment_failed"
        elif typed_atoms != molecule.GetNumAtoms() or charged_atoms != molecule.GetNumAtoms():
            status = "assignment_incomplete"
        elif alternate["alternate_parameter_line_count"]:
            status = "assigned_with_alternate_parameters"
        else:
            status = "assigned_without_alternate_parameters"
        rows.append(
            {
                "formulation_id": formulation_id,
                "atom_count": molecule.GetNumAtoms(),
                "assignment_status": status,
                "assignment_success": assigned,
                "typed_atom_count": typed_atoms,
                "charged_atom_count": charged_atoms,
                "charge_method": "gasteiger",
                "atomic_charge_sum_e": charge_sum,
                "angle_count": len(getattr(molecule, "angles", [])),
                "dihedral_count": len(getattr(molecule, "dihedrals", [])),
                "improper_count": len(getattr(molecule, "impropers", [])),
                "assignment_seconds": elapsed,
                **alternate,
                "forcefield_name": "GAFF2",
                "radonpy_version": "1.0b2",
                "radonpy_commit": "5d14893515376a4518e9f1373a1ebc4bb756db14",
                "production_md_permission": (
                    "blocked_urethane_alternate_parameter_and_charge_validation"
                ),
                "performance_claim_status": "no_performance_claim",
            }
        )
    return pd.DataFrame(rows).sort_values("formulation_id").reset_index(drop=True)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def write_release(
    graph_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    if not graph_path.is_file():
        raise ValueError(f"低聚链化学图不存在: {graph_path}")
    table = audit_graph_table(pd.read_csv(graph_path))
    table_path = output_root / "GAFF2参数覆盖审计.csv"
    _atomic_text(table_path, table.to_csv(index=False, float_format="%.12g"))
    report_path = output_root / "GAFF2参数覆盖说明.md"
    _atomic_text(
        report_path,
        "\n".join(
            [
                "# RadonPy GAFF2参数覆盖说明",
                "",
                f"- 审计低聚链：{len(table)}",
                f"- 分配成功：{int(table['assignment_success'].sum())}",
                f"- 使用替代参数：{int(table['assignment_status'].eq('assigned_with_alternate_parameters').sum())}",
                "",
                "GAFF2分配成功只表示拓扑参数可生成；RadonPy对氨基甲酸酯相关ns/cg类型使用了替代键、角、二面角和improper参数。",
                "Gasteiger电荷只用于环境烟雾，不是生产电荷。正式MD仍需氨基甲酸酯参数验证及RESP或经论证的等价电荷协议。",
                "本审计不启动LAMMPS动力学，也不产生密度、Tg、力学或相分离性能。",
                "",
            ]
        ),
    )
    manifest = {
        "release_id": release_id,
        "status": "assignment_audited_production_md_blocked",
        "counts": {
            "graphs": len(table),
            "assignment_success": int(table["assignment_success"].sum()),
            "with_alternate_parameters": int(
                table["assignment_status"]
                .eq("assigned_with_alternate_parameters")
                .sum()
            ),
        },
        "input": {
            "path": str(graph_path),
            "bytes": graph_path.stat().st_size,
            "sha256": sha256(graph_path),
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in (table_path, report_path)
        },
        "production_md_permission": (
            "blocked_urethane_alternate_parameter_and_charge_validation"
        ),
    }
    _atomic_text(
        output_root / "GAFF2参数覆盖发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--化学图", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-gaff2-audit-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.化学图,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
