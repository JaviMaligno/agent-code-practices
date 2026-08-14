from pathlib import Path

import pytest

from acp.cli import profile_repo, suite_runner
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
