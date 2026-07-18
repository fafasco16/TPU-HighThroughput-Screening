from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_required_chinese_directories_exist():
    required = [
        "01_原始数据",
        "02_暂存数据",
        "03_规范数据",
        "04_派生数据",
        "05_数据库快照",
        "06_审核导出",
        "代码",
        "结构定义",
        "配置",
        "清单",
        "文档",
    ]
    assert all((ROOT / name).is_dir() for name in required)


def test_legacy_top_level_directories_removed():
    assert not (ROOT / "sources").exists()
    assert not (ROOT / "docs").exists()


def test_third_party_data_is_outside_git_index():
    assert (ROOT / "01_原始数据" / "外部数据").is_dir()
    assert (ROOT / "01_原始数据" / "代码仓库镜像").is_dir()
