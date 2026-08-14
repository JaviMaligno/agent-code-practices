"""Preparación del entorno de un candidato, sin contenedores.

Docker está prohibido en la máquina de ejecución, así que el aislamiento es un
entorno virtual por repositorio. Lo que se prueba aquí es la parte con lógica:
qué intentos de instalación tiene sentido hacer según lo que el repo declara, y
cómo distinguir "no se pudo preparar el entorno" de "la suite está en rojo".
"""

from acp.suite import collection_failed, install_strategies

PYPROJECT_WITH_EXTRAS = """\
[project]
name = "demo"
version = "0.1.0"

[project.optional-dependencies]
docs = ["sphinx"]
test = ["pytest", "hypothesis"]
"""

PYPROJECT_WITH_GROUPS = """\
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
dev = ["pytest"]
"""

PYPROJECT_BARE = """\
[project]
name = "demo"
version = "0.1.0"
"""


def write_pyproject(tmp_path, body: str):
    (tmp_path / "pyproject.toml").write_text(body, encoding="utf-8")


def test_declared_test_extra_becomes_a_strategy(tmp_path):
    write_pyproject(tmp_path, PYPROJECT_WITH_EXTRAS)

    labels = [strategy.label for strategy in install_strategies(tmp_path)]

    assert labels == ["extra:test"]


def test_docs_extra_is_not_a_test_extra(tmp_path):
    """Instalar sphinx no acerca la suite a poder ejecutarse."""
    write_pyproject(tmp_path, '[project]\nname = "demo"\nversion = "0.1.0"\n\n[project.optional-dependencies]\ndocs = ["sphinx"]\n')

    assert install_strategies(tmp_path) == []


def test_dependency_groups_are_supported(tmp_path):
    write_pyproject(tmp_path, PYPROJECT_WITH_GROUPS)

    strategies = install_strategies(tmp_path)

    assert [s.label for s in strategies] == ["group:dev"]
    assert strategies[0].args == ["--group", "dev"]


def test_requirements_files_are_picked_up(tmp_path):
    write_pyproject(tmp_path, PYPROJECT_BARE)
    (tmp_path / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")

    strategies = install_strategies(tmp_path)

    assert [s.label for s in strategies] == ["requirements:requirements-dev.txt"]
    assert strategies[0].args == ["-r", "requirements-dev.txt"]


def test_nothing_declared_means_nothing_to_try(tmp_path):
    write_pyproject(tmp_path, PYPROJECT_BARE)

    assert install_strategies(tmp_path) == []


def test_undeclared_extras_are_never_attempted(tmp_path):
    """El motivo por el que la cadena antigua se rendía en silencio.

    `pip install -e '.[test]'` sobre un repo que no declara ese extra imprime un
    aviso y sale con código 0, así que un fallback encadenado con `||` nunca
    dispara. Solo se intenta lo que el repo declara de verdad.
    """
    write_pyproject(tmp_path, PYPROJECT_WITH_GROUPS)

    assert all(not s.label.startswith("extra:") for s in install_strategies(tmp_path))


def test_strategies_are_ordered_from_most_to_least_specific(tmp_path):
    write_pyproject(
        tmp_path,
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[project.optional-dependencies]\ndev = ["pytest"]\ntests = ["pytest"]\n\n'
        '[dependency-groups]\ntest = ["pytest"]\n',
    )
    (tmp_path / "requirements-test.txt").write_text("pytest\n", encoding="utf-8")

    labels = [strategy.label for strategy in install_strategies(tmp_path)]

    assert labels == [
        "extra:tests",
        "extra:dev",
        "group:test",
        "requirements:requirements-test.txt",
    ]


def test_missing_pyproject_falls_back_to_requirements(tmp_path):
    """Repos con setup.py y sin pyproject siguen siendo candidatos válidos."""
    (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")

    assert [s.label for s in install_strategies(tmp_path)] == ["requirements:requirements-dev.txt"]


def test_extras_declared_in_setup_py_are_found(tmp_path):
    """sqlglot declara sus dependencias de test en `extras_require` de setup.py
    y deja el pyproject con `dynamic = ["optional-dependencies"]`: leer solo el
    pyproject lo deja como un repo que no declara nada."""
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\n"
        "\n"
        "setup(\n"
        "    name='demo',\n"
        "    extras_require={\n"
        "        'dev': ['pytest', 'pandas'],\n"
        "        'docs': ['sphinx'],\n"
        "    },\n"
        ")\n",
        encoding="utf-8",
    )

    labels = [strategy.label for strategy in install_strategies(tmp_path)]

    assert labels == ["extra:dev"]


def test_extras_declared_in_setup_cfg_are_found(tmp_path):
    (tmp_path / "setup.cfg").write_text(
        "[metadata]\nname = demo\n\n[options.extras_require]\ntest =\n    pytest\n",
        encoding="utf-8",
    )

    assert [s.label for s in install_strategies(tmp_path)] == ["extra:test"]


def test_setup_py_that_cannot_be_parsed_does_not_crash_the_profiler(tmp_path):
    """No se ejecuta setup.py, se lee: ejecutar código de un repo de terceros
    para averiguar qué instalar es exactamente lo que el aislamiento evita."""
    (tmp_path / "setup.py").write_text("def broken(:\n", encoding="utf-8")

    assert install_strategies(tmp_path) == []


def test_broken_pyproject_does_not_crash_the_profiler(tmp_path):
    write_pyproject(tmp_path, "[project\nname = broken")

    assert install_strategies(tmp_path) == []


COLLECTED_CLEAN = """\
tests/test_a.py::test_one
tests/test_b.py::test_two

2 tests collected in 0.12s
"""

COLLECTION_BROKEN = """\
==================================== ERRORS ====================================
_____________________ ERROR collecting tests/test_a.py _____________________
ImportError while importing test module 'tests/test_a.py'.
ModuleNotFoundError: No module named 'hypothesis'

1 error in 0.30s
"""


def test_clean_collection_is_not_a_failure():
    assert collection_failed(COLLECTED_CLEAN) is False


def test_import_error_during_collection_is_a_failure():
    assert collection_failed(COLLECTION_BROKEN) is True


def test_zero_collected_tests_counts_as_failure():
    """Un repo del que no se colecta nada no puede admitirse como suite verde."""
    assert collection_failed("no tests ran in 0.01s\n") is True


def test_locations_are_resolved_before_running_anything(tmp_path, monkeypatch):
    """Los comandos se lanzan con cwd=repo, así que una ruta relativa se
    resolvería dos veces: el entorno acaba en repo/repo/.acp-venv y el
    intérprete no está donde se le busca después.
    """
    from pathlib import Path

    from acp.suite import resolve_locations

    monkeypatch.chdir(tmp_path)
    (tmp_path / "repo").mkdir()

    repo, env_dir = resolve_locations(Path("repo"), None)

    assert repo.is_absolute()
    assert env_dir.is_absolute()
    assert env_dir.parent == repo


def test_an_explicit_relative_env_dir_is_resolved_too(tmp_path, monkeypatch):
    from pathlib import Path

    from acp.suite import resolve_locations

    monkeypatch.chdir(tmp_path)
    (tmp_path / "repo").mkdir()

    _, env_dir = resolve_locations(Path("repo"), Path("envs/demo"))

    assert env_dir.is_absolute()
    assert env_dir == (tmp_path / "envs" / "demo").resolve()


TOX_INI = """\
[testenv]
deps = pytest
       pytest-cov
commands = pytest

[testenv:flake8]
deps = flake8
commands = flake8
"""


def test_tox_testenv_deps_are_a_strategy(tmp_path):
    """Repos antiguos declaran sus dependencias de test solo en tox.ini.

    python-stdnum es exactamente ese caso: sin extras, sin grupos y sin
    requirements-*.txt, pero con `deps = pytest, pytest-cov` en [testenv].
    """
    (tmp_path / "tox.ini").write_text(TOX_INI, encoding="utf-8")

    strategies = install_strategies(tmp_path)

    assert [s.label for s in strategies] == ["tox:testenv"]
    assert strategies[0].args == ["pytest", "pytest-cov"]


def test_tox_deps_ignore_other_environments_and_comments(tmp_path):
    (tmp_path / "tox.ini").write_text(
        "[testenv]\ndeps = pytest\n       mypy<2.0  # comentario al vuelo\n",
        encoding="utf-8",
    )

    assert install_strategies(tmp_path)[0].args == ["pytest", "mypy<2.0"]


def test_broken_tox_ini_does_not_crash_the_profiler(tmp_path):
    (tmp_path / "tox.ini").write_text("[testenv\ndeps = ???", encoding="utf-8")

    assert install_strategies(tmp_path) == []


UNRECOGNISED = """\
ERROR: usage: python -m pytest [options] [file_or_dir] [...]
python -m pytest: error: unrecognized arguments: --cov=stdnum --cov-report=html
  inifile: setup.cfg
"""


def test_plugins_are_inferred_from_unrecognised_arguments():
    """Los addopts del proyecto pueden exigir plugins que nadie declara.

    Aquí no vale neutralizar los addopts: en python-stdnum incluyen
    --doctest-modules, y los doctests son la mitad de la suite.
    """
    from acp.suite import plugins_for_unrecognised

    assert plugins_for_unrecognised(UNRECOGNISED) == ["pytest-cov"]


def test_several_missing_plugins_are_inferred_at_once():
    from acp.suite import plugins_for_unrecognised

    output = "error: unrecognized arguments: -n auto --timeout=30 --cov=x"

    assert plugins_for_unrecognised(output) == ["pytest-cov", "pytest-timeout", "pytest-xdist"]


def test_no_plugins_inferred_from_unrelated_errors():
    from acp.suite import plugins_for_unrecognised

    assert plugins_for_unrecognised("ModuleNotFoundError: No module named 'zeep'") == []


def test_plugins_are_inferred_from_compound_flags():
    """holidays rechaza `--cov-fail-under=100`, no `--cov=x`. Exigir que el
    flag termine ahí mismo dejaba fuera todas las variantes con guion, que son
    la mayoría de las que un proyecto escribe en sus addopts."""
    from acp.suite import plugins_for_unrecognised

    output = "error: unrecognized arguments: --cov-fail-under=100 --cov-report=html"

    assert plugins_for_unrecognised(output) == ["pytest-cov"]


def test_editable_locations_are_read_from_pip():
    """Se le pregunta a pip en vez de adivinar el nombre de import: el de
    distribución no tiene por qué coincidir (python-dateutil / dateutil)."""
    from acp.suite import editable_locations

    output = """[
      {"name": "python-dateutil", "version": "0.1.dev1", "editable_project_location": "/repo"},
      {"name": "pytest", "version": "9.1.1"}
    ]"""

    assert editable_locations(output) == {"/repo"}


def test_unreadable_pip_output_yields_no_locations():
    from acp.suite import editable_locations

    assert editable_locations("Traceback (most recent call last):") == set()


def test_a_flag_that_merely_starts_the_same_is_not_a_match():
    from acp.suite import plugins_for_unrecognised

    assert plugins_for_unrecognised("error: unrecognized arguments: --covfefe") == []
