from pathlib import Path

from acp.cli import profile_repo


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
