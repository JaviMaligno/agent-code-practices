import json
from pathlib import Path

from acp.cli import transform_repo


def build(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "core.py").write_text(
        'def rate(value: int) -> int:\n    """Doc."""\n    return value * 2\n',
        encoding="utf-8",
    )
    return root


def test_it_transforms_a_copy_and_leaves_the_original(tmp_path: Path):
    source = build(tmp_path / "repo")

    destination = transform_repo(source, ["A1"], tmp_path / "work")

    assert "value: int" in (source / "pkg" / "core.py").read_text(encoding="utf-8")
    assert "value: int" not in (destination / "pkg" / "core.py").read_text(encoding="utf-8")


def test_the_manifest_records_what_was_applied(tmp_path: Path):
    """Sin procedencia registrada, un cambio a mitad de campaña deja el conjunto
    de datos sin interpretación posible (§5.4.1)."""
    source = build(tmp_path / "repo")

    destination = transform_repo(source, ["A1", "A4"], tmp_path / "work")

    manifest = json.loads((destination / "acp-manifest.json").read_text(encoding="utf-8"))
    assert manifest["applied"] == ["A1", "A4"]
    assert manifest["symbols"]["pkg.core.rate"]["current_name"] == "rate"


def test_an_unknown_transform_is_rejected(tmp_path: Path):
    source = build(tmp_path / "repo")

    try:
        transform_repo(source, ["Z9"], tmp_path / "work")
    except ValueError as error:
        assert "Z9" in str(error)
    else:
        raise AssertionError("debería haber fallado")
