from pathlib import Path

from acp.symbols import apply_renames, build_symbol_map


def test_every_function_and_class_gets_a_location(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "billing.py").write_text(
        "def total(rows):\n"
        "    return sum(rows)\n"
        "\n"
        "\n"
        "class Invoice:\n"
        "    def render(self):\n"
        "        return ''\n",
        encoding="utf-8",
    )

    symbols = build_symbol_map(tmp_path)

    assert symbols["pkg.billing.total"].start == 1
    assert symbols["pkg.billing.total"].end == 2
    assert symbols["pkg.billing.Invoice"].start == 5
    assert symbols["pkg.billing.Invoice.render"].start == 6
    assert symbols["pkg.billing.total"].path == "pkg/billing.py"


def test_the_map_keeps_the_original_name_as_identity(tmp_path: Path):
    """Con A2 el nombre visible cambia. Si la clave cambiara con él, no habría
    forma de decir que el agente miró la región objetivo."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "billing.py").write_text("def total(rows):\n    return rows\n", encoding="utf-8")
    symbols = build_symbol_map(tmp_path)

    renamed = apply_renames(symbols, {"total": "f7"})

    assert renamed["pkg.billing.total"].current_name == "f7"
    assert "pkg.billing.f7" not in renamed


def test_a_symbol_nobody_renamed_keeps_its_name(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "billing.py").write_text("def total(rows):\n    return rows\n", encoding="utf-8")
    symbols = build_symbol_map(tmp_path)

    renamed = apply_renames(symbols, {"otra_cosa": "f9"})

    assert renamed["pkg.billing.total"].current_name == "total"
