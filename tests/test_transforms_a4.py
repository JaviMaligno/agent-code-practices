import doctest
import importlib.util
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


DOCTESTED = '''\
def apply_tax(amount):
    """Aplica el impuesto indirecto.

    La regla viene de la norma vigente.

    >>> apply_tax(100)
    121.0
    """
    return round(amount * 1.21, 2)
'''


def write(root: Path, source: str = SOURCE) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(exist_ok=True)
    path = pkg / "core.py"
    path.write_text(source, encoding="utf-8")
    return path


def run_doctests(path: Path) -> doctest.TestResults:
    """Corre los doctests del fichero como los correría `--doctest-modules`."""
    spec = importlib.util.spec_from_file_location("under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return doctest.testmod(module, verbose=False)


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


def test_a_body_that_was_only_a_docstring_still_runs(tmp_path: Path):
    """Sin cuerpo, `def f():` no compila: la condición se leería como un fracaso
    total del agente cuando es fontanería rota.

    La aserción es de comportamiento a propósito. La versión anterior pedía que
    la cadena `pass` estuviera en el fichero, y eso no podía fallar: el `pass`
    lo escribe el renderizador de libcst para cualquier bloque vacío, así que el
    test daba verde con la implementación puesta y quitada. Ejecutar el
    resultado sí distingue las dos cosas."""
    path = write(
        tmp_path,
        'def noop():\n    """Nada."""\n\n\nclass Empty:\n    """Nada tampoco."""\n',
    )

    a4_docs.apply(tmp_path)

    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), "core.py", "exec"), namespace)
    assert namespace["noop"]() is None
    assert namespace["noop"].__doc__ is None
    assert namespace["Empty"]().__doc__ is None


def test_removing_a_comment_leaves_no_whitespace_behind(tmp_path: Path):
    """Al quitar `# de la biblioteca estándar` la línea se queda con los espacios
    que separaban el comentario del código, y la línea del comentario suelto se
    queda con la sangría del bloque. Eso es un cambio de formato (A3) colado
    dentro de A4, y en un repo cuya suite pasa el linter lo lee como una
    transformación que rompe el repo, no como una práctica que falta."""
    path = write(tmp_path)

    a4_docs.apply(tmp_path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line != line.rstrip()] == []


def test_a_doctest_survives_because_it_is_suite_and_not_documentation(tmp_path: Path):
    """python-stdnum corre su suite con `--doctest-modules`: los ejemplos `>>>`
    de las docstrings SON 413 de sus tests. Borrarlos no es quitar una práctica
    de escritura, es quitar tests, y la verificación de equivalencia de §4.3
    fallaría por construcción en cualquier repo que documente con ejemplos."""
    path = write(tmp_path, DOCTESTED)

    a4_docs.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "Aplica el impuesto indirecto" not in source
    assert "La regla viene de la norma vigente" not in source
    assert ">>> apply_tax(100)" in source
    assert "121.0" in source
    results = run_doctests(path)
    assert (results.attempted, results.failed) == (1, 0)


def test_a_docstring_that_is_only_prose_disappears_whole(tmp_path: Path):
    """La dosis de A4 no se rebaja donde no hay ejemplos que proteger."""
    path = write(tmp_path)

    a4_docs.apply(tmp_path)

    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), "core.py", "exec"), namespace)
    assert namespace["rate"].__doc__ is None


def test_a_docstring_that_is_only_doctests_is_left_exactly_as_it_was(tmp_path: Path):
    source = '''\
def apply_tax(amount):
    """
    >>> apply_tax(100)
    121.0
    """
    return round(amount * 1.21, 2)
'''
    path = write(tmp_path, source)

    a4_docs.apply(tmp_path)

    assert path.read_text(encoding="utf-8") == source


def test_only_the_examples_and_their_output_survive_a_mixed_docstring(tmp_path: Path):
    """Se conservan las líneas `>>>`, sus continuaciones `...` y la salida
    esperada de cada bloque; la prosa de alrededor se va, que es lo que A4
    quiere medir."""
    path = write(
        tmp_path,
        '''\
def apply_tax(amount):
    """Aplica el impuesto.

    Explicación larga de por qué el tipo es ese.

    >>> apply_tax(
    ...     100
    ... )
    121.0

    Y otra explicación entre ejemplos.

    >>> apply_tax(0)
    0.0
    """
    return round(amount * 1.21, 2)
''',
    )

    a4_docs.apply(tmp_path)

    assert path.read_text(encoding="utf-8") == '''\
def apply_tax(amount):
    """
    >>> apply_tax(
    ...     100
    ... )
    121.0

    >>> apply_tax(0)
    0.0
    """
    return round(amount * 1.21, 2)
'''
    results = run_doctests(path)
    assert (results.attempted, results.failed) == (2, 0)


def test_the_text_glued_under_an_example_is_output_and_not_prose(tmp_path: Path):
    """Para doctest, la salida esperada acaba en la primera línea en blanco: una
    frase pegada bajo el resultado es parte de lo que el ejemplo espera, y
    quitarla convierte un test que pasa en uno que falla."""
    path = write(
        tmp_path,
        '''\
def shout(word):
    """Grita.

    >>> shout("eh")
    Traceback (most recent call last):
        ...
    ValueError: no
    """
    raise ValueError("no")
''',
    )

    a4_docs.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "Traceback (most recent call last):" in source
    assert "ValueError: no" in source
    assert "Grita." not in source
    results = run_doctests(path)
    assert (results.attempted, results.failed) == (1, 0)


def test_a_comment_that_a_tool_reads_is_not_prose(tmp_path: Path):
    """`# pragma: no cover`, `# noqa` y `# type: ignore` no le explican nada a
    quien lee: le hablan a coverage, al linter y al type checker. python-stdnum
    tiene 29 pragmas y `fail_under = 100` en su setup.cfg, así que borrarlos
    pone su suite en rojo sin que falle un solo test — el agente vería rojo en
    las dos condiciones de la celda A4 sin haber tocado nada."""
    path = write(
        tmp_path,
        "from typing import TYPE_CHECKING\n"
        "import os  # noqa: F401\n"
        "\n"
        "if TYPE_CHECKING:  # pragma: no cover\n"
        "    import sys\n"
        "\n"
        "\n"
        "def rate(value):\n"
        "    # la regla viene de la norma\n"
        "    total = value * 2  # type: ignore[assignment]\n"
        "    return total\n",
    )

    a4_docs.apply(tmp_path)

    result = path.read_text(encoding="utf-8")
    assert "import os  # noqa: F401" in result
    assert "if TYPE_CHECKING:  # pragma: no cover" in result
    assert "total = value * 2  # type: ignore[assignment]" in result
    assert "# la regla viene de la norma" not in result


def test_the_shebang_and_the_encoding_cookie_stay(tmp_path: Path):
    """Las dos son comentarios para el parser, no para el lector: sin shebang un
    script deja de ser ejecutable y sin cookie el intérprete decodifica el
    fichero con otra codificación. Nada de eso es legibilidad, y romperlo mete
    en la celda A4 un fallo que no tiene que ver con lo que se mide."""
    path = write(
        tmp_path,
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "# este sí es prosa\n"
        "def rate(value):\n"
        "    return value * 2\n",
    )

    a4_docs.apply(tmp_path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#!/usr/bin/env python3"
    assert lines[1] == "# -*- coding: utf-8 -*-"
    assert "# este sí es prosa" not in "\n".join(lines)


def test_a_class_docstring_goes_with_the_function_ones(tmp_path: Path):
    """La tabla del spec (§4.1) dice «docstrings de función», pero la de clase
    es la misma cosa: explica cómo funciona lo que ya has abierto. La de módulo,
    que dice qué fichero abrir, es B3 y se queda. Hasta ahora borrar
    `leave_ClassDef` dejaba la suite entera en verde, así que la dosis real de
    A4 no estaba fijada por ningún test en ninguna de las dos direcciones."""
    path = write(
        tmp_path,
        '"""Módulo."""\n'
        "\n"
        "\n"
        "class Widget:\n"
        '    """Explica el widget.\n'
        "\n"
        "    >>> Widget().render()\n"
        "    'w'\n"
        '    """\n'
        "\n"
        "    def render(self):\n"
        '        """Explica el render."""\n'
        "        return 'w'\n",
    )

    a4_docs.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "Explica el widget" not in source
    assert "Explica el render" not in source
    assert '"""Módulo."""' in source
    # El doctest de la clase es suite igual que el de una función.
    assert ">>> Widget().render()" in source
    results = run_doctests(path)
    assert (results.attempted, results.failed) == (1, 0)


def test_it_reports_how_many_files_changed(tmp_path: Path):
    write(tmp_path)
    (tmp_path / "pkg" / "sin_docs.py").write_text("x = 1\n", encoding="utf-8")

    result = a4_docs.apply(tmp_path)

    assert result.files_changed == 1
