"""生成第二阶段的 TPU 虚拟构件、组合与计量配方候选。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import yaml

import 候选配方 as candidates


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "配置" / "候选配方.yaml"
DEFAULT_INPUT = ROOT / "结果" / "可用数据集" / "候选结构.csv.gz"
DEFAULT_OUTPUT = ROOT / "候选"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_config(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("候选配方配置必须是映射")
    return value


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    compression = "gzip" if path.suffix == ".gz" else None
    frame.to_csv(path, index=False, compression=compression, float_format="%.12g")


def build(config: dict, input_path: Path, output: Path) -> dict[str, object]:
    source = pd.read_csv(input_path, low_memory=False)
    library, gate_audit = candidates.build_component_library(
        source, config["selection_counts"]
    )
    combinations = candidates.build_component_combinations(
        library,
        int(config["combination_design"]["macro_choices_per_diisocyanate"]),
        int(config["combination_design"]["extender_choices_per_macro"]),
    )
    formulations = candidates.build_formulations(combinations, config["formulation_grid"])

    paths = {
        "component_library": output / "候选构件库.csv",
        "gate_audit": output / "构件门禁审计.csv",
        "component_combinations": output / "候选组合库.csv",
        "formulations": output / "可合成配方候选.csv.gz",
    }
    write_csv(library, paths["component_library"])
    write_csv(gate_audit, paths["gate_audit"])
    write_csv(combinations, paths["component_combinations"])
    write_csv(formulations, paths["formulations"])

    manifest = {
        "candidate_release_id": config["release_id"],
        "purpose": "virtual_formulation_space_for_next_stage_screening",
        "input": {"path": str(input_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(input_path)},
        "config": config,
        "counts": {
            "component_rows": len(library),
            "combination_rows": len(combinations),
            "formulation_rows": len(formulations),
        },
        "outputs": {
            key: {
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for key, path in paths.items()
        },
        "interpretation_limits": [
            "全部记录是虚拟配方假设，不是已合成材料或性能标签。",
            "macrodiol_proxy 是结构代理；nominal_Mn 是待合成聚醚/聚酯宏二醇的目标数均分子量，不是该小分子结构的实测Mn。",
            "商业可得性、EHS、反应选择性、溶剂/催化剂和文献新颖性尚未逐条审查。",
            "第一阶段结构基线不具备跨来源发现外推能力，故本发布不产生高性能排序。",
        ],
    }
    manifest_path = output / "候选发布清单.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify(output: Path) -> None:
    manifest_path = output / "候选发布清单.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["outputs"].values():
        path = ROOT / entry["path"]
        if not path.is_file() or sha256(path) != entry["sha256"]:
            raise ValueError(f"候选发布核验失败: {path}")
    formulation_path = ROOT / manifest["outputs"]["formulations"]["path"]
    formulations = pd.read_csv(formulation_path)
    if not formulations["formulation_id"].is_unique:
        raise ValueError("候选配方 ID 不唯一")
    if not (formulations["stoichiometry_residual"] < 1e-10).all():
        raise ValueError("候选配方硬段计量残差异常")
    if not (formulations["nco_oh_ratio_calculated"] - formulations["nco_oh_ratio_target"]).abs().lt(1e-10).all():
        raise ValueError("候选配方 NCO/OH 计量残差异常")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--配置", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--输入", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--输出目录", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    output = args.输出目录.resolve()
    if args.检查:
        verify(output)
        print(f"候选发布核验通过: {output}")
        return
    manifest = build(load_config(args.配置.resolve()), args.输入.resolve(), output)
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
