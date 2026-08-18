from pathlib import Path

from acp.transforms import b2_hierarchy


def build(root: Path) -> None:
    pkg = root / "pkg"
    (pkg / "es").mkdir(parents=True)
    (pkg / "iso").mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "util.py").write_text("def clean(x):\n    return x.strip()\n", encoding="utf-8")
    (pkg / "es" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "es" / "nif.py").write_text(
        "from pkg.util import clean\n"
        "\n"
        "\n"
        "def validate(number):\n"
        "    return clean(number)\n",
        encoding="utf-8",
    )
    (pkg / "iso" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "iso" / "mod97.py").write_text(
        "from pkg.es.nif import validate\n"
        "\n"
        "\n"
        "def check(number):\n"
        "    return validate(number)\n",
        encoding="utf-8",
    )


def test_the_directories_inside_the_package_are_gone(tmp_path: Path):
    build(tmp_path)

    b2_hierarchy.apply(tmp_path)

    assert not (tmp_path / "pkg" / "es").exists()
    assert not (tmp_path / "pkg" / "iso").exists()


def test_the_root_package_survives(tmp_path: Path):
    """Es lo único que mantiene válidos a la vez los imports desde fuera y el
    comando de test (§5.6). Aplanarlo también dejaría el repo sin punto de
    entrada."""
    build(tmp_path)

    b2_hierarchy.apply(tmp_path)

    assert (tmp_path / "pkg" / "__init__.py").exists()


def test_files_are_renamed_to_opaque_names(tmp_path: Path):
    build(tmp_path)

    b2_hierarchy.apply(tmp_path)

    names = sorted(path.name for path in (tmp_path / "pkg").glob("*.py"))
    assert "__init__.py" in names
    assert any(name.startswith("m") and name[1:-3].isdigit() for name in names)
    assert "nif.py" not in names


def test_the_imports_are_rewritten_so_the_code_runs(tmp_path: Path):
    build(tmp_path)

    b2_hierarchy.apply(tmp_path)

    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import pkg; print('ok')"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    modules = list((tmp_path / "pkg").glob("m*.py"))
    joined = "\n".join(path.read_text(encoding="utf-8") for path in modules)
    assert "pkg.es.nif" not in joined
    assert "pkg.util" not in joined


def test_the_moves_travel_with_the_result(tmp_path: Path):
    """El mapa de identidad los necesita para no perder los símbolos (Task 2)."""
    build(tmp_path)

    result = b2_hierarchy.apply(tmp_path)

    assert result.moves["pkg.es.nif"].startswith("pkg.m")
    assert result.moves["pkg.util"].startswith("pkg.m")


# --- Las formas de import que traen los repos reales -------------------------
#
# El fixture de arriba solo tiene `from pkg.x import y`. Los dos finalistas con
# jerarquía traen otras tres formas, y cada una rompe de una manera distinta al
# aplanar: python-stdnum tiene un `import stdnum.bic` sin alias usado después
# como expresión (`stdnum/iso9362.py`), y pint tiene 320 imports relativos, que
# dejan de resolver en cuanto el fichero cambia de profundidad.

FORMS = {
    "pkg/__init__.py": "",
    "pkg/util.py": (
        'SUFFIX = "!"\n\n\ndef clean(x):\n    return x.strip()\n\n\n'
        "def shout(x):\n    return x.upper()\n"
    ),
    "pkg/es/__init__.py": 'COUNTRY = "es"\n',
    "pkg/es/nif.py": (
        "from pkg.util import clean\n\n\ndef validate(number):\n    return clean(number)\n"
    ),
    # `import pkg.es.nif` sin alias: el nombre que queda ligado es `pkg`, y el
    # módulo se usa después por su ruta entera.
    "pkg/plain.py": (
        "import pkg.es.nif\n\n\ndef run(number):\n    return pkg.es.nif.validate(number)\n"
    ),
    "pkg/aliased.py": (
        "import pkg.es.nif as nif\n\n\ndef run(number):\n    return nif.validate(number)\n"
    ),
    # Mezcla a propósito: `nif` es un submódulo y `COUNTRY` un nombre del
    # `__init__`, así que después de aplanar no pueden venir del mismo sitio.
    "pkg/mixed.py": (
        "from pkg.es import nif, COUNTRY\n\n\n"
        "def run(number):\n    return COUNTRY + nif.validate(number)\n"
    ),
    "pkg/deep/__init__.py": "",
    "pkg/deep/inner/__init__.py": "",
    "pkg/deep/inner/tool.py": (
        "from ...util import clean\n"
        "from ... import es\n"
        "from .. import inner\n\n\n"
        "def run(number):\n    return es.COUNTRY + clean(number) + str(inner is not None)\n"
    ),
    "pkg/relative.py": (
        "from .es import nif\n"
        "from . import util\n\n\n"
        "def run(number):\n    return util.clean(nif.validate(number))\n"
    ),
}


def build_forms(root: Path) -> None:
    for relative, source in FORMS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def run_in(root: Path, code: str):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True
    )


def test_a_module_imported_by_its_full_path_still_resolves(tmp_path: Path):
    """`import stdnum.bic` sin alias, tal cual está en python-stdnum: el módulo
    se usa después como `stdnum.bic`, así que reescribir solo la sentencia de
    import deja el uso apuntando a un módulo que ya no existe."""
    build_forms(tmp_path)

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.plain"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "12"


def test_a_module_imported_with_an_alias_still_resolves(tmp_path: Path):
    build_forms(tmp_path)

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.aliased"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "12"


def test_relative_imports_survive_the_change_of_depth(tmp_path: Path):
    """En pint hay 320, y al aplanar todos los ficheros pasan a colgar del
    paquete: un `from ...util import clean` que antes subía tres niveles ahora
    se saldría del paquete. Sin resolverlos a absoluto, B2 no es aplicable al
    único finalista con jerarquía profunda."""
    build_forms(tmp_path)

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.deep.inner.tool"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "es12True"


def test_a_relative_import_of_a_sibling_module_still_resolves(tmp_path: Path):
    build_forms(tmp_path)

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.relative"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "12"


def test_a_submodule_and_a_name_imported_in_the_same_statement(tmp_path: Path):
    """`from pkg.es import nif, COUNTRY`: después de aplanar, `nif` es un módulo
    que cuelga del paquete raíz y `COUNTRY` sigue siendo un nombre del módulo en
    que se convirtió el `__init__`. Ya no pueden venir del mismo sitio."""
    build_forms(tmp_path)

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.mixed"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "es12"


def test_no_module_keeps_its_old_path_anywhere(tmp_path: Path):
    """Si queda una sola ruta vieja, o el repo no arranca o —peor— arranca y la
    dosis de B2 es menor de lo que dice la condición."""
    build_forms(tmp_path)

    b2_hierarchy.apply(tmp_path)

    joined = "\n".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "pkg").rglob("*.py")
    )
    for old in ("pkg.es.nif", "pkg.deep.inner", "pkg.util", "pkg.es "):
        assert old not in joined, old


# --- B2 dentro de la fontanería del experimento -----------------------------


def test_b2_is_registered_so_the_cli_can_apply_it(tmp_path: Path):
    """Sin registrar, la transformación existe pero no hay forma de pedirla: la
    condición T2 no se puede montar y el mapa de identidad nunca ve sus moves."""
    import json

    from acp.cli import manifest_path_for, transform_repo

    source = tmp_path / "repo"
    build(source)

    destination = transform_repo(source, ["B2"], tmp_path / "work")

    manifest = json.loads(manifest_path_for(destination).read_text(encoding="utf-8"))
    located = manifest["symbols"]["pkg.es.nif.validate"]
    assert located["path"].startswith("pkg/m")
    assert located["current_name"] == "validate"


def test_b2_runs_before_b4_whatever_the_order_asked(tmp_path: Path):
    """B4 se lleva la suite fuera del árbol. Si corre antes que B2, esos tests se
    quedan importando rutas que B2 va a borrar después, y la corrida de
    validación —que sí los ejecuta— sale en rojo: se leería como un agente que
    rompió el repo cuando lo que falló es el orden de aplicación."""
    from acp.cli import transform_repo
    from acp.transforms import b4_tests

    source = tmp_path / "repo"
    build(source)
    (source / "tests").mkdir()
    (source / "tests" / "test_nif.py").write_text(
        "from pkg.es.nif import validate\n\n\ndef test_it():\n    assert validate(' 1 ') == '1'\n",
        encoding="utf-8",
    )

    destination = transform_repo(source, ["B4", "B2"], tmp_path / "work")

    kept = b4_tests.kept_suite_path(destination) / "tests" / "test_nif.py"
    assert "pkg.es.nif" not in kept.read_text(encoding="utf-8")


# --- Los doctests son suite, no prosa ---------------------------------------
#
# python-stdnum corre `--doctest-modules --doctest-glob="*.doctest"`: 413 de sus
# tests viven en ficheros `.doctest` y en docstrings, y sus ejemplos importan por
# ruta de módulo. Medido sobre el clon real, aplanar sin tocarlos deja 234 líneas
# de ejemplo importando rutas que ya no existen.

SETUP_CFG = '[tool:pytest]\naddopts = --doctest-modules --doctest-glob="*.doctest" pkg tests\n'

DOCTEST_FILE = """\
Comprobación del NIF.

>>> from pkg.es.nif import validate
>>> validate(' 12 ')
'12'
"""


def build_with_doctests(root: Path) -> None:
    build_forms(root)
    (root / "setup.cfg").write_text(SETUP_CFG, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "nif.doctest").write_text(DOCTEST_FILE, encoding="utf-8")
    (root / "pkg" / "__init__.py").write_text(
        '"""El paquete.\n\n>>> from pkg import util\n>>> util.clean(\' 12 \')\n\'12\'\n"""\n',
        encoding="utf-8",
    )


def test_a_doctest_file_keeps_importing_something_that_exists(tmp_path: Path):
    build_with_doctests(tmp_path)

    b2_hierarchy.apply(tmp_path)

    ran = run_in(
        tmp_path,
        "import doctest, sys;"
        "r = doctest.testfile('tests/nif.doctest', module_relative=False);"
        "sys.exit(r.failed)",
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr


def test_a_doctest_inside_a_docstring_keeps_importing_something_that_exists(tmp_path: Path):
    """El `__init__` del paquete raíz no se mueve, pero sus ejemplos importan
    módulos que sí: es el caso exacto de `stdnum/__init__.py`."""
    build_with_doctests(tmp_path)

    b2_hierarchy.apply(tmp_path)

    ran = run_in(
        tmp_path,
        "import doctest, sys, pkg;"
        "r = doctest.testmod(pkg);"
        "sys.exit(r.failed or (r.attempted == 0))",
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr


def test_a_module_path_written_as_a_string_follows_the_move(tmp_path: Path):
    """`stdnum/gs1_128.py` guarda rutas de módulo en un diccionario y las importa
    con `__import__(_ai_validators[ai])`. La cadena resuelve estáticamente —es
    exactamente el nombre de un módulo del repo—, así que §4.3.3 no la excluye:
    excluye lo *indecidible*, y dejarla atrás deja al importador sin el módulo.
    Es el mismo criterio con el que A2 sigue las cadenas de `__all__`.
    """
    build_forms(tmp_path)
    (tmp_path / "pkg" / "byname.py").write_text(
        "VALIDATORS = {'01': 'pkg.es.nif'}\n\n\n"
        "def run(number):\n"
        "    mod = __import__(VALIDATORS['01'], globals(), locals(), ['validate'])\n"
        "    return mod.validate(number)\n",
        encoding="utf-8",
    )

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.byname"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "12"


def test_prose_that_merely_mentions_a_module_is_left_alone(tmp_path: Path):
    """La cadena tiene que ser el nombre del módulo y nada más. Una frase que lo
    menciona es documentación, y reescribirla sería B3 colándose dentro de B2."""
    build_forms(tmp_path)
    (tmp_path / "pkg" / "prose.py").write_text(
        "MESSAGE = 'use pkg.es.nif instead'\n", encoding="utf-8"
    )

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.prose"].split(".")[-1]

    kept = (tmp_path / "pkg" / f"{target}.py").read_text(encoding="utf-8")
    assert "use pkg.es.nif instead" in kept


def test_a_test_configuration_that_names_a_file_follows_the_move(tmp_path: Path):
    """python-stdnum ignora un fichero por ruta (`--ignore=stdnum/iso9362.py`,
    que se sustituye a sí mismo en `sys.modules`). Al aplanar, esa ruta deja de
    existir, el `--ignore` no tapa nada, pytest lo colecta y la corrida entera
    muere en la colecta: 413 tests pasan a 0 sin que falle un solo test.

    Medido sobre el clon real: es el único fallo que B2 tenía contra
    python-stdnum, y ningún fixture pequeño lo enseña.
    """
    import subprocess
    import sys

    (tmp_path / "setup.cfg").write_text(
        "[tool:pytest]\naddopts = --doctest-modules --ignore=pkg/broken.py pkg tests\n",
        encoding="utf-8",
    )
    build_forms(tmp_path)
    (tmp_path / "pkg" / "broken.py").write_text(
        "raise ImportError('a este no hay que colectarlo')\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    b2_hierarchy.apply(tmp_path)

    ran = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert ran.returncode == 0, ran.stdout[-2000:]


MULTILINE_DOCTEST = """\
Utilidades.

>>> from pkg.util import (
...     clean, shout,
...     SUFFIX)
>>> clean(' 12 ') + SUFFIX + shout('x')
'12!X'
"""


def test_a_multi_line_import_inside_a_doctest_is_rewritten(tmp_path: Path):
    """`tests/test_util.doctest` de python-stdnum importa seis nombres repartidos
    en tres líneas. Rehacer la lista de nombres los junta en una, el ejemplo
    cambia de número de líneas y la red de seguridad lo deja sin tocar: el
    doctest se queda importando un módulo que ya no existe, en silencio.
    """
    build_with_doctests(tmp_path)
    (tmp_path / "tests" / "util.doctest").write_text(MULTILINE_DOCTEST, encoding="utf-8")

    b2_hierarchy.apply(tmp_path)

    ran = run_in(
        tmp_path,
        "import doctest, sys;"
        "r = doctest.testfile('tests/util.doctest', module_relative=False);"
        "sys.exit(r.failed)",
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr


# --- Lo que el repo alcanza por un nombre construido en ejecución ------------


def test_a_package_reached_by_a_computed_module_name_is_not_flattened(tmp_path: Path):
    """python-stdnum despacha por código de país con
    `__import__('stdnum.%s' % cc, ..., [name])`: la jerarquía de directorios *es*
    su tabla de búsqueda. Aplanarla deja 10 tests en rojo, y no hay reescritura
    posible porque el nombre no existe hasta que corre.

    Es el mismo criterio de §4.3.3 que ya excluye de A2 los símbolos que el repo
    alcanza por cadena, y la política que la fase 1 dejó escrita: la verificación
    de equivalencia lo dice, se saca del diccionario lo que rompa y se declara la
    dosis real. Aquí lo que rompe es todo el paquete, así que B2 no se aplica y
    la celda se declara no aplicable a ese repo.
    """
    build_forms(tmp_path)
    (tmp_path / "pkg" / "dispatch.py").write_text(
        "def load(cc):\n"
        "    return __import__('pkg.%s' % cc, globals(), locals(), ['nif'])\n",
        encoding="utf-8",
    )

    result = b2_hierarchy.apply(tmp_path)

    assert result.moves == {}
    assert (tmp_path / "pkg" / "es" / "nif.py").exists()


def test_a_computed_import_of_someone_elses_package_changes_nothing(tmp_path: Path):
    """pint importa clases de terceros por nombre (`import_module(module_name)`,
    `import_module("dask.array")`). Ninguna de las dos formas dice nada sobre los
    módulos del repo, y tratarlas como si lo dijeran dejaría a B2 sin aplicar en
    el único finalista con jerarquía profunda."""
    build_forms(tmp_path)
    (tmp_path / "pkg" / "third.py").write_text(
        "from importlib import import_module\n\n\n"
        "def load(name):\n"
        "    import_module('numpy.%s' % name)\n"
        "    return import_module(name)\n",
        encoding="utf-8",
    )

    result = b2_hierarchy.apply(tmp_path)

    assert result.moves["pkg.es.nif"].startswith("pkg.m")


def test_a_package_that_imports_its_own_submodules_by_name_stays(tmp_path: Path):
    """La forma de sqlglot, y lo que costó: 1.225 tests a 0 en el contenedor.

    `sqlglot/optimizer/__init__.py` resuelve sus submódulos con
    `importlib.import_module(f"{__name__}.{name}")`. Movido a `sqlglot/m66.py`,
    `__name__` pasa a ser `sqlglot.m66`, el submódulo que construye no existe, y
    el `__getattr__` que lo intenta se llama a sí mismo: RecursionError en la
    colecta y la suite entera a cero.

    Un hueco que empieza por `__name__` no es "podría ser cualquier módulo",
    que es lo que dice un hueco cualquiera: es exactamente este módulo hablando
    de sus propios hijos, la evidencia más fuerte que hay de que aquí el árbol
    de directorios es la tabla de búsqueda.
    """
    build_forms(tmp_path)
    (tmp_path / "pkg" / "opt").mkdir()
    (tmp_path / "pkg" / "opt" / "__init__.py").write_text(
        "import importlib\n"
        "\n"
        "\n"
        "def __getattr__(name):\n"
        "    return importlib.import_module(f'{__name__}.{name}')\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "opt" / "qualify.py").write_text("VALUE = 1\n", encoding="utf-8")

    assert b2_hierarchy.computed_module_prefixes(tmp_path) == {"pkg.opt."}
    result = b2_hierarchy.apply(tmp_path)

    assert "pkg.opt" not in result.moves
    assert "pkg.opt.qualify" not in result.moves
    assert (tmp_path / "pkg" / "opt" / "qualify.py").exists()


def test_the_package_a_computed_name_hangs_from_does_not_move_either(tmp_path: Path):
    """Mover el `__init__.py` de un paquete cuyos hijos se quedan lo deshace.

    `sqlglot/dialects/__init__.py` construye `f"sqlglot.dialects.{name}"`, así
    que sus hijos ya estaban protegidos; el fichero que los hace paquete, no.
    Llevárselo a la raíz deja `sqlglot/dialects/` sin `__init__.py` —un paquete
    de espacio de nombres— y sin nada de lo que ese fichero definía, mientras la
    ruta que la cadena construye sigue apuntando ahí.
    """
    build_forms(tmp_path)
    (tmp_path / "pkg" / "dial").mkdir()
    (tmp_path / "pkg" / "dial" / "__init__.py").write_text(
        "import importlib\n"
        "\n"
        "\n"
        "def load(name):\n"
        "    return importlib.import_module(f'pkg.dial.{name}')\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "dial" / "bigquery.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = b2_hierarchy.apply(tmp_path)

    assert "pkg.dial" not in result.moves
    assert (tmp_path / "pkg" / "dial" / "__init__.py").exists()
    assert (tmp_path / "pkg" / "dial" / "bigquery.py").exists()


def test_a_module_whose_path_the_suite_pins_as_text_does_not_move(tmp_path: Path):
    """La suite es el oráculo, y aquí escribe una ruta de módulo dentro de una frase.

    sqlglot compara mensajes de error que llevan dentro el `repr` de una clase:
    `"Failed to parse ... into <class 'sqlglot.expressions.query.Table'>"`. Ese
    texto no es una ruta de módulo —es una frase que contiene una—, así que la
    reescritura de cadenas no lo toca y no debe tocarlo; pero al mover el módulo
    el `__module__` de la clase cambia, el mensaje que genera el programa deja de
    coincidir con el que la suite espera y salen 7 tests en rojo donde el
    baseline no tenía ninguno. Medido en contenedor.

    Es el mismo criterio de §4.3.3 que ya deja quietos los módulos que se
    localizan por `__file__`: lo que no se puede mover sin cambiar el veredicto
    se saca del diccionario y se declara la dosis.
    """
    build(tmp_path)
    (tmp_path / "pkg" / "es" / "nif.py").write_text(
        "class Number:\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_repr.py").write_text(
        "from pkg.es.nif import Number\n"
        "\n"
        "\n"
        "def test_repr():\n"
        "    assert repr(Number) == \"<class 'pkg.es.nif.Number'>\"\n",
        encoding="utf-8",
    )

    result = b2_hierarchy.apply(tmp_path)

    assert "pkg.es.nif" not in result.moves
    assert (tmp_path / "pkg" / "es" / "nif.py").exists()


def test_a_module_the_source_merely_mentions_in_text_still_moves(tmp_path: Path):
    """El límite del guardarraíl anterior, y por qué está donde está.

    Una frase del código fuente que nombra un módulo no la compara nadie: pint
    escribe rutas de módulo dentro de textos en 57 de sus 67 módulos movibles, y
    tratar eso como una atadura dejaría su celda —la única con jerarquía
    profunda— en dosis casi cero por una prosa que no decide nada. Lo que ata es
    que lo escriba la suite, que es quien compara.
    """
    build(tmp_path)
    (tmp_path / "pkg" / "doc.py").write_text(
        'HELP = "el validador vive en pkg.es.nif y se usa asi"\n', encoding="utf-8"
    )

    result = b2_hierarchy.apply(tmp_path)

    assert result.moves["pkg.es.nif"].startswith("pkg.m")


def test_the_reason_a_repo_is_not_flattened_can_be_asked(tmp_path: Path):
    """La dosis real se declara con datos, no se deduce de un contador a cero:
    un `moves` vacío tiene dos causas distintas y hay que poder distinguirlas."""
    build_forms(tmp_path)
    (tmp_path / "pkg" / "dispatch.py").write_text(
        "def load(cc):\n    return __import__('pkg.%s' % cc)\n", encoding="utf-8"
    )

    assert b2_hierarchy.computed_module_prefixes(tmp_path) == {"pkg."}


# --- Lo que pytest resuelve por nombre y por sitio ---------------------------
#
# pint tiene su suite DENTRO del paquete: 35 ficheros `test_*.py` y dos
# `conftest.py`. Aplanar sin mirar eso los renombra a `mN.py`, pytest deja de
# colectarlos y la suite entera pasa a cero tests sin que falle ninguno. Es el
# mismo criterio con el que A2 no renombra funciones de test: pytest colecta por
# nombre, así que ahí el nombre es comportamiento, no documentación.


def build_suite_inside_package(root: Path) -> None:
    build_forms(root)
    inner = root / "pkg" / "testsuite"
    inner.mkdir()
    (inner / "__init__.py").write_text("", encoding="utf-8")
    (inner / "conftest.py").write_text(
        "import pytest\n\n\n@pytest.fixture\ndef number():\n    return ' 12 '\n",
        encoding="utf-8",
    )
    (inner / "test_nif.py").write_text(
        "from pkg.es.nif import validate\n\n\n"
        "def test_it(number):\n    assert validate(number) == '12'\n",
        encoding="utf-8",
    )


def run_pytest(root: Path):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True,
    )


def test_a_suite_that_lives_inside_the_package_is_still_collected(tmp_path: Path):
    build_suite_inside_package(tmp_path)
    before = run_pytest(tmp_path)
    assert "1 passed" in before.stdout, before.stdout[-1500:]

    b2_hierarchy.apply(tmp_path)

    after = run_pytest(tmp_path)
    assert "1 passed" in after.stdout, after.stdout[-2000:]


def test_a_directory_that_scopes_a_conftest_is_not_flattened(tmp_path: Path):
    """Un `conftest.py` no es un módulo cualquiera: pytest lo busca por nombre
    exacto y su directorio es el alcance de sus fixtures. Moverlo cambia qué
    tests lo ven; renombrarlo lo hace invisible. Las dos cosas dejan la suite en
    rojo por fontanería, no por la transformación."""
    build_suite_inside_package(tmp_path)

    result = b2_hierarchy.apply(tmp_path)

    assert (tmp_path / "pkg" / "testsuite" / "conftest.py").exists()
    assert "pkg.testsuite.conftest" not in result.moves
    # Y el código de verdad sí se aplana: la excepción es del alcance de pytest,
    # no una puerta abierta para todo el paquete.
    assert result.moves["pkg.es.nif"].startswith("pkg.m")


def test_a_test_file_without_a_conftest_beside_it_stays_collectable(tmp_path: Path):
    """Sin `conftest.py` que declare alcance, el fichero sí se aplana —y ahí es
    donde tiene que conservar el prefijo por el que pytest lo colecta—. Si no,
    la suite no falla: desaparece."""
    build_forms(tmp_path)
    inner = tmp_path / "pkg" / "checks"
    inner.mkdir()
    (inner / "__init__.py").write_text("", encoding="utf-8")
    (inner / "test_nif.py").write_text(
        "from pkg.es.nif import validate\n\n\n"
        "def test_it():\n    assert validate(' 12 ') == '12'\n",
        encoding="utf-8",
    )

    result = b2_hierarchy.apply(tmp_path)

    assert result.moves["pkg.checks.test_nif"].startswith("pkg.test_m")
    after = run_pytest(tmp_path)
    assert "1 passed" in after.stdout, after.stdout[-2000:]


def test_a_module_that_locates_data_from_its_own_file_does_not_move(tmp_path: Path):
    """pint carga su registro de unidades con
    `Path(__file__).parent.parent.parent / "default_en.txt"`: la profundidad del
    fichero es parte del programa. Movido a la raíz del paquete, esa cuenta de
    saltos se sale del árbol y busca en `/default_en.txt`. Medido sobre el clon
    real: 2.024 tests pasan a 623, con 1.289 errores, todos del mismo sitio.

    Un módulo así no se mueve. Es lo mismo que hace §4.3.3 con lo que no resuelve
    estáticamente: aquí la ruta sí se lee, pero depende de dónde está el fichero,
    y eso es exactamente lo que B2 cambia.
    """
    build_forms(tmp_path)
    (tmp_path / "pkg" / "data.txt").write_text("hola\n", encoding="utf-8")
    inner = tmp_path / "pkg" / "deep" / "inner"
    (inner / "loader.py").write_text(
        "from pathlib import Path\n\n\n"
        "def data():\n"
        "    return (Path(__file__).parent.parent.parent / 'data.txt').read_text().strip()\n",
        encoding="utf-8",
    )

    result = b2_hierarchy.apply(tmp_path)

    assert "pkg.deep.inner.loader" not in result.moves
    ran = run_in(tmp_path, "from pkg.deep.inner.loader import data; print(data())")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "hola"


def test_a_stationary_submodule_is_still_reached_through_its_own_path(tmp_path: Path):
    """Un módulo que no se mueve —porque se localiza a sí mismo— sigue colgando
    de su directorio, no del paquete raíz. Su padre sí se mueve, así que rebasar
    el import al destino del padre lo manda a buscar un submódulo dentro de un
    fichero plano. No pasa en pint ni en python-stdnum; pasaría en cuanto un repo
    mezcle las dos cosas, y en silencio."""
    build_forms(tmp_path)
    (tmp_path / "pkg" / "data.txt").write_text("hola\n", encoding="utf-8")
    inner = tmp_path / "pkg" / "deep" / "inner"
    (inner / "loader.py").write_text(
        "from pathlib import Path\n\n\n"
        "def data():\n"
        "    return (Path(__file__).parent.parent.parent / 'data.txt').read_text().strip()\n",
        encoding="utf-8",
    )
    (inner / "__init__.py").write_text(
        "from . import loader\n\n\ndef run():\n    return loader.data()\n", encoding="utf-8"
    )

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.deep.inner"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run())")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "hola"


def test_a_patch_target_naming_a_module_attribute_follows_the_move(tmp_path: Path):
    """pint parchea con `@patch("pint.compat.upcast_type_names", ...)`. La cadena
    no es el nombre de un módulo —es módulo más atributo—, así que la
    coincidencia exacta no la ve, y `mock` la resuelve importando el módulo: sin
    reescribir, dos tests fallan con `module 'pint' has no attribute 'compat'`.

    Es una cadena con puntos que resuelve estáticamente, del mismo tipo que las
    de `__all__` que ya sigue A2. La fase 1 la dejó como límite conocido porque
    no aparecía en ningún finalista; con B2 sí aparece.
    """
    build_forms(tmp_path)
    (tmp_path / "pkg" / "patcher.py").write_text(
        "from unittest.mock import patch\n\n\n"
        "def run():\n"
        "    with patch('pkg.util.SUFFIX', '?'):\n"
        "        from pkg import util\n"
        "        return util.SUFFIX\n",
        encoding="utf-8",
    )

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.patcher"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run())")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "?"


def test_a_dotted_sentence_is_not_mistaken_for_a_module_path(tmp_path: Path):
    build_forms(tmp_path)
    (tmp_path / "pkg" / "prose2.py").write_text(
        "MESSAGE = 'pkg.util is gone. use something else'\n", encoding="utf-8"
    )

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.prose2"].split(".")[-1]

    kept = (tmp_path / "pkg" / f"{target}.py").read_text(encoding="utf-8")
    assert "pkg.util is gone" in kept


# --- Lo que declara el empaquetado ------------------------------------------
#
# `_rewrite_configured_paths` ya sigue las rutas de *fichero* que nombra la
# configuración. Un entry point nombra lo mismo en la otra forma —módulo con
# puntos— y vive en los mismos ficheros, así que dejarlo atrás rompe la interfaz
# pública del repo sin que ningún test lo note.


def test_a_console_script_still_names_a_module_that_exists(tmp_path: Path):
    """pint declara `pint-convert = "pint.pint_convert:main"` y B2 mueve ese
    módulo a `pint/m61.py`. Medido sobre el clon: `pip install -e .` va bien y
    `pint-convert 1m` muere con `ModuleNotFoundError: No module named
    'pint.pint_convert'`. La suite no ejecuta entry points, así que la celda se
    lee 3/3 en verde con el CLI del repo roto.
    """
    import tomllib

    build(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0.1.0"\n\n'
        '[project.scripts]\npkg-check = "pkg.es.nif:validate"\n',
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    value = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    target = value["project"]["scripts"]["pkg-check"]
    module, _, attribute = target.partition(":")
    # Exactamente lo que hace el script que escribe pip.
    ran = run_in(tmp_path, f"from {module} import {attribute}\nprint({attribute}(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "12"


def test_an_entry_point_group_follows_the_move_too(tmp_path: Path):
    """Los grupos arbitrarios (`pytest11`, `console_scripts` de un plugin) se
    resuelven igual que un script y se declaran en la misma tabla."""
    import tomllib

    build(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0.1.0"\n\n'
        '[project.entry-points."pkg.plugins"]\nnif = "pkg.es.nif"\n',
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    data = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    module = data["project"]["entry-points"]["pkg.plugins"]["nif"]
    ran = run_in(tmp_path, f"import importlib; importlib.import_module('{module}')")
    assert ran.returncode == 0, ran.stderr


def test_an_entry_point_declared_in_setup_cfg_follows_the_move(tmp_path: Path):
    """La otra forma de declararlo. Es el mismo fichero que ya se reescribe por
    rutas, y ahí las dos formas conviven."""
    build(tmp_path)
    (tmp_path / "setup.cfg").write_text(
        "[metadata]\nname = pkg\n\n"
        "[options.entry_points]\nconsole_scripts =\n    pkg-check = pkg.es.nif:validate\n",
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    text = (tmp_path / "setup.cfg").read_text(encoding="utf-8")
    assert "pkg.es.nif" not in text
    module = text.split("pkg-check = ")[1].split(":")[0].strip()
    ran = run_in(tmp_path, f"from {module} import validate\nprint(validate(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "12"


def test_prose_in_the_packaging_file_is_left_alone(tmp_path: Path):
    """Solo se sigue lo que está declarado como entry point. La descripción del
    proyecto puede mencionar un módulo, y reescribirla sería B3 dentro de B2."""
    build(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0.1.0"\n'
        'description = "validate with pkg.es.nif"\n\n'
        '[project.scripts]\npkg-check = "pkg.es.nif:validate"\n',
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'description = "validate with pkg.es.nif"' in text


def test_prose_that_repeats_the_entry_point_verbatim_is_left_alone(tmp_path: Path):
    """El caso que la prueba de arriba no llega a tocar: la prosa no menciona el
    módulo, repite el valor **entero** del entry point. Sustituirlo con un
    `replace` sobre el fichero entero reescribe las dos apariciones, o sea que la
    promesa de tocar solo lo declarado depende de que nadie escriba en la
    descripción lo mismo que en `[project.scripts]`.
    """
    build(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0.1.0"\n'
        'description = "run pkg.es.nif:validate to check a number"\n\n'
        '[project.scripts]\npkg-check = "pkg.es.nif:validate"\n',
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'description = "run pkg.es.nif:validate to check a number"' in text
    # Y lo declarado sí se sigue, que es lo que la prosa no debe arrastrar.
    assert 'pkg-check = "pkg.es.nif:validate"' not in text


def test_prose_in_setup_cfg_that_repeats_an_entry_point_is_left_alone(tmp_path: Path):
    """La otra forma de declararlo, con el mismo agujero: `description` vive en
    `[metadata]` y el `replace` no distingue secciones."""
    build(tmp_path)
    (tmp_path / "setup.cfg").write_text(
        "[metadata]\nname = pkg\n"
        "description = run pkg.es.nif:validate to check a number\n\n"
        "[options.entry_points]\nconsole_scripts =\n"
        "    pkg-check = pkg.es.nif:validate\n",
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    text = (tmp_path / "setup.cfg").read_text(encoding="utf-8")
    assert "description = run pkg.es.nif:validate to check a number" in text
    assert "    pkg-check = pkg.es.nif:validate\n" not in text


def test_a_console_script_declared_in_setup_py_still_names_a_module(tmp_path: Path):
    """El tercer sitio donde se declara lo mismo, y el más antiguo: `setup.py`
    pasa los entry points como argumento de `setup()`. Medido sobre un fixture
    con `entry_points={'console_scripts': [...]}`: `pip install -e .` va bien
    —el fichero se instala igual— y el script instalado muere con
    `ModuleNotFoundError` porque su `from pkg.cli import main` apunta al módulo
    de antes. Es el mismo modo de fallo que ya se tapó para `pyproject.toml`,
    invisible por la misma razón: la suite no ejecuta entry points.
    """
    build(tmp_path)
    (tmp_path / "setup.py").write_text(
        "from setuptools import find_packages, setup\n"
        "\n"
        "setup(\n"
        "    name='pkg',\n"
        "    version='0.1.0',\n"
        "    packages=find_packages(),\n"
        "    entry_points={'console_scripts': ['pkg-check = pkg.es.nif:validate']},\n"
        ")\n",
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    text = (tmp_path / "setup.py").read_text(encoding="utf-8")
    assert "pkg.es.nif" not in text
    module = text.split("pkg-check = ")[1].split(":")[0].strip()
    ran = run_in(tmp_path, f"from {module} import validate\nprint(validate(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "12"


def test_an_entry_point_block_written_as_one_string_follows_the_move(tmp_path: Path):
    """La otra escritura del mismo argumento: el bloque con formato ini, tal cual
    se hereda de `setup.cfg`, en una sola cadena. Las líneas van dentro del
    literal, así que encontrarlas exige partir también por el `\\n` escrito: se
    declaran dos scripts a propósito, porque con uno solo el último `=` de la
    cadena es siempre el bueno y la partición no haría falta."""
    build(tmp_path)
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\n"
        "\n"
        "setup(\n"
        "    name='pkg',\n"
        "    version='0.1.0',\n"
        "    entry_points='[console_scripts]\\n"
        "pkg-check = pkg.es.nif:validate\\n"
        "pkg-mod97 = pkg.iso.mod97:check\\n',\n"
        ")\n",
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    text = (tmp_path / "setup.py").read_text(encoding="utf-8")
    for script, attribute in (("pkg-check", "validate"), ("pkg-mod97", "check")):
        module = text.split(f"{script} = ")[1].split(":")[0].strip()
        ran = run_in(tmp_path, f"from {module} import {attribute}\nprint({attribute}(' 12 '))")
        assert ran.returncode == 0, ran.stderr
        assert ran.stdout.strip() == "12"


def test_prose_in_setup_py_is_not_an_entry_point(tmp_path: Path):
    """Aquí no hay parser que declare valores, así que lo declarado se decide por
    posición: dentro del argumento `entry_points` y en ningún otro sitio. El
    argumento de al lado repite la misma cadena dentro de una frase."""
    build(tmp_path)
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\n"
        "\n"
        "setup(\n"
        "    name='pkg',\n"
        "    version='0.1.0',\n"
        "    description='run pkg-check = pkg.es.nif:validate to check a number',\n"
        "    entry_points={'console_scripts': ['pkg-check = pkg.es.nif:validate']},\n"
        ")\n",
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    text = (tmp_path / "setup.py").read_text(encoding="utf-8")
    assert "description='run pkg-check = pkg.es.nif:validate to check a number'" in text


# --- La otra cosa que el empaquetado nombra con puntos ----------------------
#
# Un entry point no es lo único: la lista estática de paquetes también nombra
# módulos con puntos, y aplanar deja ahí subpaquetes que ya no existen. El
# fallo es más temprano y más tonto que el del entry point —el árbol
# transformado ni siquiera se instala—, así que se lleva por delante el arreglo
# de los entry points en los repos que la usan.


def declared_packages_exist(root: Path, names) -> None:
    for name in names:
        assert (root / Path(*name.split("."))).is_dir(), name


def test_the_static_package_list_in_pyproject_only_names_what_exists(tmp_path: Path):
    """Medido con pip sobre el fixture: `pip install -e . --no-build-isolation`
    muere con `error: package directory 'pkg/es' does not exist` y
    `metadata-generation-failed`. La matriz de equivalencia corre B2 con
    `install_repo=False`, así que la celda no lo vería nunca.
    """
    import tomllib

    build(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0.1.0"\n\n'
        '[tool.setuptools]\npackages = ["pkg", "pkg.es", "pkg.iso"]\n',
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    declared = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    names = declared["tool"]["setuptools"]["packages"]
    declared_packages_exist(tmp_path, names)
    # Y el paquete raíz sigue declarado: la lista se poda, no se vacía.
    assert names == ["pkg"]


def test_the_static_package_list_in_setup_cfg_only_names_what_exists(tmp_path: Path):
    """La misma lista en la otra forma, con los nombres separados por comas."""
    build(tmp_path)
    (tmp_path / "setup.cfg").write_text(
        "[metadata]\nname = pkg\nversion = 0.1.0\n\n"
        "[options]\npackages = pkg, pkg.es, pkg.iso\n",
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    text = (tmp_path / "setup.cfg").read_text(encoding="utf-8")
    names = [name.strip() for name in text.split("packages =")[1].split("\n")[0].split(",")]
    declared_packages_exist(tmp_path, [name for name in names if name])
    assert [name for name in names if name] == ["pkg"]


def test_a_package_list_written_one_per_line_survives_the_pruning(tmp_path: Path):
    """La escritura multilínea de `setup.cfg`: el valor sigue siendo una lista,
    y podarla no puede convertirla en otra cosa."""
    import configparser

    build(tmp_path)
    (tmp_path / "setup.cfg").write_text(
        "[metadata]\nname = pkg\nversion = 0.1.0\n\n"
        "[options]\npackages =\n    pkg\n    pkg.es\n    pkg.iso\n"
        "install_requires =\n    tomli\n",
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string((tmp_path / "setup.cfg").read_text(encoding="utf-8"))
    names = [name for name in parser["options"]["packages"].split("\n") if name.strip()]
    declared_packages_exist(tmp_path, [name.strip() for name in names])
    assert [name.strip() for name in names] == ["pkg"]
    # Lo de al lado no se toca: la clave siguiente sigue entera.
    assert parser["options"]["install_requires"].split() == ["tomli"]
    # Y sigue escrita una por línea: la dosis de B2 es la jerarquía, no el
    # formato del fichero de empaquetado.
    assert "\npackages =\n    pkg\n" in (tmp_path / "setup.cfg").read_text(encoding="utf-8")


def test_the_package_list_in_setup_py_only_names_what_exists(tmp_path: Path):
    """En `setup.py` la lista no se queda obsoleta: la reescritura de cadenas la
    sigue, y `packages=['pkg', 'pkg.m0']` nombra un módulo como si fuera un
    directorio. Medido con pip: `error: package directory 'pkg/m0' does not
    exist`. Roto igual, y por el mismo sitio."""
    import ast

    build(tmp_path)
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\n"
        "\n"
        "setup(\n"
        "    name='pkg',\n"
        "    version='0.1.0',\n"
        "    packages=['pkg', 'pkg.es', 'pkg.iso'],\n"
        ")\n",
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    tree = ast.parse((tmp_path / "setup.py").read_text(encoding="utf-8"))
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    names = next(
        ast.literal_eval(keyword.value) for keyword in call.keywords if keyword.arg == "packages"
    )
    declared_packages_exist(tmp_path, names)
    assert names == ["pkg"]


def test_a_package_list_that_is_a_directive_is_left_alone(tmp_path: Path):
    """`packages = find:` no nombra nada: lo resuelve setuptools al construir, y
    después de aplanar sigue resolviendo bien. Podarlo sería quitarle al repo la
    única declaración que tiene."""
    build(tmp_path)
    (tmp_path / "setup.cfg").write_text(
        "[metadata]\nname = pkg\nversion = 0.1.0\n\n[options]\npackages = find:\n",
        encoding="utf-8",
    )

    b2_hierarchy.apply(tmp_path)

    text = (tmp_path / "setup.cfg").read_text(encoding="utf-8")
    assert "packages = find:" in text


# --- La dosis en cero, en silencio ------------------------------------------
#
# La limpieza final solo borra los directorios que quedan **vacíos**, así que
# cualquier cosa que sobreviva dentro los mantiene vivos. El bytecode compilado
# es exactamente eso, y encima está nombrado por el módulo de antes.


def compile_tree(root: Path) -> None:
    import compileall

    compileall.compile_dir(str(root), quiet=2)


def test_a_bytecode_cache_does_not_keep_the_original_hierarchy_alive(tmp_path: Path):
    """Con un `__pycache__` dentro, ningún directorio queda vacío y B2 no aplana
    nada: la jerarquía entera sobrevive y la celda mide cero en verde.

    Medido sobre el clon de pint: pristino sobreviven 5 directorios, y tras un
    `compileall` sobreviven 15 —`pint/delegates`, `pint/facets/numpy`,
    `pint/facets/context`...—, doce de ellos sin un solo .py dentro.
    """
    build(tmp_path)
    compile_tree(tmp_path)

    b2_hierarchy.apply(tmp_path)

    assert not (tmp_path / "pkg" / "es").exists()
    assert not (tmp_path / "pkg" / "iso").exists()


def test_no_stale_bytecode_keeps_naming_a_module_that_moved(tmp_path: Path):
    """No es solo cuestión de directorios vacíos: `pint/__pycache__` cuelga del
    paquete raíz, que sobrevive por diseño (§5.6), y dentro guarda
    `pint_convert.cpython-312.pyc`, `registry_helpers.cpython-312.pyc`... o sea
    los nombres de módulo que B2 acaba de destruir, en un directorio que ningún
    borrado de vacíos alcanza. Un `ls -R` los recupera enteros.
    """
    build(tmp_path)
    compile_tree(tmp_path)

    b2_hierarchy.apply(tmp_path)

    names = [path.name for path in (tmp_path / "pkg").rglob("*") if path.is_file()]
    assert not [name for name in names if name.endswith((".pyc", ".pyo"))], names
    assert not [name for name in names if name.startswith(("nif", "util", "mod97"))], names


def test_the_flattened_package_still_imports_after_the_cache_is_dropped(tmp_path: Path):
    """Borrar el bytecode no puede romper nada: Python solo usa un `.pyc` de
    `__pycache__` si el `.py` sigue al lado, y el de los módulos movidos ya no
    lo está."""
    build(tmp_path)
    compile_tree(tmp_path)

    b2_hierarchy.apply(tmp_path)

    ran = run_in(tmp_path, "import pkg; print('ok')")
    assert ran.returncode == 0, ran.stderr


def test_a_directory_with_data_files_still_survives(tmp_path: Path):
    """El borrado sigue siendo solo de vacíos: quien abre un fichero de datos lo
    hace por ruta, y llevarse su directorio rompería el repo. Lo que cambia es
    que el bytecode ya no cuenta como contenido."""
    build(tmp_path)
    (tmp_path / "pkg" / "iso" / "table.dat").write_text("1\n", encoding="utf-8")
    compile_tree(tmp_path)

    b2_hierarchy.apply(tmp_path)

    assert (tmp_path / "pkg" / "iso" / "table.dat").exists()


# --- Cuál de los directorios de primer nivel es el paquete -------------------
#
# Medido sobre el sustrato: sqlglot tiene `benchmarks/`, `sqlglot/` y `tests/`
# con `__init__.py`, y holidays tiene `holidays/`, `scripts/` y `tests/`. Con la
# regla de "exactamente uno" B2 no se aplicaba en ninguno de los dos y la fila
# entera de la campaña medía cero. La suite y las utilidades del repositorio no
# son candidatas a paquete raíz, y quien ya sabe cuáles son es
# `acp.metrics.size`, que las excluye de todas las métricas de fase 0.


def test_the_suite_beside_the_package_is_not_a_second_candidate(tmp_path: Path):
    """La forma de sqlglot y holidays: `tests/` es un paquete importable.

    Contarlo como candidato deja el repo sin paquete raíz claro y B2 se vuelve
    un no-op silencioso: árbol idéntico, celda en verde y dosis cero.
    """
    build(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_nif.py").write_text(
        "from pkg.es.nif import validate\n\n\ndef test_it():\n    assert validate(' 1 ') == '1'\n",
        encoding="utf-8",
    )

    result = b2_hierarchy.apply(tmp_path)

    assert result.moves, "sin paquete raíz reconocido B2 no aplica nada"
    assert not (tmp_path / "pkg" / "es").exists()


def test_the_repo_utilities_are_not_second_candidates_either(tmp_path: Path):
    """`scripts/` (holidays) y `benchmarks/` (sqlglot) son utilidades del
    repositorio, no el código que se estudia: el mismo criterio con el que
    `acp.metrics.size` las deja fuera de la muestra de dominio."""
    build(tmp_path)
    for name in ("scripts", "benchmarks"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "__init__.py").write_text("", encoding="utf-8")

    assert b2_hierarchy._package_root(tmp_path) == tmp_path / "pkg"


def test_two_packages_of_the_repo_itself_still_leave_no_clear_root(tmp_path: Path):
    """El guardarraíl que se conserva: con dos paquetes que sí son el programa
    no está claro cuál es el punto de entrada, y aplanar el equivocado deja el
    repo sin forma de importarse."""
    build(tmp_path)
    (tmp_path / "otherpkg").mkdir()
    (tmp_path / "otherpkg" / "__init__.py").write_text("", encoding="utf-8")

    assert b2_hierarchy._package_root(tmp_path) is None
    assert b2_hierarchy.plan_moves(tmp_path) == {}
