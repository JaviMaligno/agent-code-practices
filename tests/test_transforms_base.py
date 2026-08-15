from pathlib import Path

from acp.transforms.base import TransformResult, copy_tree


def test_copy_tree_leaves_the_original_untouched(tmp_path: Path):
    source = tmp_path / "repo"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")

    destination = copy_tree(source, tmp_path / "work")
    (destination / "pkg" / "core.py").write_text("x = 2\n", encoding="utf-8")

    assert (source / "pkg" / "core.py").read_text(encoding="utf-8") == "x = 1\n"


def test_copy_tree_keeps_the_git_directory_out(tmp_path: Path):
    """El .git de un clon pesa más que el código y no se transforma nunca."""
    source = tmp_path / "repo"
    (source / ".git").mkdir(parents=True)
    (source / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (source / "pkg").mkdir()
    (source / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")

    destination = copy_tree(source, tmp_path / "work")

    assert (destination / "pkg" / "core.py").exists()
    assert not (destination / ".git").exists()


def test_a_result_reports_nothing_changed_by_default():
    assert TransformResult().files_changed == 0
    assert TransformResult().renames == {}


def test_transformable_files_include_the_repo_tests(tmp_path: Path):
    """§4.3.1: renombrar solo el código fuente deja los tests sin compilar, y
    entonces la condición mide otra cosa. Es justo lo contrario de lo que hace
    `iter_source_files`, que los excluye para no perfilarlos."""
    from acp.transforms.base import iter_transformable_files

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_core.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "generated.py").write_text("x = 1\n", encoding="utf-8")

    found = {path.relative_to(tmp_path).as_posix() for path in iter_transformable_files(tmp_path)}

    assert found == {"pkg/core.py", "tests/test_core.py"}
