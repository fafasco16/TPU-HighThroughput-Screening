"""严格解析 xTB 6.7.1 单点结果并聚合电子能代理构象系综。

本模块只处理 xTB 原生的能量、轨道、偶极、电荷、WBO 和 GFN2/D4
``Mol. α(0) /au`` 输出。任一必需字段不闭合时逐构象失败；构件中只要有
一个构象失败，就不会对剩余构象重新归一化并发布伪完整的系综均值。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

HARTREE_TO_KCAL_MOL = 627.509474
AU_DIPOLE_TO_DEBYE = 2.541746
GAS_CONSTANT_KCAL_MOL_K = 0.00198720425864083
DEFAULT_ENSEMBLE_TEMPERATURE_K = 298.15
EXPECTED_METHOD = "GFN2-xTB"
EXPECTED_XTB_VERSION = "6.7.1"
CHARGE_SUM_TOLERANCE_E = 1e-5
GAP_TOLERANCE_EV = 1e-4

_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?"
_MOLECULAR_ALPHA = re.compile(
    rf"^\s*Mol\.\s*(?:α|alpha)\(0\)\s*/\s*au\s*:\s*({_NUMBER})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_ATOMIC_ALPHA_HEADER = re.compile(r"^\s*#\s+Z\s+covCN\s+q\s+C6AA\s+(?:α|alpha)\(0\)\s*$", re.I)


class XtbOutputError(ValueError):
    """xTB 输出未通过科学数据门。"""


def sha256(path: Path) -> str:
    """返回文件内容的 SHA-256。"""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise XtbOutputError(f"{field}: expected a finite number")
    try:
        normalized = value.replace("D", "E").replace("d", "e") if isinstance(value, str) else value
        number = float(normalized)
    except (TypeError, ValueError) as exc:
        raise XtbOutputError(f"{field}: expected a finite number") from exc
    if not math.isfinite(number):
        raise XtbOutputError(f"{field}: expected a finite number")
    return number


def _finite_array(value: Any, field: str, *, length: int | None = None) -> list[float]:
    if not isinstance(value, list):
        raise XtbOutputError(f"{field}: expected an array")
    if length is not None and len(value) != length:
        raise XtbOutputError(f"{field}: expected {length} values, found {len(value)}")
    return [_finite_number(item, f"{field}[{index}]") for index, item in enumerate(value)]


def _load_json(source: Path | Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if isinstance(source, Mapping):
        return dict(source), ""
    path = Path(source)
    if not path.is_file():
        raise XtbOutputError(f"missing xtbout.json: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise XtbOutputError(f"invalid xtbout.json: {path}") from exc
    if not isinstance(data, dict):
        raise XtbOutputError("xtbout.json root must be an object")
    return data, sha256(path)


def _frontier_orbitals(energies: Sequence[float], occupations: Sequence[float]) -> tuple[float, float, float]:
    if len(energies) != len(occupations) or len(energies) < 2:
        raise XtbOutputError("orbital energies and occupations must have the same length >= 2")
    if any(occupations[index] + 1e-12 < occupations[index + 1] for index in range(len(occupations) - 1)):
        raise XtbOutputError("ambiguous_frontier_occupancy: occupations are not monotonic")
    occupied = [index for index, occupation in enumerate(occupations) if occupation >= 1.0]
    if not occupied or occupied != list(range(occupied[-1] + 1)) or occupied[-1] + 1 >= len(energies):
        raise XtbOutputError("ambiguous_frontier_occupancy")
    homo_index = occupied[-1]
    lumo_index = homo_index + 1
    if occupations[lumo_index] >= 1.0:
        raise XtbOutputError("ambiguous_frontier_occupancy")
    homo, lumo = energies[homo_index], energies[lumo_index]
    gap = lumo - homo
    if gap < 0:
        raise XtbOutputError("derived HOMO-LUMO gap is negative")
    return homo, lumo, gap


def parse_xtbout_json(
    source: Path | Mapping[str, Any],
    *,
    expected_total_charge: float,
    expected_atom_count: int | None = None,
    expected_method: str = EXPECTED_METHOD,
    expected_version: str = EXPECTED_XTB_VERSION,
) -> dict[str, Any]:
    """解析 6.7.1 官方 JSON 键并执行方法、能隙和电荷和校验。"""

    data, source_hash = _load_json(source)
    if data.get("method") != expected_method:
        raise XtbOutputError(f"method mismatch: {data.get('method')!r} != {expected_method!r}")
    if str(data.get("xtb version", "")).strip() != expected_version:
        raise XtbOutputError(f"xtb version mismatch: {data.get('xtb version')!r} != {expected_version!r}")

    energy = _finite_number(data.get("total energy"), "total energy")
    reported_gap = _finite_number(data.get("HOMO-LUMO gap / eV"), "HOMO-LUMO gap / eV")
    orbital_energies = _finite_array(data.get("orbital energies / eV"), "orbital energies / eV")
    occupations = _finite_array(data.get("fractional occupation"), "fractional occupation")
    homo, lumo, derived_gap = _frontier_orbitals(orbital_energies, occupations)
    if abs(derived_gap - reported_gap) > GAP_TOLERANCE_EV:
        raise XtbOutputError(
            f"HOMO-LUMO gap mismatch: derived {derived_gap:.8g} eV, reported {reported_gap:.8g} eV"
        )

    dipole = _finite_array(data.get("dipole / a.u."), "dipole / a.u.", length=3)
    charges = _finite_array(data.get("partial charges"), "partial charges")
    if expected_atom_count is not None and len(charges) != int(expected_atom_count):
        raise XtbOutputError(f"partial charges: atom count {len(charges)} != {expected_atom_count}")
    expected_charge = _finite_number(expected_total_charge, "expected_total_charge")
    charge_sum = math.fsum(charges)
    if abs(charge_sum - expected_charge) > CHARGE_SUM_TOLERANCE_E:
        raise XtbOutputError(
            f"partial charge sum mismatch: {charge_sum:.8g} e != {expected_charge:.8g} e"
        )

    return {
        "method": expected_method,
        "xtb_version": expected_version,
        "total_energy_hartree": energy,
        "homo_ev": homo,
        "lumo_ev": lumo,
        "homo_lumo_gap_ev": derived_gap,
        "reported_homo_lumo_gap_ev": reported_gap,
        "dipole_x_au": dipole[0],
        "dipole_y_au": dipole[1],
        "dipole_z_au": dipole[2],
        "dipole_magnitude_debye": math.sqrt(math.fsum(value * value for value in dipole)) * AU_DIPOLE_TO_DEBYE,
        "partial_charges": charges,
        "partial_charge_sum_e": charge_sum,
        "xtbout_json_sha256": source_hash,
    }


def parse_wbo(source: Path | str) -> dict[tuple[int, int], float]:
    """解析 xTB ``wbo`` 三列表，键统一为升序的 1-based 原子索引。"""

    if isinstance(source, Path):
        if not source.is_file():
            raise XtbOutputError(f"missing wbo file: {source}")
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise XtbOutputError(f"cannot read wbo file: {source}") from exc
    else:
        text = source
    bonds: dict[tuple[int, int], float] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 3:
            raise XtbOutputError(f"wbo line {line_number}: expected three fields")
        try:
            first, second = int(fields[0]), int(fields[1])
        except ValueError as exc:
            raise XtbOutputError(f"wbo line {line_number}: invalid atom index") from exc
        value = _finite_number(fields[2].replace("D", "E").replace("d", "e"), f"wbo line {line_number}")
        if first <= 0 or second <= 0 or first == second or value < 0:
            raise XtbOutputError(f"wbo line {line_number}: invalid bond record")
        key = tuple(sorted((first, second)))
        if key in bonds:
            raise XtbOutputError(f"wbo line {line_number}: duplicate bond {key}")
        bonds[key] = value
    if not bonds:
        raise XtbOutputError("wbo file contains no bond records")
    return bonds


def parse_polarizability_stdout(source: Path | str) -> dict[str, Any]:
    """解析 GFN2/D4 分子及可选逐原子 ``α(0)``，不推测无标签值。"""

    source_hash = ""
    if isinstance(source, Path):
        if not source.is_file():
            raise XtbOutputError(f"missing xtb stdout: {source}")
        try:
            text = source.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            raise XtbOutputError(f"cannot read xtb stdout: {source}") from exc
        source_hash = sha256(source)
    else:
        text = source
        source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    matches = list(_MOLECULAR_ALPHA.finditer(text))
    if not matches:
        raise XtbOutputError("missing_polarizability_output")
    if len(matches) != 1:
        raise XtbOutputError("ambiguous_polarizability_output")
    match = matches[0]
    molecular = _finite_number(match.group(1).replace("D", "E").replace("d", "e"), "Mol. α(0) /au")
    if molecular < 0:
        raise XtbOutputError("Mol. α(0) /au must be non-negative")

    lines = text.splitlines()
    atomic: list[float] = []
    for index, line in enumerate(lines):
        if not _ATOMIC_ALPHA_HEADER.match(line):
            continue
        for atom_line in lines[index + 1 :]:
            fields = atom_line.split()
            if len(fields) != 7 or not fields[0].isdigit() or not fields[1].isdigit():
                break
            value = _finite_number(fields[-1].replace("D", "E").replace("d", "e"), "atomic α(0)")
            if value < 0:
                raise XtbOutputError("atomic α(0) must be non-negative")
            atomic.append(value)
        break
    return {
        "gfn2_d4_alpha0_au": molecular,
        "gfn2_d4_atomic_alpha0_au": atomic,
        "polarizability_source_line": match.group(0).strip(),
        "stdout_sha256": source_hash,
    }


def parse_conformer_directory(
    directory: Path,
    *,
    expected_total_charge: float,
    expected_atom_count: int | None = None,
) -> dict[str, Any]:
    """按一构象一目录门禁解析生产结果；极化率缺失降级为 partial_property。"""

    directory = Path(directory)
    if not directory.is_dir():
        raise XtbOutputError(f"missing conformer directory: {directory}")
    if not (directory / ".xtbok").is_file():
        raise XtbOutputError("missing .xtbok success marker")
    if (directory / ".sccnotconverged").exists():
        raise XtbOutputError("SCC did not converge")
    json_result = parse_xtbout_json(
        directory / "xtbout.json",
        expected_total_charge=expected_total_charge,
        expected_atom_count=expected_atom_count,
    )
    wbo_path = directory / "wbo"
    wbo = parse_wbo(wbo_path)
    result = {**json_result, "wbo": wbo, "wbo_sha256": sha256(wbo_path), "run_status": "success", "warning_codes": []}
    try:
        result.update(parse_polarizability_stdout(directory / "xtb.out"))
    except XtbOutputError as exc:
        if str(exc) != "missing_polarizability_output":
            raise
        result.update(
            gfn2_d4_alpha0_au=None,
            gfn2_d4_atomic_alpha0_au=[],
            polarizability_source_line="",
            stdout_sha256=sha256(directory / "xtb.out") if (directory / "xtb.out").is_file() else "",
            run_status="partial_property",
            warning_codes=["missing_polarizability_output"],
        )
    return result


def electronic_energy_proxy_weights(
    energies_hartree: Iterable[float], temperature_k: float = DEFAULT_ENSEMBLE_TEMPERATURE_K
) -> tuple[list[float], list[float]]:
    """返回相对能量(kcal/mol)与数值稳定、严格归一的电子能代理权重。"""

    energies = [_finite_number(value, "total_energy_hartree") for value in energies_hartree]
    if not energies:
        raise XtbOutputError("energies must be a non-empty sequence")
    temperature = _finite_number(temperature_k, "ensemble_temperature_k")
    if temperature <= 0:
        raise XtbOutputError("ensemble_temperature_k must be positive")
    minimum = min(energies)
    relative = [(energy - minimum) * HARTREE_TO_KCAL_MOL for energy in energies]
    rt = GAS_CONSTANT_KCAL_MOL_K * temperature
    log_weights = [-delta / rt for delta in relative]
    maximum = max(log_weights)
    factors = [math.exp(value - maximum) for value in log_weights]
    denominator = math.fsum(factors)
    weights = [value / denominator for value in factors]
    normalization = math.fsum(weights)
    weights = [value / normalization for value in weights]
    if abs(math.fsum(weights) - 1.0) >= 1e-12:
        raise XtbOutputError("Boltzmann proxy weights do not sum to one")
    return relative, weights


def weighted_scalar_summary(values: Sequence[float], weights: Sequence[float]) -> dict[str, float]:
    """计算总体加权均值/标准差及未加权范围。"""

    if not values or len(values) != len(weights):
        raise XtbOutputError("values and weights must have the same non-zero length")
    numbers = [_finite_number(value, "property value") for value in values]
    probabilities = [_finite_number(value, "weight") for value in weights]
    if any(value < 0 for value in probabilities) or abs(math.fsum(probabilities) - 1.0) >= 1e-12:
        raise XtbOutputError("weights must be non-negative and sum to one")
    mean = math.fsum(weight * value for weight, value in zip(probabilities, numbers))
    variance = math.fsum(weight * (value - mean) ** 2 for weight, value in zip(probabilities, numbers))
    return {"weighted_mean": mean, "weighted_sd": math.sqrt(max(0.0, variance)), "min": min(numbers), "max": max(numbers)}


DEFAULT_SCALAR_FIELDS = (
    "homo_ev",
    "lumo_ev",
    "homo_lumo_gap_ev",
    "dipole_magnitude_debye",
    "gfn2_d4_alpha0_au",
)


def aggregate_component_ensemble(
    conformers: Iterable[Mapping[str, Any]],
    *,
    expected_conformer_count: int | None = None,
    temperature_k: float = DEFAULT_ENSEMBLE_TEMPERATURE_K,
    scalar_fields: Sequence[str] = DEFAULT_SCALAR_FIELDS,
) -> dict[str, Any]:
    """聚合构件系综；任何硬失败都禁止发布权重和完整加权统计。

    输入是逐构象的标量解析记录，可直接传生成器；函数不会读取或缓存 XYZ
    坐标。内存规模因此与单个构件的描述符行数相关，而非全批次坐标规模。
    """

    rows = [dict(record) for record in conformers]
    expected = len(rows) if expected_conformer_count is None else int(expected_conformer_count)
    if expected < 0:
        raise XtbOutputError("expected_conformer_count must be non-negative")
    successful = [row for row in rows if row.get("run_status") in {"success", "partial_property"}]
    hard_failure = len(rows) != expected or len(successful) != expected
    base: dict[str, Any] = {
        "conformer_count_input": expected,
        "conformer_count_success": len(successful),
        "ensemble_temperature_k": float(temperature_k),
        "ensemble_status": "incomplete" if hard_failure else "complete",
        "energy_span_kcal_mol": None,
        "dominant_conformer_weight": None,
        "conformer_count_weight_ge_0p01": None,
        "effective_conformer_count": None,
        "boltzmann_weight_sum": None,
        "conformers": rows,
    }
    for field in scalar_fields:
        for suffix in ("weighted_mean", "weighted_sd", "min", "max"):
            base[f"{field}_{suffix}"] = None
    if hard_failure or expected == 0:
        for row in rows:
            row["relative_energy_kcal_mol"] = None
            row["boltzmann_proxy_weight_298K"] = None
        return base

    relative, weights = electronic_energy_proxy_weights(
        [row.get("total_energy_hartree") for row in successful], temperature_k
    )
    for row, delta, weight in zip(successful, relative, weights):
        row["relative_energy_kcal_mol"] = delta
        row["boltzmann_proxy_weight_298K"] = weight
    entropy = -math.fsum(weight * math.log(weight) for weight in weights if weight > 0)
    base.update(
        energy_span_kcal_mol=max(relative),
        dominant_conformer_weight=max(weights),
        conformer_count_weight_ge_0p01=sum(weight >= 0.01 for weight in weights),
        effective_conformer_count=math.exp(entropy),
        boltzmann_weight_sum=math.fsum(weights),
    )
    if any(row.get("run_status") == "partial_property" for row in successful):
        base["ensemble_status"] = "partial_property"
    for field in scalar_fields:
        values = [row.get(field) for row in successful]
        if any(value is None for value in values):
            continue
        stats = weighted_scalar_summary(values, weights)
        for suffix, value in stats.items():
            base[f"{field}_{suffix}"] = value
    return base
