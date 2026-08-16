"""Equivalencia de una transformación contra un repositorio real.

Necesita Docker y red: clona un finalista de la fase 0 y pasa su suite dos
veces, antes y después de transformar.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from acp.cli import transform_repo
from acp.equivalence import compare
from acp.suite import run_suite_in_docker

pytestmark = [pytest.mark.integration, pytest.mark.docker]


@pytest.fixture(autouse=True)
def require_docker():
    if shutil.which("docker") is None:
        pytest.skip("docker no está instalado")


def test_a1_keeps_python_stdnum_equivalent(tmp_path: Path):
    """El finalista más barato (96 s por corrida), que es el que hace viable
    correr esta comprobación antes de cada bloque (§5.4.6)."""
    clone = tmp_path / "python-stdnum"
    subprocess.run(
        ["git", "clone", "--depth", "1",
         "https://github.com/arthurdejong/python-stdnum", str(clone)],
        check=True, capture_output=True,
    )

    before = run_suite_in_docker(clone, timeout=1800)
    transformed = transform_repo(clone, ["A1"], tmp_path / "work")
    after = run_suite_in_docker(transformed, timeout=1800)

    report = compare(before, after)
    assert report.equivalent is True, report.differences
