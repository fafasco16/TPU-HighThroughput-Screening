from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_required_chinese_directories_exist():
    required = ["数据", "代码", "配置", "文档", "结果"]
    assert all((ROOT / name).is_dir() for name in required)

    data_layers = ["原始", "暂存", "规范", "派生", "快照"]
    assert all((ROOT / "数据" / name).is_dir() for name in data_layers)
    assert (ROOT / "配置" / "结构定义").is_dir()
    assert (ROOT / "配置" / "清单").is_dir()


def test_legacy_top_level_directories_removed():
    legacy = [
        "01_原始数据",
        "02_暂存数据",
        "03_规范数据",
        "04_派生数据",
        "05_数据库快照",
        "06_审核导出",
        "结构定义",
        "清单",
        "sources",
        "docs",
    ]
    assert all(not (ROOT / name).exists() for name in legacy)


def test_third_party_data_is_outside_git_index():
    assert (ROOT / "数据/原始" / "外部数据").is_dir()
    assert (ROOT / "数据/原始" / "代码仓库镜像").is_dir()
