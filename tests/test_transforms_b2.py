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
