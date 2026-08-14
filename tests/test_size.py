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


def test_counts_the_files_that_cannot_be_parsed(tmp_path):
    """Un fichero que no parsea desaparece de las métricas de AST pero sus
    líneas siguen contando: si nadie lo publica, la omisión no se ve."""
    build_tree(tmp_path)
    (tmp_path / "pkg" / "broken.py").write_text("def f(:\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.python_files == 4
    assert result.unparseable_files == 1


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


def test_skips_test_packages_nested_in_the_source_package(tmp_path):
    """pint guarda su suite en pint/testsuite/: 12.016 líneas de test contadas como código."""
    build_tree(tmp_path)
    (tmp_path / "pkg" / "testsuite").mkdir()
    (tmp_path / "pkg" / "testsuite" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "testsuite" / "test_x.py").write_text("x = 1\n" * 50, encoding="utf-8")
    (tmp_path / "pkg" / "test_suite").mkdir()
    (tmp_path / "pkg" / "test_suite" / "helper.py").write_text("y = 2\n" * 10, encoding="utf-8")

    result = measure(tmp_path)

    assert result.python_files == 3
    assert result.code_lines == 3


def test_skips_test_files_outside_test_directories(tmp_path):
    build_tree(tmp_path)
    (tmp_path / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    (tmp_path / "tests.py").write_text("x = 1\n" * 30, encoding="utf-8")
    (tmp_path / "pkg" / "test_a.py").write_text("x = 1\n" * 20, encoding="utf-8")
    (tmp_path / "pkg" / "a_test.py").write_text("x = 1\n" * 20, encoding="utf-8")

    result = measure(tmp_path)

    assert result.python_files == 3
    assert result.code_lines == 3


def test_keeps_source_packages_whose_name_merely_starts_with_test(tmp_path):
    """`testfixtures` es una librería real: excluirla entera sería peor que el fallo."""
    build_tree(tmp_path)
    (tmp_path / "testfixtures").mkdir()
    (tmp_path / "testfixtures" / "core.py").write_text("x = 1\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.python_files == 4
