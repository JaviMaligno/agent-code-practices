"""Test de humo de la preparación de entornos. Necesita red, no contenedores.

Marcado como `integration` porque crea un entorno virtual de verdad y descarga
paquetes. Es el único punto donde se comprueba que la fontanería funciona; el
resto del módulo se prueba con funciones puras.
"""

from pathlib import Path

import pytest

from acp.suite import prepare_environment, run_suite_in_venv

pytestmark = pytest.mark.integration

PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"

[project.optional-dependencies]
test = ["pytest"]

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
py-modules = []
"""


def build_repo(root: Path) -> None:
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )


def test_runs_a_trivial_repo_in_a_virtualenv(tmp_path: Path):
    build_repo(tmp_path)

    result = run_suite_in_venv(tmp_path, env_dir=tmp_path / ".env", timeout=900)

    assert result.install_ok is True
    assert result.collect_ok is True
    assert result.ran is True
    assert result.passed == 1
    assert result.timed_out is False


def test_environment_is_removed_afterwards(tmp_path: Path):
    build_repo(tmp_path)
    env_dir = tmp_path / ".env"

    run_suite_in_venv(tmp_path, env_dir=env_dir, timeout=900)

    assert not env_dir.exists()


def test_unbuildable_repo_reports_a_preparation_failure(tmp_path: Path):
    """Un repo que no se deja instalar no debe parecer una suite en rojo."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "nonexistent.backend"\n',
        encoding="utf-8",
    )

    result = prepare_environment(tmp_path, tmp_path / ".env", timeout=900)

    assert result.install_ok is False
    assert result.install_error != ""
    assert result.ran is False


# Un test que hace lo que hace pint con `cache_folder=":auto:"`: escribir en la
# caché del usuario y encontrársela en la corrida siguiente. El nombre es
# reconocible a propósito —si esto se rompe otra vez, el fichero aparece en el
# HOME de verdad de quien lo corra y hay que poder saber de dónde salió—.
CACHE_WRITER = """\
from pathlib import Path


def test_nothing_from_the_previous_run_is_here():
    testigo = Path.home() / ".acp-testigo-de-la-corrida"
    assert not testigo.exists(), f"lo dejó la corrida anterior: {testigo}"
    testigo.write_text("1", encoding="utf-8")
"""


def test_two_runs_in_a_row_do_not_share_the_users_cache(tmp_path: Path):
    """§5.4.4: cada ejecución arranca sin estado compartido con la anterior.

    Este ejecutor aísla dependencias, no el sistema, así que sin un HOME propio
    las dos condiciones de un par comparten la caché del usuario. Medido sobre
    pint: la base deja sus pickles en `~/Library/Caches/pint`, B1 y B5 cambian
    el `__module__` de las clases y la segunda corrida muere con
    `AttributeError: Can't get attribute 'OffsetConverter'` —un rojo que no
    tiene nada que ver con la práctica que la celda quita—. Con la caché limpia,
    B1 vuelve a dar los 2.289 de la base.

    El entorno se conserva entre las dos corridas a propósito: es como corre la
    campaña (`keep_env`), y es justo donde reutilizar lo instalado no puede
    significar heredar lo que la condición anterior escribió.
    """
    from acp.runners import VenvRunner

    build_repo(tmp_path)
    (tmp_path / "tests" / "test_cache.py").write_text(CACHE_WRITER, encoding="utf-8")
    env_dir = tmp_path / ".env"

    primera = run_suite_in_venv(tmp_path, env_dir=env_dir, timeout=900, keep_env=True)
    segunda = run_suite_in_venv(tmp_path, env_dir=env_dir, timeout=900)

    assert primera.passed == 2 and primera.failed == 0
    assert segunda.passed == 2 and segunda.failed == 0
    assert not VenvRunner(tmp_path, env_dir).home.exists()
