from pathlib import Path

from acp.transforms import b3_repo_docs

MODULE = '''\
"""Validación de identificadores españoles."""

import os  # sobrevive: eso es A4


def validate(number):
    """Sobrevive: la docstring de función es A4, no B3."""
    return number
'''


def build(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    path = pkg / "core.py"
    path.write_text(MODULE, encoding="utf-8")
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("guía\n", encoding="utf-8")
    return path


def test_the_readme_and_the_docs_directory_are_gone(tmp_path: Path):
    build(tmp_path)

    b3_repo_docs.apply(tmp_path)

    assert not (tmp_path / "README.md").exists()
    assert not (tmp_path / "docs").exists()


def test_module_docstrings_are_gone(tmp_path: Path):
    path = build(tmp_path)

    b3_repo_docs.apply(tmp_path)

    assert "Validación de identificadores" not in path.read_text(encoding="utf-8")


def test_function_docstrings_and_comments_survive(tmp_path: Path):
    """El reparto que sostiene el contraste del experimento: lo que te dice qué
    fichero abrir es B3; lo que te explica lo que ya has abierto es A4."""
    path = build(tmp_path)

    b3_repo_docs.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "Sobrevive: la docstring de función es A4" in source
    assert "# sobrevive: eso es A4" in source


def test_a_module_whose_body_is_only_a_docstring_still_compiles(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    path = pkg / "empty.py"
    path.write_text('"""Solo esto."""\n', encoding="utf-8")

    b3_repo_docs.apply(tmp_path)

    compile(path.read_text(encoding="utf-8"), "empty.py", "exec")


def test_a_doctest_in_a_module_docstring_survives(tmp_path: Path):
    """Misma razón que en A4: en python-stdnum los doctests son media suite, y
    borrarlos haría fallar la verificación de equivalencia por construcción."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    path = pkg / "core.py"
    path.write_text('"""Doc.\n\n>>> 1 + 1\n2\n"""\n', encoding="utf-8")

    b3_repo_docs.apply(tmp_path)

    assert ">>> 1 + 1" in path.read_text(encoding="utf-8")


def test_b3_is_registered_as_a_transformation():
    """La condición se pide por su identificador del spec (§4.2): sin registro,
    B3 existe pero la campaña no puede lanzarla."""
    from acp.transforms import TRANSFORMS

    assert TRANSFORMS["B3"] is b3_repo_docs.apply
