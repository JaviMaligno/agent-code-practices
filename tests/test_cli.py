from pathlib import Path

import pytest

from acp.cli import RUNNERS, main, profile_repo, suite_runner
from acp.models import SuiteMetrics
from acp.suite import run_suite_in_docker, run_suite_in_venv


def test_profile_repo_produces_a_profile_without_running_the_suite(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        '"""Core."""\n\n\ndef f(a: int) -> int:\n    if a:\n        return 1\n    return 0\n',
        encoding="utf-8",
    )

    profile = profile_repo(tmp_path, name="demo", run_suite=False)

    assert profile.name == "demo"
    assert profile.size.python_files == 2
    assert profile.suite.ran is False
    assert profile.readability.annotated_function_ratio == 1.0


def test_docker_is_the_default_executor():
    """La campaña corre en contenedores (§2 del spec); el entorno virtual queda
    como alternativa verificada para la máquina que no los admite."""
    assert suite_runner("docker") is run_suite_in_docker
    assert suite_runner(None) is run_suite_in_docker


def test_the_virtualenv_executor_stays_selectable():
    assert suite_runner("venv") is run_suite_in_venv


def test_an_unknown_executor_is_rejected():
    with pytest.raises(ValueError):
        suite_runner("podman")


def build_repo(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def f(a):\n    return a\n", encoding="utf-8")
    return root


def record_runner(calls: list[dict]):
    """Ejecutor de mentira que apunta con qué opciones lo llamaron.

    Lo que se comprueba aquí es el cableado —qué llega del flag al ejecutor—, y
    montar un entorno real para eso costaría minutos y una red.
    """

    def run(root: Path, **options):
        calls.append(options)
        return SuiteMetrics(attempted=True)

    return run


def test_the_mode_that_installs_dependencies_but_not_the_repo_is_reachable_from_the_cli(
    tmp_path: Path, monkeypatch
):
    """Es el único modo válido para B1, B2 y B5 —el árbol transformado ya no
    encaja con lo que declara su pyproject (§5.6)— y hasta ahora solo se
    alcanzaba llamando a `install_and_collect` desde Python. Quien corra una
    celda desde la línea de comandos no puede: mide el paquete instalado de PyPI
    o no mide nada, y en los dos casos la celda se lee como un resultado."""
    calls: list[dict] = []
    monkeypatch.setitem(RUNNERS, "venv", record_runner(calls))
    source = build_repo(tmp_path / "repo")

    code = main([
        "profile", str(source), "--name", "demo", "--out", str(tmp_path / "out"),
        "--runner", "venv", "--no-install-repo",
    ])

    assert code == 0
    assert calls == [{"install_repo": False}]


def test_the_repo_is_installed_unless_the_flag_says_otherwise(
    tmp_path: Path, monkeypatch
):
    """Perfilar un candidato mide el repo tal cual, y ahí instalarlo es lo
    correcto: el flag nuevo no puede cambiar lo que hacía la campaña."""
    calls: list[dict] = []
    monkeypatch.setitem(RUNNERS, "venv", record_runner(calls))
    source = build_repo(tmp_path / "repo")

    code = main([
        "profile", str(source), "--name", "demo", "--out", str(tmp_path / "out"),
        "--runner", "venv",
    ])

    assert code == 0
    assert calls == [{"install_repo": True}]
