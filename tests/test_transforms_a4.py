from pathlib import Path

from acp.transforms import a4_docs

SOURCE = '''\
"""Módulo."""
import os  # de la biblioteca estándar


def rate(value):
    """Calcula la tarifa."""
    # la regla viene de la norma
    return value * 2
'''


def write(root: Path, source: str = SOURCE) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(exist_ok=True)
    path = pkg / "core.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_comments_and_function_docstrings_are_gone(tmp_path: Path):
    path = write(tmp_path)

    a4_docs.apply(tmp_path)

    result = path.read_text(encoding="utf-8")
    assert "# de la biblioteca estándar" not in result
    assert "# la regla viene de la norma" not in result
    assert "Calcula la tarifa" not in result


def test_the_module_docstring_survives(tmp_path: Path):
    """El docstring de módulo es B3, no A4: dice qué hay en el fichero, no cómo
    funciona una función. Mezclarlos borraría el contraste que busca el spec."""
    path = write(tmp_path)

    a4_docs.apply(tmp_path)

    assert '"""Módulo."""' in path.read_text(encoding="utf-8")


def test_the_code_still_runs(tmp_path: Path):
    path = write(tmp_path)

    a4_docs.apply(tmp_path)

    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), "core.py", "exec"), namespace)
    assert namespace["rate"](21) == 42


def test_a_function_whose_body_is_only_a_docstring_keeps_a_body(tmp_path: Path):
    """Sin esto queda `def f():` sin cuerpo, que no compila: la condición se
    leería como un fracaso total del agente cuando es fontanería rota."""
    path = write(tmp_path, 'def noop():\n    """Nada."""\n')

    a4_docs.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "pass" in source


def test_it_reports_how_many_files_changed(tmp_path: Path):
    write(tmp_path)
    (tmp_path / "pkg" / "sin_docs.py").write_text("x = 1\n", encoding="utf-8")

    result = a4_docs.apply(tmp_path)

    assert result.files_changed == 1
