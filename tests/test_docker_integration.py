"""Test de humo del ejecutor con contenedores. Necesita Docker y red.

Marcado `docker` además de `integration` para poder pedirlo por separado: la
máquina donde se desarrolló la fase 0 no admite contenedores, y allí este
fichero se deselecciona entero en vez de fallar.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from acp.runners import DockerRunner
from acp.suite import run_suite_in_docker

pytestmark = [pytest.mark.integration, pytest.mark.docker]

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


def containers() -> str:
    return subprocess.run(
        ["docker", "ps", "--all", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    ).stdout


@pytest.fixture(autouse=True)
def require_docker():
    if shutil.which("docker") is None:
        pytest.skip("docker no está instalado")


def test_runs_a_trivial_repo_inside_a_container(tmp_path: Path):
    build_repo(tmp_path)

    result = run_suite_in_docker(tmp_path, timeout=900)

    assert result.install_ok is True
    assert result.collect_ok is True
    assert result.ran is True
    assert result.passed == 1
    assert result.timed_out is False


def test_the_container_does_not_survive_the_run(tmp_path: Path):
    """Un contenedor huérfano bloquea el nombre y hace que la siguiente corrida
    del mismo repo falle al arrancar."""
    build_repo(tmp_path)
    runner = DockerRunner(repo=tmp_path)

    run_suite_in_docker(tmp_path, timeout=900)

    assert runner.container not in containers()


# Layout src/, como dateutil: sin él, pytest mete la raíz en sys.path y el
# import encuentra el árbol por accidente aunque la instalación apunte a PyPI.
SELF_UNINSTALLING = """\
[project]
name = "six"
version = "0.1.0"

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
py-modules = ["six"]

[tool.setuptools.package-dir]
"" = "src"
"""


def test_a_dependency_cannot_swap_the_repo_for_its_published_version(tmp_path: Path):
    """Reproduce lo verificado con dateutil: `pip install -r requirements` trae
    el propio paquete desde PyPI y desinstala la editable, con lo que la suite
    pasaría a medir el código publicado en vez del árbol del repositorio."""
    (tmp_path / "pyproject.toml").write_text(SELF_UNINSTALLING, encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "six.py").write_text("MARKER = 'from the tree'\n", encoding="utf-8")
    # `six>=1.0` no lo satisface la editable, que declara 0.1.0: pip la
    # desinstala y pone la de PyPI, igual que freezegun con python-dateutil>=2.7.
    # El fichero solo se instala si la colecta base falla, así que el test
    # importa algo que solo trae él — que es exactamente cómo se llegó allí.
    (tmp_path / "requirements-test.txt").write_text(
        "pytest\nsortedcontainers\nsix>=1.0\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "import six\nimport sortedcontainers  # noqa: F401\n\n\n"
        "def test_tree():\n    assert six.MARKER == 'from the tree'\n",
        encoding="utf-8",
    )

    result = run_suite_in_docker(tmp_path, timeout=900)

    assert result.tree_under_test is True, result.install_error
    assert result.passed == 1


def test_a_repo_that_needs_git_to_install_still_installs(tmp_path: Path):
    """La razón por la que la imagen no puede ser `slim`: varios candidatos
    derivan su versión del repositorio en tiempo de instalación."""
    build_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        PYPROJECT.replace(
            'requires = ["setuptools"]', 'requires = ["setuptools", "setuptools-scm"]'
        ).replace('version = "0.1.0"', 'dynamic = ["version"]')
        + "\n[tool.setuptools_scm]\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path, check=True,
    )
    subprocess.run(["git", "tag", "v0.1.0"], cwd=tmp_path, check=True)

    result = run_suite_in_docker(tmp_path, timeout=900)

    assert result.install_ok is True, result.install_error
    assert result.passed == 1
