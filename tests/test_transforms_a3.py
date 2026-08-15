from pathlib import Path

from acp.transforms import a3_format

SOURCE = '''\
import os


def rate(value, factor):
    total = value * factor + 1

    return total


def other(x):
    return x
'''


def write(root: Path, source: str = SOURCE) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(exist_ok=True)
    path = pkg / "core.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_blank_lines_are_gone(tmp_path: Path):
    path = write(tmp_path)

    a3_format.apply(tmp_path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert all(line.strip() for line in lines)


def test_operator_spacing_is_gone(tmp_path: Path):
    path = write(tmp_path)

    a3_format.apply(tmp_path)

    assert "value*factor+1" in path.read_text(encoding="utf-8")


def test_indentation_survives_because_it_is_syntax(tmp_path: Path):
    path = write(tmp_path)

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert any(line.startswith("    ") for line in source.splitlines())


def test_the_code_still_runs(tmp_path: Path):
    path = write(tmp_path)

    a3_format.apply(tmp_path)

    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), "core.py", "exec"), namespace)
    assert namespace["rate"](6, 6) == 37


def test_strings_are_not_touched(tmp_path: Path):
    """Colapsar espacios dentro de una cadena cambia el programa, y varios
    finalistas comparan mensajes de error literales en sus tests."""
    path = write(tmp_path, 'MESSAGE = "a + b  se queda igual"\n')

    a3_format.apply(tmp_path)

    assert '"a + b  se queda igual"' in path.read_text(encoding="utf-8")
