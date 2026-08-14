from acp.metrics.coupling import measure


def test_counts_internal_edges_only(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("import os\n", encoding="utf-8")
    (pkg / "api.py").write_text("from pkg import core\nimport json\n", encoding="utf-8")
    (pkg / "cli.py").write_text("from pkg import core\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.internal_modules == 4
    assert result.internal_edges == 2
    assert result.max_fan_in == 2
    assert result.mean_fan_out == 0.5


def test_repo_without_internal_imports_has_no_edges(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("import os\n", encoding="utf-8")
    (pkg / "b.py").write_text("import sys\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.internal_edges == 0
    assert result.max_fan_in == 0
