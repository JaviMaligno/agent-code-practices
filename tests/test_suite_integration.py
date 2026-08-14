import subprocess
from pathlib import Path

import pytest

from acp.suite import run_suite_in_docker

pytestmark = pytest.mark.integration


def test_runs_a_trivial_repo_in_docker(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n',
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    result = run_suite_in_docker(tmp_path, timeout=600)

    assert result.ran is True
    assert result.passed == 1
