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
