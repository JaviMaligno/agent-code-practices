import shutil
from pathlib import Path

import pytest

from acp.cli import transform_repo
from acp.transforms import b4_tests


def build(root: Path) -> None:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_core.py").write_text(
        "from pkg.core import f\n\n\ndef test_f():\n    assert f() == 1\n", encoding="utf-8"
    )
    (root / "conftest.py").write_text("", encoding="utf-8")


def test_the_suite_leaves_the_tree(tmp_path: Path):
    root = tmp_path / "work"
    build(root)

    b4_tests.apply(root)

    assert not (root / "tests").exists()


def test_the_suite_is_kept_outside_and_intact(tmp_path: Path):
    """Los tests de validación se ejecutan fuera del alcance del agente y no se
    tocan nunca (§4.2): ocultarlos no puede significar perderlos."""
    root = tmp_path / "work"
    build(root)

    b4_tests.apply(root)

    kept = tmp_path / "work.acp-tests" / "tests" / "test_core.py"
    assert kept.exists()
    assert "def test_f()" in kept.read_text(encoding="utf-8")


def test_the_kept_suite_never_lands_inside_the_tree(tmp_path: Path):
    """Dentro del árbol, el agente la encuentra con un `ls` y B4 no mide nada."""
    root = tmp_path / "work"
    build(root)

    b4_tests.apply(root)

    assert not any(path.name.startswith("acp-tests") for path in root.rglob("*"))


def test_the_package_itself_is_not_confused_with_the_suite(tmp_path: Path):
    """Un paquete que se llame `testing` o un módulo `test_utils.py` dentro del
    código fuente no son la suite: llevárselos cambiaría el programa."""
    root = tmp_path / "work"
    build(root)
    (root / "pkg" / "testing").mkdir()
    (root / "pkg" / "testing" / "__init__.py").write_text("HELPER = 1\n", encoding="utf-8")

    b4_tests.apply(root)

    assert (root / "pkg" / "testing" / "__init__.py").exists()


def test_the_root_conftest_leaves_with_the_suite(tmp_path: Path):
    """El conftest de la raíz es maquinaria de la suite —fixtures, plugins, los
    paths que registra—: dejarlo en el árbol le enseña al agente la mitad de lo
    que B4 esconde, y sacarlo sin conservarlo rompería la verificación."""
    root = tmp_path / "work"
    build(root)

    b4_tests.apply(root)

    assert not (root / "conftest.py").exists()
    assert (tmp_path / "work.acp-tests" / "conftest.py").exists()


def test_the_kept_suite_mirrors_the_tree_so_it_can_be_put_back(tmp_path: Path):
    """La verificación restaura la suite volcando lo guardado sobre la raíz del
    árbol dentro del contenedor. Si lo guardado no conservara la ruta relativa,
    los ficheros volverían a otro sitio y la configuración de pytest del repo
    —que nombra `tests` como ruta de colecta— dejaría de encontrarlos."""
    root = tmp_path / "work"
    build(root)

    b4_tests.apply(root)

    kept = b4_tests.kept_suite_path(root)
    assert sorted(path.relative_to(kept).as_posix() for path in kept.rglob("*")) == [
        "conftest.py",
        "tests",
        "tests/test_core.py",
    ]


def test_the_kept_suite_keeps_files_that_are_not_python(tmp_path: Path):
    """Comprobado contra python-stdnum: de los 170 ficheros de su suite, **ni
    uno solo es `.py`** —169 son `.doctest` y uno es un `.dat` binario—, así que
    una B4 que moviera solo módulos de Python se llevaría un directorio vacío y
    dejaría la suite entera sin restaurar. Nada lo pinchaba: el test del espejo
    compara nombres de ruta sobre un fixture que solo tiene `.py`.
    """
    root = tmp_path / "work"
    build(root)
    (root / "tests" / "test_nif.doctest").write_text(">>> 1 + 1\n2\n", encoding="utf-8")
    (root / "tests" / "numdb-test.dat").write_bytes(b"\x00\x01binario\xff")

    b4_tests.apply(root)

    kept = b4_tests.kept_suite_path(root) / "tests"
    assert kept.joinpath("test_nif.doctest").read_text(encoding="utf-8") == ">>> 1 + 1\n2\n"
    assert kept.joinpath("numdb-test.dat").read_bytes() == b"\x00\x01binario\xff"


def test_a_repo_without_a_suite_keeps_nothing(tmp_path: Path):
    """Un directorio guardado vacío es peor que ninguno: la verificación lo
    volcaría sin restaurar nada y la corrida se leería como una suite que no
    colecta, o sea como un fracaso, cuando lo que pasa es que B4 no encontró
    nada que esconder."""
    root = tmp_path / "work"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    result = b4_tests.apply(root)

    assert result.files_changed == 0
    assert not b4_tests.kept_suite_path(root).exists()


def test_a_suite_nested_inside_the_package_also_leaves_the_tree(tmp_path: Path):
    """Es la forma de pint (`pint/testsuite/`), y era la celda que no medía nada.

    Mientras B4 solo miró el primer nivel, un repo que guarda su suite dentro
    del paquete se leía como un repo sin suite: cero ficheros sacados, ningún
    directorio guardado y la condición «los tests no están» sin aplicar. Lo que
    protege al programa no es la profundidad —un paquete `testing` del código
    fuente sigue sin ser la suite—, es que nadie de fuera lo importe, que es lo
    que comprueba el test siguiente.
    """
    root = tmp_path / "work"
    build(root)
    (root / "pkg" / "testsuite").mkdir()
    (root / "pkg" / "testsuite" / "test_registry.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    b4_tests.apply(root)

    assert not (root / "pkg" / "testsuite").exists()
    kept = tmp_path / "work.acp-tests" / "pkg" / "testsuite" / "test_registry.py"
    assert kept.exists()
    assert "def test_ok()" in kept.read_text(encoding="utf-8")


def test_a_nested_test_directory_the_source_imports_is_left_alone(tmp_path: Path):
    """El guardarraíl que sustituye al límite de profundidad, y por qué existe.

    La verificación **restaura** la suite antes de correr, así que un import
    roto por habérsela llevado no lo vería nadie: el contenedor pasaría y el
    árbol que explora el agente estaría roto en silencio. Por eso la pregunta no
    es dónde está el directorio, sino si alguien de fuera lo importa: si el
    código fuente lo hace, ese directorio es parte del programa y se queda.
    """
    root = tmp_path / "work"
    build(root)
    (root / "pkg" / "testsuite").mkdir()
    (root / "pkg" / "testsuite" / "__init__.py").write_text("HELPERS = 1\n", encoding="utf-8")
    (root / "pkg" / "testsuite" / "test_registry.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (root / "pkg" / "core.py").write_text(
        "from pkg.testsuite import HELPERS\n\n\ndef f():\n    return HELPERS\n",
        encoding="utf-8",
    )

    b4_tests.apply(root)

    assert (root / "pkg" / "testsuite" / "test_registry.py").exists()


def test_a_nested_test_directory_imported_by_a_relative_import_is_left_alone(tmp_path: Path):
    """La misma dependencia escrita de la otra forma: `from .testsuite import`.

    Buscar solo la ruta absoluta dejaría pasar el caso más común dentro de un
    paquete, y el fallo sería el silencioso: contenedor en verde, árbol roto.
    """
    root = tmp_path / "work"
    build(root)
    (root / "pkg" / "testsuite").mkdir()
    (root / "pkg" / "testsuite" / "__init__.py").write_text("HELPERS = 1\n", encoding="utf-8")
    (root / "pkg" / "testsuite" / "test_registry.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (root / "pkg" / "__init__.py").write_text(
        "from . import testsuite\n", encoding="utf-8"
    )

    b4_tests.apply(root)

    assert (root / "pkg" / "testsuite" / "test_registry.py").exists()


def test_the_dose_can_be_declared_before_applying(tmp_path: Path):
    """Igual que en B3: quien escriba los resultados tiene que poder decir qué
    se llevó B4 en cada repo sin deducirlo de un contador de ficheros."""
    root = tmp_path / "work"
    build(root)

    found = b4_tests.suite_paths(root)

    assert [path.relative_to(root).as_posix() for path in found] == [
        "conftest.py",
        "tests",
    ]


def build_with_a_readme_the_suite_verifies(root: Path) -> None:
    """La forma de holidays: su suite comprueba que las tablas del README listan
    todo lo soportado, así que ahí el README es contrato y B3 no lo toca."""
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# demo\n\n| país |\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_docs.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def test_the_readme_lists_everything():\n"
        "    assert 'país' in Path('README.md').read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )


def test_b3_still_sees_the_suite_when_b4_is_asked_for_first(tmp_path: Path):
    """B4 esconde la suite, y B3 decide mirándola si el README es contrato: en
    el orden en que se piden, `--apply B4,B3` deja a B3 sin nada que mirar,
    vacía un README que la suite verifica, y el fallo aparece en la corrida de
    validación —donde la suite sí existe— y no en el árbol. El orden canónico es
    parte de la condición, no del capricho de quien escribe los flags."""
    source = tmp_path / "repo"
    build_with_a_readme_the_suite_verifies(source)

    work = transform_repo(source, ["B4", "B3"], tmp_path / "work")

    assert (tmp_path / "work.acp-tests" / "tests" / "test_docs.py").exists()
    assert "país" in (work / "README.md").read_text(encoding="utf-8")


def run_pytest(root: Path):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True,
    )


def build_like_python_stdnum(root: Path) -> None:
    """La forma real de python-stdnum: sus `addopts` nombran `tests` como ruta
    de colecta, al lado del paquete, de `--doctest-modules` y de un `--ignore`
    por ruta de fichero."""
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "core.py").write_text(
        'def f():\n    """\n    >>> f()\n    1\n    """\n    return 1\n', encoding="utf-8"
    )
    # Se colectaría y estallaría al importarse: está en el `--ignore`, así que
    # si la reescritura tocara algo más que la ruta de la suite, se vería.
    (root / "pkg" / "broken.py").write_text(
        "raise ImportError('a este no hay que colectarlo')\n", encoding="utf-8"
    )
    (root / "setup.cfg").write_text(
        "[tool:pytest]\n"
        'addopts = --doctest-modules --doctest-glob="*.doctest" pkg tests'
        " --ignore=pkg/broken.py\n"
        "doctest_optionflags = NORMALIZE_WHITESPACE\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_core.py").write_text(
        "from pkg.core import f\n\n\ndef test_f():\n    assert f() == 1\n", encoding="utf-8"
    )


def test_the_tree_the_agent_sees_can_still_run_pytest(tmp_path: Path):
    """Con la suite fuera, la configuración del repo sigue nombrando `tests`
    como ruta de colecta y pytest **no arranca**: muere con `ERROR: file or
    directory not found: tests` antes de colectar nada.

    Medido sobre el clon real de python-stdnum: baseline 422 tests colectados,
    árbol B4 0. Los 251 que se pierden de más son los doctests sobre `stdnum/`
    —`--doctest-modules`—, que B4 nunca quiso esconder. La condición quita
    entonces mucho más que su dosis declarada, y de paso le grita al agente que
    algo le han quitado."""
    root = tmp_path / "work"
    build_like_python_stdnum(root)

    b4_tests.apply(root)

    ran = run_pytest(root)
    # El aviso de la ruta que falta sale por stderr y el resumen por stdout: se
    # miran los dos, porque el fallo es que pytest ni arranca.
    assert "file or directory not found" not in ran.stderr, ran.stderr[-2000:]
    # El doctest de `pkg/core.py`: lo que B4 no esconde tiene que seguir corriendo.
    assert "1 passed" in ran.stdout, ran.stdout[-2000:] + ran.stderr[-2000:]


def test_verification_gets_the_original_configuration_back(tmp_path: Path):
    """La verificación restaura la suite volcando lo guardado sobre la raíz. Si
    la configuración editada se quedara puesta, la corrida de equivalencia
    colectaría los tests del repo pero no por la ruta que su `addopts` declara,
    y la celda mediría una suite distinta de la del baseline."""
    root = tmp_path / "work"
    build_like_python_stdnum(root)

    b4_tests.apply(root)
    shutil.copytree(b4_tests.kept_suite_path(root), root, dirs_exist_ok=True)

    ran = run_pytest(root)
    # El doctest más el test del repo, igual que antes de transformar nada.
    assert "2 passed" in ran.stdout, ran.stdout[-2000:]
    assert (root / "setup.cfg").read_text(encoding="utf-8").count("tests") == 1


def test_a_toml_configuration_stops_pointing_at_the_hidden_suite(tmp_path: Path):
    """La forma real de holidays, y en la variante de clave con puntos que un
    lector de secciones `[tool.pytest.ini_options]` no vería:

        [tool.pytest]
        ini_options.testpaths = [ "tests" ]

    Aquí pytest no aborta —avisa y colecta recursivamente desde el directorio—,
    así que no rompe la celda, pero el aviso nombra `tests` en cada corrida que
    haga el agente y la colecta deja de ser la que el repo declara."""
    root = tmp_path / "work"
    build(root)
    (root / "pyproject.toml").write_text(
        "[project]\nname = \"demo\"\n\n"
        "[tool.pytest]\n"
        'ini_options.testpaths = [ "tests" ]\n'
        'ini_options.addopts = [\n  "--strict-markers",\n]\n',
        encoding="utf-8",
    )

    b4_tests.apply(root)

    rewritten = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"tests"' not in rewritten, rewritten
    assert '"--strict-markers"' in rewritten, rewritten
    kept = b4_tests.kept_suite_path(root) / "pyproject.toml"
    assert '"tests"' in kept.read_text(encoding="utf-8")


def test_a_configuration_that_does_not_name_the_suite_is_left_alone(tmp_path: Path):
    """B4 esconde la suite, no reescribe la configuración del repo: si nada
    apunta a lo que se llevó, no hay nada que tocar y nada que restaurar."""
    root = tmp_path / "work"
    build(root)
    (root / "setup.cfg").write_text(
        "[tool:pytest]\naddopts = --strict-markers\n", encoding="utf-8"
    )

    b4_tests.apply(root)

    assert (root / "setup.cfg").read_text(encoding="utf-8") == (
        "[tool:pytest]\naddopts = --strict-markers\n"
    )
    assert not (b4_tests.kept_suite_path(root) / "setup.cfg").exists()


def test_a_multiline_configuration_value_is_followed_to_its_last_line(tmp_path: Path):
    """Cuando las opciones son muchas, `addopts` se escribe repartido en varias
    líneas y la ruta de la suite cae en cualquiera de ellas. Mirando solo la
    línea de la clave, la ruta se queda y pytest sigue sin arrancar; y lo que
    queda tiene que seguir siendo configuración válida, no solo texto sin la
    ruta."""
    root = tmp_path / "work"
    build_like_python_stdnum(root)
    (root / "setup.cfg").write_text(
        "[tool:pytest]\n"
        "addopts =\n"
        "    --doctest-modules\n"
        "    --ignore=pkg/broken.py\n"
        "    pkg\n"
        "    tests\n",
        encoding="utf-8",
    )

    b4_tests.apply(root)

    ran = run_pytest(root)
    assert "file or directory not found" not in ran.stderr, ran.stderr[-2000:]
    # El `--ignore` de la línea de al lado sigue en pie: si la reescritura se
    # hubiera comido la línea entera, `pkg/broken.py` estallaría al colectarse.
    assert "1 passed" in ran.stdout, ran.stdout[-2000:] + ran.stderr[-2000:]


def test_a_subdirectory_of_the_suite_in_the_collect_paths_leaves_too(tmp_path: Path):
    """Un repo que separa unitarios de integración no nombra `tests` en su
    configuración: nombra `tests/unit`. Esa ruta desaparece del árbol igual que
    la del directorio entero —se la lleva B4 dentro—, así que pytest muere con
    `ERROR: file or directory not found: tests/unit` antes de colectar nada.

    Es el mismo fallo que la ruta desnuda: la condición se lleva por delante lo
    que nunca quiso esconder —aquí el doctest de `pkg/core.py`— y le anuncia al
    agente que le han quitado algo."""
    root = tmp_path / "work"
    build_like_python_stdnum(root)
    (root / "tests" / "unit").mkdir()
    (root / "tests" / "test_core.py").rename(root / "tests" / "unit" / "test_core.py")
    (root / "setup.cfg").write_text(
        "[tool:pytest]\n"
        "addopts = --doctest-modules pkg tests/unit --ignore=pkg/broken.py\n",
        encoding="utf-8",
    )

    b4_tests.apply(root)

    ran = run_pytest(root)
    assert "file or directory not found" not in ran.stderr, ran.stderr[-2000:]
    assert "1 passed" in ran.stdout, ran.stdout[-2000:] + ran.stderr[-2000:]


def test_the_suite_named_with_a_leading_dot_slash_also_leaves_the_collect_paths(
    tmp_path: Path,
):
    """`./tests` y `tests` son la misma ruta escrita de dos maneras, y las dos
    apuntan a lo que B4 acaba de sacar del árbol. Reconocer solo la segunda deja
    a pytest sin arrancar en cuanto el repo escribe la primera."""
    root = tmp_path / "work"
    build_like_python_stdnum(root)
    (root / "setup.cfg").write_text(
        "[tool:pytest]\n"
        "addopts = --doctest-modules pkg ./tests --ignore=pkg/broken.py\n",
        encoding="utf-8",
    )

    b4_tests.apply(root)

    ran = run_pytest(root)
    assert "file or directory not found" not in ran.stderr, ran.stderr[-2000:]
    assert "1 passed" in ran.stdout, ran.stdout[-2000:] + ran.stderr[-2000:]


@pytest.mark.parametrize(
    "declared",
    ['["tests/", "pkg"]', '["tests/unit", "tests/integration", "pkg"]'],
)
def test_the_tree_stops_naming_the_suite_however_the_path_is_written(
    tmp_path: Path, declared: str
):
    """Aquí pytest no revienta —un `testpaths` que falta lo tolera—, pero el
    árbol se queda diciendo `testpaths = ["tests/"]`. La mitad declarada del
    arreglo es que el árbol quede como el de un repo que nunca tuvo suite; un
    agente que lee esa línea sabe que había tests y sabe cómo se llamaban.

    La barra final y el subdirectorio son la misma ruta que el nombre desnudo,
    escrita como la escribe el repo."""
    root = tmp_path / "work"
    build(root)
    (root / "tests" / "unit").mkdir()
    (root / "tests" / "integration").mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = \"demo\"\n\n"
        "[tool.pytest.ini_options]\n"
        f"testpaths = {declared}\n",
        encoding="utf-8",
    )

    b4_tests.apply(root)

    rewritten = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "tests" not in rewritten, rewritten
    assert '"pkg"' in rewritten, rewritten
    # Lo editado se guarda con la suite: la verificación restaura volcando lo
    # guardado sobre la raíz, y sin esta copia la corrida de equivalencia
    # colectaría por una ruta que el repo no declara.
    kept = b4_tests.kept_suite_path(root) / "pyproject.toml"
    assert declared in kept.read_text(encoding="utf-8")


def test_what_only_looks_like_the_suite_path_survives_the_rewrite(tmp_path: Path):
    """El contrapeso del reconocimiento por ruta, que es lo que puede pasarse de
    largo. `tests-slow` no es la suite —lo dice `is_test_dir`, que es quien lo
    decide, y por eso B4 no se lo lleva: lo que sigue en el árbol tiene que
    seguir nombrado o la colecta pierde ficheros que están—; `docs/tests` es
    otro sitio, que el nombre de la suite viene relativo a la raíz; y
    `--cov=tests` y `--ignore=tests/conftest.py` son opciones con valor, que
    apuntar a algo que ya no existe no rompe nada y reescribirlas sería
    quitarle al repo una decisión suya.

    Solo se va el elemento de la lista de colecta que nombra lo que B4 se llevó.
    """
    root = tmp_path / "work"
    build(root)
    (root / "tests-slow").mkdir()
    (root / "tests-slow" / "test_slow.py").write_text(
        "def test_slow():\n    assert True\n", encoding="utf-8"
    )
    (root / "docs").mkdir()
    (root / "docs" / "tests").mkdir()
    (root / "pytest.ini").write_text(
        "[pytest]\n"
        "addopts = --cov=tests --ignore=tests/conftest.py tests tests-slow docs/tests\n",
        encoding="utf-8",
    )

    b4_tests.apply(root)

    assert (root / "tests-slow" / "test_slow.py").exists()
    rewritten = (root / "pytest.ini").read_text(encoding="utf-8")
    assert rewritten == (
        "[pytest]\n"
        "addopts = --cov=tests --ignore=tests/conftest.py tests-slow docs/tests\n"
    ), rewritten
