"""审计 RSC 2026 异山梨醇动态聚氨酯多尺度计算仓库。"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import statistics
import stat
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HERE = (
    PROJECT_ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第七批计算_异山梨醇动态聚氨酯多尺度力学"
)
ARCHIVE = HERE / "poly-mech-props_d2d3229.zip"
COMMIT = "d2d322942135c7a1b93fcbb03fe7c018416cb6b8"
ROOT = f"poly-mech-props-{COMMIT}"
ARTICLE_DOI = "10.1039/D5PY01221J"
REPOSITORY_URL = "https://github.com/pic-ai-robotic-chemistry/poly-mech-props"
ARCHIVE_URL = f"{REPOSITORY_URL}/archive/{COMMIT}.zip"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    root = PROJECT_ROOT.resolve(strict=True)
    target = path.resolve(strict=False)
    target.relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and (
        target.is_symlink() or not stat.S_ISREG(target.lstat().st_mode)
    ):
        raise ValueError(f"拒绝覆盖非普通文件: {target}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            descriptor = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def write_tsv(path: Path, rows: list[dict], columns: list[str]) -> None:
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=columns,
        delimiter="\t",
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(path, "\ufeff" + buffer.getvalue())


def system_parts(stereo: str, design: str) -> tuple[str, str, int, str]:
    match = re.fullmatch(r"([A-Z]+)([1-6])", design)
    if not match:
        raise ValueError(design)
    dynamic_motif, hb_number = match.group(1), int(match.group(2))
    ring = "isoidide" if stereo == "IIPU" else "isomannide"
    system_id = f"rsc2026_{stereo.lower()}_{design.lower()}"
    return dynamic_motif, ring, hb_number, system_id


def read_two_column_csv(zf: zipfile.ZipFile, name: str) -> list[tuple[float, float]]:
    text = zf.read(name).decode("utf-8-sig")
    rows: list[tuple[float, float]] = []
    for index, line in enumerate(text.splitlines()):
        if index == 0 or not line.strip():
            continue
        left, right = line.split(",", 1)
        rows.append((float(left), float(right)))
    return rows


def read_bulk_curve(zf: zipfile.ZipFile, name: str, system: str, axis: str) -> dict:
    initial_lengths = {
        "NO5M": {"X": 43.4470, "Y": 37.1978, "Z": 49.5970},
        "SS4I": {"X": 42.7328, "Y": 37.8402, "Z": 50.4537},
    }
    axis_column = {"X": 2, "Y": 3, "Z": 4}[axis]
    upper_strain = 350.0 if system == "NO5M" else 340.0
    raw: list[tuple[float, float]] = []
    for line in zf.read(name).decode("utf-8-sig").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        values = [float(item) for item in line.split()]
        strain_percent = (values[axis_column] / initial_lengths[system][axis] - 1.0) * 100.0
        # This is the exact conversion used in the authors' Regression.ipynb.
        stress_mpa = -values[-1] / 10.0
        if 0.0 <= strain_percent <= upper_strain:
            raw.append((strain_percent, stress_mpa))
    blocks = [raw[index : index + 20] for index in range(0, len(raw) - 19, 20)]
    averaged = [
        (statistics.fmean(point[0] for point in block), statistics.fmean(point[1] for point in block))
        for block in blocks
    ]
    peak_strain, peak_stress = max(averaged, key=lambda item: item[1])
    return {
        "point_count": len(raw),
        "block20_count": len(averaged),
        "strain_min_percent": min(point[0] for point in raw),
        "strain_max_percent": max(point[0] for point in raw),
        "raw_peak_stress_mpa": max(point[1] for point in raw),
        "block20_peak_stress_mpa": peak_stress,
        "strain_at_block20_peak_percent": peak_strain,
    }


def main() -> None:
    archive_sha = sha256(ARCHIVE)
    observations: list[dict] = []
    inputs: list[dict] = []
    systems: dict[str, dict] = {}

    with zipfile.ZipFile(ARCHIVE) as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        names = [info.filename for info in infos]
        if not all(name.startswith(ROOT + "/") for name in names):
            raise RuntimeError("归档根目录与固定提交不一致")

        internal_license_files = [
            name for name in names if PurePosixPath(name).name.lower() in {"license", "license.md", "license.txt", "copying"}
        ]
        extension_counts = Counter((PurePosixPath(name).suffix.lower() or "[none]") for name in names)
        top_counts = Counter(PurePosixPath(name).parts[1] for name in names)

        monomer_pattern = re.compile(rf"^{re.escape(ROOT)}/monomer/(IIPU|IMPU)/([A-Z]+[1-6])_([0-9.]+)\.xyz$")
        monomer_forces: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
        for name in names:
            match = monomer_pattern.match(name)
            if match:
                monomer_forces[(match.group(1), match.group(2))].append((float(match.group(3)), name))

        for (stereo, design), force_files in sorted(monomer_forces.items()):
            dynamic_motif, ring, hb_number, system_id = system_parts(stereo, design)
            values = sorted({force for force, _ in force_files if force > 0})
            relaxed = next((name for force, name in force_files if force == 0), "")
            systems[system_id] = {
                "system_id": system_id,
                "polyurethane_family": stereo,
                "stereochemical_ring": ring,
                "dynamic_bond_motif": dynamic_motif,
                "hydrogen_bonding_motif": hb_number,
                "chemistry_class": "nonsegmented_alternating_isohexide_polyurethane",
                "structure_representation": "3D_XYZ_no_canonical_SMILES",
                "relaxed_xyz": relaxed,
                "monomer_geometry_file_count": len(force_files),
                "single_chain_scission_available": False,
                "dimer_force_curve_available": False,
                "dimer_hbond_curve_available": False,
                "classical_md_hbond_available": False,
                "bulk_reaxff_curve_axes": "",
            }
            # The repository retains the relaxed geometry and near-threshold geometries.
            # Per the paper, Fmax is the last intact geometry immediately before the
            # highest-force fragmented geometry, hence the second-highest nonzero file.
            if len(values) >= 2:
                scission_force = values[-2]
                systems[system_id]["single_chain_scission_available"] = True
                observations.append(
                    {
                        "observation_id": f"{system_id}_single_chain_scission",
                        "system_id": system_id,
                        "method_family": "DFT_EFEI",
                        "property_name": "single_chain_scission_force",
                        "value": f"{scission_force:.3f}",
                        "unit": "nN",
                        "point_count": 1,
                        "source_location": ";".join(name for force, name in sorted(force_files) if force > 0),
                        "target_origin": "dft",
                        "decision": "admitted_gold_c_science_rights_pending",
                        "independence_note": "one chemical design; nearby force geometries are not independent samples",
                        "quality_note": "paper-defined last-intact EFEI geometry; force resolution 0.1 nN",
                    }
                )

        dimer_result_pattern = re.compile(rf"^{re.escape(ROOT)}/dimer/DFT/(IIPU|IMPU)/([A-Z]+[1-6])_Result\.txt$")
        dimer_hbond_pattern = re.compile(rf"^{re.escape(ROOT)}/dimer/DFT/(IIPU|IMPU)/([A-Z]+[1-6])_HBonds\.txt$")
        for name in names:
            match = dimer_result_pattern.match(name)
            if match:
                _, _, _, system_id = system_parts(match.group(1), match.group(2))
                curve = read_two_column_csv(zf, name)
                if not curve:
                    continue
                systems[system_id]["dimer_force_curve_available"] = True
                observations.append(
                    {
                        "observation_id": f"{system_id}_dimer_force_extension",
                        "system_id": system_id,
                        "method_family": "GFN2-xTB_EFEI",
                        "property_name": "double_chain_force_extension_curve",
                        "value": "",
                        "unit": "nN;angstrom",
                        "point_count": len(curve),
                        "source_location": name,
                        "target_origin": "computational",
                        "decision": "admitted_gold_c_science_rights_pending",
                        "independence_note": "one curve per chemical design; curve points are correlated",
                        "quality_note": f"force range {curve[0][0]:.3f}-{curve[-1][0]:.3f} nN; preserve full curve rather than infer shear point again",
                    }
                )
            match = dimer_hbond_pattern.match(name)
            if match:
                _, _, _, system_id = system_parts(match.group(1), match.group(2))
                curve = read_two_column_csv(zf, name)
                if not curve:
                    continue
                systems[system_id]["dimer_hbond_curve_available"] = True
                observations.append(
                    {
                        "observation_id": f"{system_id}_dimer_hbond_force",
                        "system_id": system_id,
                        "method_family": "GFN2-xTB_EFEI_geometry_analysis",
                        "property_name": "double_chain_hydrogen_bonds_vs_force",
                        "value": "",
                        "unit": "count;nN",
                        "point_count": len(curve),
                        "source_location": name,
                        "target_origin": "computational",
                        "decision": "admitted_gold_c_science_rights_pending",
                        "independence_note": "paired descriptor for the same double-chain EFEI curve",
                        "quality_note": f"hydrogen-bond count range {int(min(v for _, v in curve))}-{int(max(v for _, v in curve))}",
                    }
                )

        md_pattern = re.compile(rf"^{re.escape(ROOT)}/dimer/MD/([A-Z]+[1-6])([IM])_re_135_([1-4])_135\.txt$")
        md_traces: dict[tuple[str, str], list[tuple[str, list[int]]]] = defaultdict(list)
        for name in names:
            match = md_pattern.match(name)
            if not match:
                continue
            stereo = "IIPU" if match.group(2) == "I" else "IMPU"
            values = [int(line) for line in zf.read(name).decode("utf-8-sig").splitlines() if line.strip()]
            md_traces[(stereo, match.group(1))].append((name, values))

        for (stereo, design), trace_rows in sorted(md_traces.items()):
            _, _, _, system_id = system_parts(stereo, design)
            flattened = [value for _, values in trace_rows for value in values]
            histogram = Counter(flattened)
            systems[system_id]["classical_md_hbond_available"] = True
            observations.append(
                {
                    "observation_id": f"{system_id}_md_hbond_distribution",
                    "system_id": system_id,
                    "method_family": "classical_MD_OPLS-AA",
                    "property_name": "interchain_hydrogen_bond_count_distribution",
                    "value": f"{statistics.fmean(flattened):.6f}",
                    "unit": "mean_count_per_snapshot",
                    "point_count": len(flattened),
                    "source_location": ";".join(name for name, _ in trace_rows),
                    "target_origin": "md",
                    "decision": "admitted_gold_c_science_rights_pending",
                    "independence_note": f"{len(trace_rows)} trace files grouped as one system-level observation; frames are correlated",
                    "quality_note": f"std={statistics.pstdev(flattened):.6f}; histogram={json.dumps(dict(sorted(histogram.items())), separators=(',', ':'))}",
                }
            )

        bulk_pattern = re.compile(
            rf"^{re.escape(ROOT)}/bulk/LAMMPS_(NO5M|SS4I)/15/([XYZ])/\1_deform_15\.dat$"
        )
        for name in names:
            match = bulk_pattern.match(name)
            if not match:
                continue
            bulk_name, axis = match.groups()
            stereo, design = ("IMPU", "NO5") if bulk_name == "NO5M" else ("IIPU", "SS4")
            _, _, _, system_id = system_parts(stereo, design)
            curve = read_bulk_curve(zf, name, bulk_name, axis)
            current_axes = set(filter(None, systems[system_id]["bulk_reaxff_curve_axes"].split(";")))
            current_axes.add(axis)
            systems[system_id]["bulk_reaxff_curve_axes"] = ";".join(sorted(current_axes))
            observations.append(
                {
                    "observation_id": f"{system_id}_bulk_reaxff_{axis.lower()}",
                    "system_id": system_id,
                    "method_family": "ReaxFF_MD",
                    "property_name": "high_rate_bulk_stress_strain_curve",
                    "value": f"{curve['block20_peak_stress_mpa']:.6f}",
                    "unit": "derived_block20_peak_MPa",
                    "point_count": curve["point_count"],
                    "source_location": name,
                    "target_origin": "md",
                    "decision": "admitted_gold_c_science_rights_pending",
                    "independence_note": "three loading axes per material are anisotropic protocol outputs, not independent materials",
                    "quality_note": (
                        f"axis={axis}; speed=15 m/s; strain_max={curve['strain_max_percent']:.3f}%; "
                        f"raw_peak={curve['raw_peak_stress_mpa']:.3f} MPa; "
                        f"block20_peak_strain={curve['strain_at_block20_peak_percent']:.3f}%"
                    ),
                }
            )

        inputs.extend(
            [
                {"method_family": "DFT_EFEI", "parameter": "software", "value": "ORCA 5.0", "source": "paper computational details"},
                {"method_family": "DFT_EFEI", "parameter": "functional", "value": "BP86", "source": "paper computational details"},
                {"method_family": "DFT_EFEI", "parameter": "basis_set", "value": "def2-TZVP", "source": "paper computational details"},
                {"method_family": "DFT_EFEI", "parameter": "dispersion", "value": "D3(BJ)", "source": "paper computational details"},
                {"method_family": "DFT_EFEI", "parameter": "force_increment", "value": "0.5 nN then 0.1 nN near scission", "source": "paper computational details"},
                {"method_family": "GFN2-xTB_EFEI", "parameter": "method", "value": "GFN2-xTB", "source": "paper computational details"},
                {"method_family": "GFN2-xTB_EFEI", "parameter": "force_increment", "value": "0.02 nN", "source": "paper computational details"},
                {"method_family": "classical_MD_OPLS-AA", "parameter": "software_force_field", "value": "LAMMPS; LigParGen OPLS-AA/CM1A-LBCC", "source": "paper and dimer/MD/input.in"},
                {"method_family": "classical_MD_OPLS-AA", "parameter": "cell_boundary", "value": "100 x 100 x 100 angstrom; periodic", "source": "paper computational details"},
                {"method_family": "classical_MD_OPLS-AA", "parameter": "ensemble_temperature", "value": "NVT; 500 K; Langevin", "source": "paper and dimer/MD/input.in"},
                {"method_family": "classical_MD_OPLS-AA", "parameter": "timestep_duration", "value": "0.001 ps; 8 ns; last 4 ns analyzed", "source": "paper and dimer/MD/input.in"},
                {"method_family": "classical_MD_OPLS-AA", "parameter": "snapshot_interval", "value": "2 ps; 2000 analyzed snapshots per trace file", "source": "paper and repository traces"},
                {"method_family": "classical_MD_OPLS-AA", "parameter": "hydrogen_bond_definition", "value": "H-acceptor <2.0 angstrom; donor-H-acceptor >135 degrees", "source": "paper computational details"},
                {"method_family": "ReaxFF_MD", "parameter": "software_force_field", "value": "LAMMPS; dispersion/CHONSSi-lg ReaxFF", "source": "paper and bulk input/ffield"},
                {"method_family": "ReaxFF_MD", "parameter": "system_size", "value": "24 chains x 4 repeat units; approximately 8500 atoms", "source": "paper computational details"},
                {"method_family": "ReaxFF_MD", "parameter": "equilibration", "value": "NPT 300 K then staged 300-800-300 K cycles until density change <0.1% for five cycles", "source": "paper computational details"},
                {"method_family": "ReaxFF_MD", "parameter": "final_density", "value": "1.25-1.35 g cm-3", "source": "paper computational details"},
                {"method_family": "ReaxFF_MD", "parameter": "loading", "value": "uniaxial X/Y/Z; 300 K; 1 atm; 15 m s-1", "source": "paper and bulk inputs"},
                {"method_family": "ReaxFF_MD", "parameter": "stress_conversion", "value": "engineering stress = -pressure_component/10 MPa", "source": "authors' Regression.ipynb"},
                {"method_family": "ReaxFF_MD", "parameter": "timestep", "value": "0.1 fs", "source": "bulk/uniaxial_tensile_deformation.in"},
            ]
        )

        category_counts = Counter(row["property_name"] for row in observations)
        numeric_point_counts = defaultdict(int)
        for row in observations:
            numeric_point_counts[row["property_name"]] += int(row["point_count"])

        source_metadata = {
            "source_id": "rsc2026_isohexide_dynamic_polyurethane_multiscale",
            "title": "A computational framework for tuning intra- and intermolecular ductility in polyurethanes",
            "authors": ["Chenxi Sheng", "Meng Huang", "Andrew P. Dove", "Linjiang Chen"],
            "article_doi": ARTICLE_DOI,
            "article_url": "https://pubs.rsc.org/en/content/articlehtml/2026/py/d5py01221j",
            "repository_url": REPOSITORY_URL,
            "fixed_commit": COMMIT,
            "archive_url": ARCHIVE_URL,
            "accessed_at": "2026-07-21",
            "article_license": "CC BY-NC 3.0 Unported",
            "repository_license": "none declared; GitHub API license=null and no LICENSE/COPYING file in fixed archive",
            "rights_decision": "scientifically admitted Gold-C reference; training and redistribution remain pending explicit repository/data-rights review",
            "redistribution_decision": "do not commit or redistribute the upstream archive until rights are cleared",
            "archive_size_bytes": ARCHIVE.stat().st_size,
            "archive_sha256": archive_sha,
            "internal_file_count": len(infos),
            "internal_uncompressed_bytes": sum(info.file_size for info in infos),
            "internal_license_files": internal_license_files,
        }
        atomic_write_text(
            HERE / "来源元数据.json",
            json.dumps(source_metadata, ensure_ascii=False, indent=2) + "\n",
        )

        write_tsv(
            HERE / "下载清单.tsv",
            [
                {
                    "file_name": ARCHIVE.name,
                    "download_url": ARCHIVE_URL,
                    "source_version": COMMIT,
                    "size_bytes": ARCHIVE.stat().st_size,
                    "sha256": archive_sha,
                    "license_state": "repository_no_license; article_CC_BY-NC_3.0",
                    "accessed_at": "2026-07-21",
                }
            ],
            ["file_name", "download_url", "source_version", "size_bytes", "sha256", "license_state", "accessed_at"],
        )
        file_rows = [
            {"group": group, "file_count": count, "uncompressed_bytes": sum(info.file_size for info in infos if PurePosixPath(info.filename).parts[1] == group)}
            for group, count in sorted(top_counts.items())
        ]
        file_rows.extend(
            {"group": f"extension:{extension}", "file_count": count, "uncompressed_bytes": sum(info.file_size for info in infos if (PurePosixPath(info.filename).suffix.lower() or "[none]") == extension)}
            for extension, count in sorted(extension_counts.items())
        )
        write_tsv(HERE / "文件内容计数.tsv", file_rows, ["group", "file_count", "uncompressed_bytes"])
        write_tsv(
            HERE / "计算体系清单.tsv",
            list(sorted(systems.values(), key=lambda row: row["system_id"])),
            [
                "system_id", "polyurethane_family", "stereochemical_ring", "dynamic_bond_motif",
                "hydrogen_bonding_motif", "chemistry_class", "structure_representation", "relaxed_xyz",
                "monomer_geometry_file_count", "single_chain_scission_available", "dimer_force_curve_available",
                "dimer_hbond_curve_available", "classical_md_hbond_available", "bulk_reaxff_curve_axes",
            ],
        )
        write_tsv(
            HERE / "计算观测清单.tsv",
            observations,
            [
                "observation_id", "system_id", "method_family", "property_name", "value", "unit",
                "point_count", "source_location", "target_origin", "decision", "independence_note", "quality_note",
            ],
        )
        write_tsv(HERE / "计算输入参数清单.tsv", inputs, ["method_family", "parameter", "value", "source"])

        candidates = [
            {
                "rank": 1,
                "candidate": "异山梨醇/异甘露醇动态PU多尺度DFT-xTB-MD-ReaxFF",
                "article_doi": ARTICLE_DOI,
                "data_identifier": f"git:{REPOSITORY_URL}@{COMMIT}",
                "access": "fixed archive downloaded and SHA-256 verified",
                "license": "article CC BY-NC 3.0; repository has no independent license",
                "verified_content": "72 chemical designs; ORCA/xTB geometries and curves; 11 MD H-bond systems; 2x3 ReaxFF bulk curves",
                "recommendation": "highest scientific value; admitted Gold-C scientific reference, local-only until rights review",
            },
            {
                "rank": 2,
                "candidate": "功能化石墨烯/TPU摩擦与力学MD",
                "article_doi": "10.1177/1350650120912612",
                "data_identifier": "10.25384/sage.12000450.v1",
                "access": "article body structurally extractable; legacy SAGE Figshare gateway returned HTTP 202/empty body during audit",
                "license": "DataCite rights: In Copyright",
                "verified_content": "pure TPU and 0.5-3 wt% functionalized graphene TPU; modulus, friction coefficient and abrasion outputs",
                "recommendation": "mechanics/tribology proxy; request file or permission before formal admission",
            },
            {
                "rank": 3,
                "candidate": "1,6-HDI/巴巴苏油2-单月桂酸甘油酯聚氨酯形成DFT",
                "article_doi": "10.21577/0103-5053.20200096",
                "data_identifier": "10.6084/m9.figshare.14303976.v1",
                "access": "official API metadata and OA article available; custom-domain file object currently stale/zero-byte",
                "license": "CC BY 4.0",
                "verified_content": "M06 DFT; stepwise vs concerted pathways; Table_1.xls plus six mechanism/energy figures listed",
                "recommendation": "valuable reaction-feasibility descriptor; retrieve through journal/SciELO mirror before admission",
            },
        ]
        write_tsv(
            HERE / "候选来源核验.tsv",
            candidates,
            ["rank", "candidate", "article_doi", "data_identifier", "access", "license", "verified_content", "recommendation"],
        )

        summary = {
            "audit_version": "batch7-computational-audit-v1",
            "generated_at": "2026-07-21",
            "source": source_metadata,
            "content": {
                "internal_files": len(infos),
                "uncompressed_bytes": sum(info.file_size for info in infos),
                "unique_chemical_designs": len(systems),
                "observation_rows": len(observations),
                "observation_counts": dict(sorted(category_counts.items())),
                "numeric_point_counts": dict(sorted(numeric_point_counts.items())),
                "monomer_xyz_files": sum(1 for name in names if "/monomer/" in name and name.endswith(".xyz")),
                "dimer_result_curves": sum(1 for name in names if name.endswith("_Result.txt")),
                "dimer_result_curves_with_numeric_rows": sum(
                    1
                    for name in names
                    if name.endswith("_Result.txt") and read_two_column_csv(zf, name)
                ),
                "dimer_hbond_force_curves": sum(1 for name in names if name.endswith("_HBonds.txt")),
                "dimer_hbond_force_curves_with_numeric_rows": sum(
                    1
                    for name in names
                    if name.endswith("_HBonds.txt") and read_two_column_csv(zf, name)
                ),
                "classical_md_hbond_trace_files": sum(1 for name in names if md_pattern.match(name)),
                "bulk_reaxff_stress_strain_files": sum(1 for name in names if bulk_pattern.match(name)),
                "lammps_data_files": sum(1 for name in names if name.endswith(".data")),
                "input_scripts": sum(1 for name in names if name.endswith(".in")),
            },
            "task_mapping": {
                "single_chain_scission_force": "dynamic-bond intrinsic strength and molecular fracture ranking",
                "double_chain_force_extension_curve": "interchain cohesion/shear proxy",
                "hydrogen_bond_descriptors": "supramolecular interaction and phase/morphology descriptor",
                "bulk_reaxff_stress_strain_curve": "high-rate anisotropic strength curve; not quasistatic experimental truth",
                "xyz_and_lammps_structures": "3D graph/conformer pretraining and simulation reproducibility",
            },
            "admission": {
                "recommended_layer": "Gold-C admitted scientific reference",
                "recommended_weight_ceiling": 0.20,
                "rights_gate": "materialized training weight and redistribution remain blocked until repository license review",
                "blockers": [
                    "GitHub repository has no independent LICENSE; associated article license cannot automatically be assumed to cover repository data",
                    "no canonical SMILES/reaction mapping yet",
                    "bulk ReaxFF loading speed is 15 m/s and must not be treated as quasistatic strength",
                    "curve points and loading axes are correlated and must not be counted as independent materials",
                    "one IIPU-S1 design lacks a usable near-scission geometry series",
                ],
            },
        }
        atomic_write_text(
            HERE / "内容审计摘要.json",
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        )

    print(json.dumps(summary["content"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
