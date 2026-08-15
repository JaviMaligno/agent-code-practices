from pathlib import Path

from acp.transforms import a1_types

SOURCE = '''\
from __future__ import annotations

import os  # comentario que debe sobrevivir

TOTAL: int = 0


def rate(value: int, factor: float = 1.0) -> float:
    """Sobrevive: esto es A4, no A1."""
    partial: float = value * factor
    return partial
'''


def write(root: Path, source: str = SOURCE) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(exist_ok=True)
    path = pkg / "core.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_annotations_are_gone(tmp_path: Path):
    path = write(tmp_path)

    a1_types.apply(tmp_path)

    result = path.read_text(encoding="utf-8")
    assert "value: int" not in result
    assert "-> float" not in result
    assert "partial: float" not in result
    assert "TOTAL: int" not in result


def test_only_the_types_change(tmp_path: Path):
    """A1 mide el valor de los tipos. Si de paso se lleva un comentario o una
    docstring, mide A4 y el resultado no es atribuible."""
    path = write(tmp_path)

    a1_types.apply(tmp_path)

    result = path.read_text(encoding="utf-8")
    assert "# comentario que debe sobrevivir" in result
    assert "Sobrevive: esto es A4, no A1." in result


def test_defaults_keep_their_spacing(tmp_path: Path):
    """Quitar la anotación deja `factor = 1.0`, y ese espaciado es un cambio de
    formato: sería A3 colándose dentro de A1."""
    path = write(tmp_path)

    a1_types.apply(tmp_path)

    assert "factor=1.0" in path.read_text(encoding="utf-8")


def test_an_annotated_assignment_without_value_becomes_nothing(tmp_path: Path):
    """`x: int` sin valor no declara nada en ejecución: al quitar el tipo no
    puede quedar `x`, que sería un NameError."""
    path = write(tmp_path, "def f():\n    x: int\n    x = 1\n    return x\n")

    a1_types.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "x: int" not in source


def test_the_code_still_runs(tmp_path: Path):
    path = write(tmp_path)

    a1_types.apply(tmp_path)

    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), "core.py", "exec"), namespace)
    assert namespace["rate"](21, 2.0) == 42.0
