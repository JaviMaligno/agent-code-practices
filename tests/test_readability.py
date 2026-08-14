from acp.metrics.readability import measure

SOURCE = '''\
"""Module docstring."""


def annotated(a: int) -> int:
    """Doc."""
    # a comment
    return a + 1


def bare(a):
    return a
'''


def test_measures_ratios_and_doc_presence(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.annotated_function_ratio == 0.5
    assert result.has_readme is True
    assert result.has_docs_dir is False
    assert result.comment_ratio > 0
    assert result.docstring_ratio > 0


def test_end_of_line_comments_count(tmp_path):
    """Contar solo las líneas que empiezan por `#` subestima el margen de
    degradación de A4 justo en los repos que comentan al lado del código."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1  # explica x\ny = 2\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.comment_ratio == 0.5


def test_a_hash_inside_a_string_is_not_a_comment(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(
        'TEMPLATE = """\n# esto es contenido, no un comentario\n"""\n', encoding="utf-8"
    )

    result = measure(tmp_path)

    assert result.comment_ratio == 0.0


def test_detects_docs_directory(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()

    result = measure(tmp_path)

    assert result.has_docs_dir is True
    assert result.annotated_function_ratio == 0.0
