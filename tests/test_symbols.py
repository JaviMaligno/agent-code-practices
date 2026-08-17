from pathlib import Path

from acp.symbols import build_symbol_map, relocate_symbols


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
    original = tmp_path / "repo" / "pkg"
    original.mkdir(parents=True)
    (original / "billing.py").write_text("def total(rows):\n    return rows\n", encoding="utf-8")
    symbols = build_symbol_map(tmp_path / "repo")

    transformed = tmp_path / "work" / "pkg"
    transformed.mkdir(parents=True)
    (transformed / "billing.py").write_text("def f7(rows):\n    return rows\n", encoding="utf-8")

    renamed = relocate_symbols(symbols, tmp_path / "work")

    assert renamed["pkg.billing.total"].current_name == "f7"
    assert "pkg.billing.f7" not in renamed


def test_the_visible_name_is_read_back_from_the_transformed_tree(tmp_path: Path):
    """El nombre publicado tiene que ser el que está escrito en el código, no el
    que se deduce de un diccionario: A2 renombra por ámbito y restaura lo que
    define el cuerpo de una clase, así que el mismo nombre desnudo puede haber
    cambiado en un sitio y no en otro."""
    original = tmp_path / "repo" / "pkg"
    original.mkdir(parents=True)
    (original / "core.py").write_text(
        "def info(rows):\n"
        "    return rows\n"
        "\n"
        "\n"
        "class Store:\n"
        "    def info(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    symbols = build_symbol_map(tmp_path / "repo")

    transformed = tmp_path / "work" / "pkg"
    transformed.mkdir(parents=True)
    (transformed / "core.py").write_text(
        "def f1(rows):\n"
        "    return rows\n"
        "\n"
        "\n"
        "class K0:\n"
        "    def info(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )

    relocated = relocate_symbols(symbols, tmp_path / "work")

    assert relocated["pkg.core.info"].current_name == "f1"
    assert relocated["pkg.core.Store"].current_name == "K0"
    assert relocated["pkg.core.Store.info"].current_name == "info"


def test_a_symbol_nobody_renamed_keeps_its_name(tmp_path: Path):
    original = tmp_path / "repo" / "pkg"
    original.mkdir(parents=True)
    (original / "billing.py").write_text("def total(rows):\n    return rows\n", encoding="utf-8")
    symbols = build_symbol_map(tmp_path / "repo")

    # Una condición sin A2: el árbol transformado conserva los nombres.
    transformed = tmp_path / "work" / "pkg"
    transformed.mkdir(parents=True)
    (transformed / "billing.py").write_text("def total(rows):\n    return rows\n", encoding="utf-8")

    renamed = relocate_symbols(symbols, tmp_path / "work")

    assert renamed["pkg.billing.total"].current_name == "total"
