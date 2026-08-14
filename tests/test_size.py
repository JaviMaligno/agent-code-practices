from pathlib import Path

from acp.metrics.size import measure


def build_tree(root: Path) -> None:
    (root / "pkg" / "sub").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "a.py").write_text("x = 1\n\n# comment\ny = 2\n", encoding="utf-8")
    (root / "pkg" / "sub" / "b.py").write_text("z = 3\n", encoding="utf-8")
    (root / "notes.txt").write_text("ignored\n", encoding="utf-8")


def test_counts_python_files_and_code_lines(tmp_path):
    build_tree(tmp_path)
    result = measure(tmp_path)
    assert result.python_files == 3
    assert result.code_lines == 3  # __init__ vacío, dos líneas en a.py, una en b.py


def test_depth_is_measured_relative_to_root(tmp_path):
    build_tree(tmp_path)
    result = measure(tmp_path)
    assert result.max_depth == 2
    assert result.mean_depth == 4 / 3  # profundidades 1, 1 y 2


def test_skips_test_and_vendor_directories(tmp_path):
    build_tree(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("x = 1\n", encoding="utf-8")
    result = measure(tmp_path)
    assert result.python_files == 3
