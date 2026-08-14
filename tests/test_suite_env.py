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
