from acp.metrics.runtime_typing import measure


def write(tmp_path, source: str):
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")


def test_flags_pydantic(tmp_path):
    write(tmp_path, "from pydantic import BaseModel\n\n\nclass M(BaseModel):\n    x: int\n")
    result = measure(tmp_path)
    assert result.uses_runtime_typing is True
    assert any("pydantic" in item for item in result.evidence)


def test_flags_get_type_hints(tmp_path):
    write(tmp_path, "import typing\n\n\ndef f(o):\n    return typing.get_type_hints(o)\n")
    result = measure(tmp_path)
    assert result.uses_runtime_typing is True


def test_plain_annotations_are_clean(tmp_path):
    write(tmp_path, "def f(a: int) -> int:\n    return a\n")
    result = measure(tmp_path)
    assert result.uses_runtime_typing is False
    assert result.evidence == []
