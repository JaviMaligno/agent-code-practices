import json
from pathlib import Path

from acp.cli import main, transform_repo


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


def build_annotated(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "core.py").write_text(
        "TOTAL: int = 0\n"
        "\n"
        "\n"
        "def rate(value: int) -> int:\n"
        "    partial: int = value * 2\n"
        "    return partial\n",
        encoding="utf-8",
    )
    return root


def test_the_dose_does_not_depend_on_the_order_of_the_flags(tmp_path: Path):
    """A1 reconstruye `x: int = 1` como una asignación nueva, y LibCST la escribe
    con el espaciado por defecto. Si A3 ya había pasado, ese espaciado vuelve: la
    dosis de A3 acabaría dependiendo del orden en que se escribieron los flags, y
    dos condiciones con el mismo nombre no serían la misma condición."""
    typed = transform_repo(build_annotated(tmp_path / "typed"), ["A3", "A1"], tmp_path / "a")
    canonical = transform_repo(
        build_annotated(tmp_path / "canonical"), ["A1", "A3"], tmp_path / "b"
    )

    result = (typed / "pkg" / "core.py").read_text(encoding="utf-8")
    assert result == (canonical / "pkg" / "core.py").read_text(encoding="utf-8")
    assert " = " not in result


def test_the_subcommand_writes_the_transformed_tree(tmp_path: Path):
    """`--out` de `transform` es el árbol destino, no el directorio de informes
    de `profile`: crearlo antes de copiar deja la copia sin sitio donde ir."""
    source = build(tmp_path / "repo")

    code = main(["transform", str(source), "--apply", "A1", "--out", str(tmp_path / "work")])

    assert code == 0
    assert "value: int" not in (tmp_path / "work" / "pkg" / "core.py").read_text(encoding="utf-8")


def test_an_unknown_transform_is_rejected(tmp_path: Path):
    source = build(tmp_path / "repo")

    try:
        transform_repo(source, ["Z9"], tmp_path / "work")
    except ValueError as error:
        assert "Z9" in str(error)
    else:
        raise AssertionError("debería haber fallado")
