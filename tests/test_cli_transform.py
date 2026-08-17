import json
from pathlib import Path

from acp.cli import main, manifest_path_for, transform_repo


def relative_paths(root: Path) -> set[str]:
    """Todo lo que hay en un árbol, tal y como lo vería un `ls -R` del agente."""
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if ".git" not in path.relative_to(root).parts
    }


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

    manifest = json.loads(manifest_path_for(destination).read_text(encoding="utf-8"))
    assert manifest["applied"] == ["A1", "A4"]
    assert manifest["symbols"]["pkg.core.rate"]["current_name"] == "rate"


def build_shifted(root: Path) -> Path:
    """Un módulo con todo lo que las transformaciones desplazan: líneas en
    blanco (las borra A3), docstrings (las borra A4) y anotaciones (A1)."""
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "core.py").write_text(
        "import os\n"
        "\n"
        "\n"
        "def alpha(value: int) -> int:\n"
        '    """Duplica."""\n'
        "    return value * 2\n"
        "\n"
        "\n"
        "class Widget:\n"
        '    """Un trasto."""\n'
        "\n"
        "    def render(self) -> str:\n"
        "        return str(os.sep)\n",
        encoding="utf-8",
    )
    return root


def declared_line(root: Path, location: dict) -> str:
    """La línea del árbol transformado a la que apunta el manifiesto."""
    lines = (root / location["path"]).read_text(encoding="utf-8").splitlines()
    return lines[location["start"] - 1]


def test_the_symbol_ranges_point_at_the_transformed_tree(tmp_path: Path):
    """El mapa se publica para proyectar sobre él lo que el agente lee (§5.4.2),
    y el agente lee el árbol transformado. A3 borra líneas en blanco y A4
    docstrings: un rango medido sobre el original señala otra cosa, y una
    localización falsa es peor que ninguna porque nadie la ve venir."""
    for index, transforms in enumerate((["A3"], ["A4"], ["A1", "A4", "A3"])):
        source = build_shifted(tmp_path / f"repo{index}")
        destination = transform_repo(source, transforms, tmp_path / f"work{index}")

        symbols = json.loads(
            manifest_path_for(destination).read_text(encoding="utf-8")
        )["symbols"]

        assert declared_line(destination, symbols["pkg.core.alpha"]).startswith("def alpha")
        assert declared_line(destination, symbols["pkg.core.Widget"]).startswith("class Widget")
        assert "def render" in declared_line(destination, symbols["pkg.core.Widget.render"])


def test_the_symbol_range_ends_where_the_definition_ends(tmp_path: Path):
    """El rango completo, no solo su primera línea: lo que se proyecta es la
    región, así que el final tiene que caer dentro del fichero transformado y
    cubrir el cuerpo entero de la definición."""
    source = build_shifted(tmp_path / "repo")
    destination = transform_repo(source, ["A3"], tmp_path / "work")

    symbols = json.loads(manifest_path_for(destination).read_text(encoding="utf-8"))["symbols"]
    location = symbols["pkg.core.alpha"]
    lines = (destination / location["path"]).read_text(encoding="utf-8").splitlines()

    assert location["end"] <= len(lines)
    region = lines[location["start"] - 1 : location["end"]]
    assert any("return value" in line for line in region)
    # El rango no puede desbordar hacia la definición siguiente: proyectar una
    # lectura de `Widget` como si fuera de `alpha` es la misma mentira al revés.
    assert not any("class Widget" in line for line in region)


def test_the_published_name_is_the_one_written_in_the_code(tmp_path: Path):
    """A2 deja los métodos como estaban a propósito (renombrar `self.algo` deja
    un AttributeError que se lee como un agente que fracasa). Si el manifiesto
    los renombra igualmente porque comparten nombre con una función de módulo,
    publica un nombre que no existe en ningún fichero, y la localización
    resuelve el símbolo equivocado (§5.4.2)."""
    source = tmp_path / "repo"
    pkg = source / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "report.py").write_text("def info(rows):\n    return rows\n", encoding="utf-8")
    (pkg / "store.py").write_text(
        "class Store:\n    def info(self):\n        return 1\n", encoding="utf-8"
    )

    destination = transform_repo(source, ["A2"], tmp_path / "work")

    symbols = json.loads(manifest_path_for(destination).read_text(encoding="utf-8"))["symbols"]
    for key, location in symbols.items():
        assert location["current_name"] in declared_line(destination, location), key
    assert symbols["pkg.store.Store.info"]["current_name"] == "info"
    assert symbols["pkg.report.info"]["current_name"] != "info"


def test_the_transformed_tree_gains_nothing_that_the_original_did_not_have(tmp_path: Path):
    """El árbol transformado es lo que explora el agente: cualquier fichero que
    el pipeline deje dentro es material del experimento filtrado al sujeto de
    estudio, y además una diferencia extra entre condición y control. El
    manifiesto es el caso concreto —lleva el diccionario original→opaco y el
    rango de cada símbolo objetivo, o sea la clave de A2 y la respuesta de la
    métrica de localización (§5.4.1, §5.4.2)—, pero la aserción es general a
    propósito: vale para el siguiente artefacto que se le ocurra a alguien."""
    source = build(tmp_path / "repo")
    before = relative_paths(source)

    destination = transform_repo(source, ["A1", "A2", "A4"], tmp_path / "work")

    assert relative_paths(destination) == before


def test_the_manifest_lands_beside_the_tree(tmp_path: Path):
    """Fuera del árbol, pero atado a él por el nombre: la procedencia no sirve
    de nada si no se sabe a qué condición pertenece."""
    source = build(tmp_path / "repo")

    destination = transform_repo(source, ["A2"], tmp_path / "work")

    manifest_path = manifest_path_for(destination)
    assert manifest_path.parent == destination.parent
    assert destination.name in manifest_path.name
    assert manifest_path.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["applied"] == ["A2"]


def test_an_explicit_manifest_path_inside_the_tree_is_rejected(tmp_path: Path):
    """Sacarlo por defecto no basta si el parámetro explícito permite volver a
    meterlo: quien pase una ruta dentro del árbol tiene que enterarse antes de
    gastar una corrida, no después de mirar los resultados."""
    source = build(tmp_path / "repo")

    try:
        transform_repo(
            source, ["A2"], tmp_path / "work", manifest=tmp_path / "work" / "provenance.json"
        )
    except ValueError as error:
        assert "árbol" in str(error)
    else:
        raise AssertionError("debería haber rechazado un manifiesto dentro del árbol")


def test_the_subcommand_writes_the_manifest_where_it_is_asked_to(tmp_path: Path):
    """La procedencia de la campaña se acumula fuera, en su propio directorio."""
    source = build(tmp_path / "repo")
    manifest_path = tmp_path / "provenance" / "work.json"

    code = main([
        "transform", str(source), "--apply", "A1", "--out", str(tmp_path / "work"),
        "--manifest", str(manifest_path),
    ])

    assert code == 0
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["applied"] == ["A1"]
    assert relative_paths(tmp_path / "work") == relative_paths(source)


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
