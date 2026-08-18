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


def test_a_module_whose_shape_changed_is_not_paired_by_guesswork(tmp_path: Path):
    """Casar por posición vale mientras el árbol tenga las mismas definiciones.
    Si alguna vez una transformación quita o añade una, las hermanas siguientes
    se corren y cada símbolo heredaría el rango de otro: exactamente la
    localización falsa que este mapa existe para no publicar. Antes de mentir,
    callar."""
    original = tmp_path / "repo" / "pkg"
    original.mkdir(parents=True)
    (original / "core.py").write_text(
        "def alpha(x):\n    return x\n\n\ndef beta(x):\n    return x\n", encoding="utf-8"
    )
    symbols = build_symbol_map(tmp_path / "repo")

    transformed = tmp_path / "work" / "pkg"
    transformed.mkdir(parents=True)
    (transformed / "core.py").write_text("def beta(x):\n    return x\n", encoding="utf-8")

    relocated = relocate_symbols(symbols, tmp_path / "work")

    assert "pkg.core.alpha" not in relocated


def test_a_symbol_that_changed_module_keeps_its_identity(tmp_path: Path):
    """B2 renombra los ficheros: sin seguir el movimiento, el módulo original no
    existe en el árbol transformado y sus símbolos se caen del mapa entero."""
    original = tmp_path / "before"
    (original / "pkg" / "es").mkdir(parents=True)
    (original / "pkg" / "es" / "nif.py").write_text(
        "def validate(number):\n    return number\n", encoding="utf-8"
    )
    symbols = build_symbol_map(original)

    after = tmp_path / "after"
    (after / "pkg").mkdir(parents=True)
    (after / "pkg" / "m17.py").write_text(
        "def validate(number):\n    return number\n", encoding="utf-8"
    )

    relocated = relocate_symbols(symbols, after, moves={"pkg.es.nif": "pkg.m17"})

    assert relocated["pkg.es.nif.validate"].path == "pkg/m17.py"
    assert relocated["pkg.es.nif.validate"].current_name == "validate"


def test_without_a_move_a_vanished_module_still_drops_out(tmp_path: Path):
    """Lo que no se puede verificar contra el árbol que ve el agente no se
    publica: un rango inventado es peor que ningún rango."""
    original = tmp_path / "before"
    (original / "pkg").mkdir(parents=True)
    (original / "pkg" / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    symbols = build_symbol_map(original)

    after = tmp_path / "after"
    (after / "pkg").mkdir(parents=True)

    assert relocate_symbols(symbols, after) == {}


def test_a_move_that_lands_nowhere_does_not_fall_back_to_the_old_module(tmp_path: Path):
    """Seguir el movimiento no puede convertirse en buscar por ahí: si el
    destino anunciado no está en el árbol, el símbolo se calla. Un rescate por
    el nombre viejo publicaría el rango de un fichero que el agente no ve —o
    peor, el de un módulo homónimo que la transformación dejó atrás."""
    original = tmp_path / "before"
    (original / "pkg").mkdir(parents=True)
    (original / "pkg" / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    symbols = build_symbol_map(original)

    # El árbol transformado conserva `pkg/core.py`, pero el movimiento dice que
    # ese símbolo se fue a `pkg.m3`, que no existe: no hay nada que publicar.
    after = tmp_path / "after"
    (after / "pkg").mkdir(parents=True)
    (after / "pkg" / "core.py").write_text("def other():\n    return 2\n", encoding="utf-8")

    assert relocate_symbols(symbols, after, moves={"pkg.core": "pkg.m3"}) == {}


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


def test_a_package_is_named_by_how_it_is_imported_not_by_its_file(tmp_path: Path):
    """`pkg/sub/__init__.py` se importa como `pkg.sub`, y esa es la única forma
    del nombre que sirve: es la clave con la que la familia B anuncia sus
    movimientos (`_module_name` en b2_hierarchy). Nombrarlo `pkg.sub.__init__`
    aquí hace que la búsqueda en `moves` falle siempre para los `__init__.py`, y
    los símbolos que declaran —el API pública del subpaquete— se caen del mapa
    sin una sola queja."""
    root = tmp_path / "repo"
    (root / "pkg" / "sub").mkdir(parents=True)
    (root / "pkg" / "sub" / "__init__.py").write_text(
        "class Widget:\n    def render(self):\n        return 1\n", encoding="utf-8"
    )

    symbols = build_symbol_map(root)

    assert sorted(symbols) == ["pkg.sub.Widget", "pkg.sub.Widget.render"]
    assert symbols["pkg.sub.Widget"].module == "pkg.sub"
    assert symbols["pkg.sub.Widget"].path == "pkg/sub/__init__.py"


def test_in_a_src_layout_the_map_follows_the_move_like_anywhere_else(tmp_path: Path):
    """El mismo cruce de nombres, en el layout donde el paquete no cuelga de la
    raíz. `src/pkg/es/nif.py` se importa como `pkg.es.nif`, y esa tiene que ser
    también la clave del mapa: si aquí se llamara `src.pkg.es.nif` y la familia
    B anunciara `pkg.es.nif`, la búsqueda en `moves` no encontraría nada y todos
    los símbolos del paquete se caerían del mapa a la vez —callar donde §5.4.2
    pide procedencia—."""
    from acp.transforms import b2_hierarchy

    root = tmp_path / "repo"
    package = root / "src" / "pkg" / "es"
    package.mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "nif.py").write_text(
        "def validate(number):\n    return number.strip()\n", encoding="utf-8"
    )
    symbols = build_symbol_map(root)
    assert "pkg.es.nif.validate" in symbols

    moves = b2_hierarchy.apply(root).moves
    relocated = relocate_symbols(symbols, root, moves=moves)

    target = moves["pkg.es.nif"].split(".")[-1]
    assert relocated["pkg.es.nif.validate"].path == f"src/pkg/{target}.py"
