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


def test_keyword_comparisons_keep_the_space_they_need(tmp_path: Path):
    """`a in b` sin espacios sería `ainb`: en estos operadores el espacio es
    sintaxis, igual que la sangría, y LibCST se niega a escribirlos pegados."""
    path = write(tmp_path, "def f(a, b):\n    return a in b and a is not b\n")

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "a in b" in source
    assert "a is not b" in source


def test_comments_survive_because_they_are_A4(tmp_path: Path):
    """Llevarse los comentarios haría que A3 midiera también A4, y entonces
    ninguna de las dos celdas del diseño es atribuible."""
    path = write(
        tmp_path,
        "# la regla viene de la norma\n"
        "X = 1\n"
        "\n"
        "\n"
        "def f(a, b):\n"
        "    # el orden importa\n"
        "    return (a +  # la suma primero\n"
        "            b)\n",
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "# la regla viene de la norma" in source
    assert "# el orden importa" in source
    assert "# la suma primero" in source


def test_strings_are_not_touched(tmp_path: Path):
    """Colapsar espacios dentro de una cadena cambia el programa, y varios
    finalistas comparan mensajes de error literales en sus tests."""
    path = write(tmp_path, 'MESSAGE = "a + b  se queda igual"\n')

    a3_format.apply(tmp_path)

    assert '"a + b  se queda igual"' in path.read_text(encoding="utf-8")
