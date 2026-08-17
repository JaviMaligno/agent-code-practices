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


def test_the_readme_says_nothing_and_the_docs_directory_is_gone(tmp_path: Path):
    build(tmp_path)

    b3_repo_docs.apply(tmp_path)

    assert (tmp_path / "README.md").read_text(encoding="utf-8") == ""
    assert not (tmp_path / "docs").exists()


def test_the_readme_survives_as_a_file_because_the_packaging_reads_it(tmp_path: Path):
    """python-stdnum abre `README.md` en su `setup.py` para el long_description
    (setup.py:37), y pint, sqlglot y holidays lo declaran en el `pyproject`.
    Borrar el fichero revienta la construcción con FileNotFoundError antes de
    que el paquete pueda ni declarar su versión, y esa condición se leería como
    un fracaso total del agente cuando es fontanería rota (§4.3, fase 1). Se
    vacía: la información se va, el fichero se queda.
    """
    build(tmp_path)
    (tmp_path / "setup.py").write_text(
        "with open('README.md', 'rb') as fp:\n"
        "    long_description = fp.read().decode('utf-8')\n"
        "print(len(long_description))\n",
        encoding="utf-8",
    )

    b3_repo_docs.apply(tmp_path)

    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "setup.py"], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


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


def test_the_readme_is_found_whatever_it_is_called(tmp_path: Path):
    """Una lista cerrada de nombres exactos falla en silencio: el repo que
    escriba `Readme.markdown` conserva su README y la celda mide media dosis sin
    que nada lo cante. La dosis perdida invisible es peor que la declarada.
    """
    build(tmp_path)
    (tmp_path / "README.md").unlink()
    (tmp_path / "Readme.markdown").write_text("# demo\n", encoding="utf-8")
    (tmp_path / "readme.rst").write_text("demo\n", encoding="utf-8")
    # No es el README del repo: es documentación con nombre propio y se queda.
    (tmp_path / "readme-for-packagers.md").write_text("empaquetado\n", encoding="utf-8")

    b3_repo_docs.apply(tmp_path)

    assert (tmp_path / "Readme.markdown").read_text(encoding="utf-8") == ""
    assert (tmp_path / "readme.rst").read_text(encoding="utf-8") == ""
    assert (tmp_path / "readme-for-packagers.md").read_text(encoding="utf-8") != ""


def test_a_repo_that_publishes_its_docstrings_keeps_them(tmp_path: Path):
    """En python-stdnum la docstring de módulo no es documentación, es un dato:
    `stdnum.util.get_module_name()` la lee con `pydoc.getdoc()` para cada módulo
    de número y la publica, y su suite lo comprueba (`tests/test_util.doctest`).
    Borrarlas deja 412 de 413 tests y la cobertura por debajo del 100% exigido.
    Mismo reparto que en A4 con los doctests: lo que el programa lee no es
    documentación, así que se queda y la dosis real se declara.
    """
    path = build(tmp_path)
    (tmp_path / "pkg" / "util.py").write_text(
        "import pydoc\n"
        "\n"
        "\n"
        "def name_of(module):\n"
        "    return pydoc.splitdoc(pydoc.getdoc(module))[0]\n",
        encoding="utf-8",
    )

    b3_repo_docs.apply(tmp_path)

    assert "Validación de identificadores" in path.read_text(encoding="utf-8")
    # El resto de B3 sí corre: lo que se declara es la dosis, no la condición.
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == ""
    assert not (tmp_path / "docs").exists()


def test_a_module_that_reads_its_own_docstring_keeps_it(tmp_path: Path):
    """`tests/leakcheck.py` de sqlglot hace `__doc__.splitlines()[0]`: sin
    docstring eso es un AttributeError sobre None. Es el mismo caso que el de
    arriba, pero se ve fichero a fichero, así que solo cuesta esa docstring.
    """
    build(tmp_path)
    reader = tmp_path / "pkg" / "script.py"
    reader.write_text(
        '"""Se lee a sí mismo."""\n\nSUBJECT = __doc__.splitlines()[0]\n', encoding="utf-8"
    )
    other = tmp_path / "pkg" / "plain.py"
    other.write_text('"""Nadie me lee."""\n\nVALUE = 1\n', encoding="utf-8")

    b3_repo_docs.apply(tmp_path)

    assert "Se lee a sí mismo" in reader.read_text(encoding="utf-8")
    assert "Nadie me lee" not in other.read_text(encoding="utf-8")


def test_reading_a_class_docstring_is_not_reading_a_module_docstring(tmp_path: Path):
    """holidays lee `cls.__doc__` en sus scripts de l10n. Eso es asunto de A4:
    ensanchar la excepción a cualquier `__doc__` dejaría a B3 sin la mitad de su
    dosis en un repo donde nadie lee la docstring de un módulo.
    """
    path = build(tmp_path)
    (tmp_path / "pkg" / "l10n.py").write_text(
        "def describe(cls):\n    return cls.__doc__ or ''\n", encoding="utf-8"
    )

    b3_repo_docs.apply(tmp_path)

    assert "Validación de identificadores" not in path.read_text(encoding="utf-8")


def test_b3_is_registered_as_a_transformation():
    """La condición se pide por su identificador del spec (§4.2): sin registro,
    B3 existe pero la campaña no puede lanzarla."""
    from acp.transforms import TRANSFORMS

    assert TRANSFORMS["B3"] is b3_repo_docs.apply
