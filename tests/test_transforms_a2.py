from pathlib import Path

from acp.transforms import a2_names


def build(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    path = pkg / "billing.py"
    path.write_text(
        "TAX_RATE = 0.21\n"
        "\n"
        "\n"
        "def apply_tax(amount):\n"
        "    return amount * (1 + TAX_RATE)\n"
        "\n"
        "\n"
        "def total(amount):\n"
        "    return apply_tax(amount)\n",
        encoding="utf-8",
    )
    return path


def test_definitions_and_their_uses_are_renamed_together(tmp_path: Path):
    path = build(tmp_path)

    a2_names.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "def apply_tax" not in source
    assert "apply_tax(" not in source
    compile(source, "billing.py", "exec")


def test_the_dictionary_travels_with_the_result(tmp_path: Path):
    """El enunciado de la tarea se transforma con el mismo diccionario
    (§4.3.2): si no viaja, el enunciado habla de un código que ya no existe."""
    build(tmp_path)

    result = a2_names.apply(tmp_path)

    assert result.renames["apply_tax"].startswith("f")
    assert result.renames["TAX_RATE"].startswith("C")


def test_the_root_package_name_is_never_touched(tmp_path: Path):
    """Es lo único que mantiene válidos a la vez la instalación, los imports
    desde fuera y el comando de test (§5.6)."""
    build(tmp_path)

    result = a2_names.apply(tmp_path)

    assert "pkg" not in result.renames


def test_dunder_and_stdlib_names_are_left_alone(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    path = pkg / "core.py"
    path.write_text(
        "import os\n"
        "\n"
        "\n"
        "class Thing:\n"
        "    def __init__(self, value):\n"
        "        self.value = value\n"
        "\n"
        "    def path(self):\n"
        "        return os.path.join('a', 'b')\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "__init__" in source
    assert "os.path.join" in source
    assert "os" not in result.renames


def test_the_repo_tests_are_updated_but_not_renamed(tmp_path: Path):
    """Los tests se transforman (§4.3.1) pero sus propios nombres no: pytest los
    colecta por nombre, y renombrarlos dejaría la suite sin encontrar nada."""
    build(tmp_path)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_billing.py").write_text(
        "from pkg.billing import apply_tax\n"
        "\n"
        "\n"
        "def test_apply_tax():\n"
        "    assert apply_tax(100) > 100\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = (tests / "test_billing.py").read_text(encoding="utf-8")
    assert "def test_apply_tax" in source
    assert "apply_tax(100)" not in source
    assert result.renames["apply_tax"] in source


def test_names_reachable_by_string_are_left_alone(tmp_path: Path):
    """Renombrar lo que se alcanza por getattr rompe el programa, y el fallo se
    leería como un agente que fracasa (§4.3.3)."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    path = pkg / "core.py"
    path.write_text(
        "def handler(x):\n"
        "    return x\n"
        "\n"
        "\n"
        "def dispatch(name, x):\n"
        "    return globals()[name](x)\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    assert "handler" not in result.renames
    assert "def handler" in path.read_text(encoding="utf-8")
