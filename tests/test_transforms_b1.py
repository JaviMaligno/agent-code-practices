from pathlib import Path

from acp.transforms import b1_cohesion


def build(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "billing.py").write_text(
        "TAX = 0.21\n"
        "\n"
        "\n"
        "def rate(amount):\n"
        "    return amount * TAX\n"
        "\n"
        "\n"
        "def total(rows):\n"
        "    return sum(rate(row) for row in rows)\n",
        encoding="utf-8",
    )
    (pkg / "report.py").write_text(
        "def render(rows):\n"
        "    return ', '.join(str(row) for row in rows)\n"
        "\n"
        "\n"
        "def header():\n"
        "    return 'informe'\n",
        encoding="utf-8",
    )


def test_the_definitions_end_up_somewhere_else(tmp_path: Path):
    build(tmp_path)

    result = b1_cohesion.apply(tmp_path, seed=1)

    assert result.symbol_moves, "no movió ninguna definición"
    origen = {key.rsplit(".", 1)[0] for key in result.symbol_moves}
    destino = set(result.symbol_moves.values())
    assert origen != destino or any(
        key.rsplit(".", 1)[0] != value for key, value in result.symbol_moves.items()
    )


def test_the_number_of_files_does_not_change(tmp_path: Path):
    """B1 rompe la organización SIN tocar el tamaño; el tamaño es B5. Si las dos
    cosas cambian a la vez, ninguna de las dos celdas es atribuible (§4.2)."""
    build(tmp_path)
    antes = sorted(p.name for p in (tmp_path / "pkg").glob("*.py"))

    b1_cohesion.apply(tmp_path, seed=1)

    assert sorted(p.name for p in (tmp_path / "pkg").glob("*.py")) == antes


def test_the_code_still_runs(tmp_path: Path):
    """Lo que una definición necesita —una constante del módulo, otra función—
    tiene que viajar con ella o importarse en el destino, o el primer uso da
    NameError."""
    build(tmp_path)

    b1_cohesion.apply(tmp_path, seed=1)

    import subprocess
    import sys

    proceso = subprocess.run(
        [sys.executable, "-c",
         "import pkg.billing, pkg.report; "
         "mods = [pkg.billing, pkg.report]; "
         "f = [getattr(m, 'total') for m in mods if hasattr(m, 'total')][0]; "
         "print(f([100]))"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proceso.returncode == 0, proceso.stderr


def test_the_same_seed_produces_the_same_tree(tmp_path: Path):
    """Sin esto, dos corridas de la misma celda no son la misma condición y los
    seeds del 2×2 dejan de ser comparables (§5.4.4)."""
    build(tmp_path / "una")
    build(tmp_path / "otra")

    primera = b1_cohesion.apply(tmp_path / "una", seed=7)
    segunda = b1_cohesion.apply(tmp_path / "otra", seed=7)

    assert primera.symbol_moves == segunda.symbol_moves


def test_a_different_seed_produces_a_different_tree(tmp_path: Path):
    build(tmp_path / "una")
    build(tmp_path / "otra")

    primera = b1_cohesion.apply(tmp_path / "una", seed=1)
    segunda = b1_cohesion.apply(tmp_path / "otra", seed=2)

    assert primera.symbol_moves != segunda.symbol_moves


def test_a_moved_symbol_survives_in_the_manifest_next_to_the_renaming(tmp_path: Path):
    """La clave de `symbol_moves` es la del mapa de identidad: el nombre
    ORIGINAL. B1 solo puede publicarla si ve el árbol antes de que A2 renombre,
    y si no la publica el símbolo se cae del manifiesto sin que nada lo diga
    —exactamente el fallo en verde de la fase 2 (§5.4.2)."""
    import json

    from acp.cli import manifest_path_for, transform_repo

    source = tmp_path / "repo"
    build(source)

    destination = transform_repo(source, ["A2", "B1"], tmp_path / "work")

    manifest = json.loads(manifest_path_for(destination).read_text(encoding="utf-8"))
    movidas = {
        clave
        for clave, sitio in manifest["symbols"].items()
        if sitio["path"] != f"{clave.rsplit('.', 2)[0]}/{clave.rsplit('.', 2)[1]}.py"
    }
    assert movidas, "ningún símbolo movido llegó al manifiesto"
