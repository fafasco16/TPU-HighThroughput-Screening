import subprocess
import sys
from pathlib import Path
import 生成多目标材料索引 as source

ROOT = Path(__file__).resolve().parents[2]


def test_release():
    f = source.build_release()
    assert {"Cheetah", "Filaflex 60A"} <= set(f.material_key)
    assert (
        f.query("material_key in ['Cheetah','Filaflex 60A']")
        .objective_coverage_count.eq(2)
        .all()
    )
    assert (f.objective_coverage_count == 3).sum() > 0
    assert f.loc[f.objective_coverage_count.eq(3), "missing_objectives"].eq("").all()
    assert f.loc[f.objective_coverage_count.eq(2), "objective_gap_count"].eq(1).all()
    assert f.loc[f.objective_gap_count.gt(0), "gap_next_action"].ne("none").all()
    assert f.loc[f.material_key.eq("PMCL-2k-18HS"), "gap_evidence_status"].eq("source_tensile_sheet_empty").all()
    qub = f.loc[f.source_family.eq("QUB_生物基三重自修复TPU")]
    assert set(qub.material_key) == {"P35", "P40", "P45", "P40-HDO"}
    assert qub.loc[qub.material_key.eq("P40"), "objective_coverage_count"].eq(3).all()
    assert qub.loc[qub.material_key.isin(["P35", "P45"]), "objective_coverage_count"].eq(2).all()
    assert qub.loc[qub.material_key.eq("P40-HDO"), "objective_coverage_count"].eq(1).all()
    assert qub.loc[qub.material_key.eq("P40"), "cyclic_evidence_level"].eq(
        "hysteresis_proxy_not_direct_recovery"
    ).all()


def test_command():
    s = ROOT / "代码" / "生成多目标材料索引.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
