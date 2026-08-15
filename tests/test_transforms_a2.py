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


def test_an_attribute_of_something_ajeno_is_not_renamed(tmp_path: Path):
    """`','.join(...)` no es el `join` del repo, solo se llama igual. El
    diccionario va por nombre desnudo, así que renombrar el atributo convierte
    la llamada en un AttributeError: un repo roto se lee igual que un agente que
    fracasa (§4.3)."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    path = pkg / "core.py"
    path.write_text(
        "def join(parts):\n"
        "    return parts\n"
        "\n"
        "\n"
        "def render(parts):\n"
        "    return ','.join(parts)\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "','.join(parts)" in source
    # El símbolo del repo sí se renombra: lo que no puede es arrastrar consigo
    # los atributos ajenos que comparten nombre.
    assert "def join" not in source
    assert result.renames["join"] in source


def test_an_attribute_of_a_module_of_the_repo_follows_the_rename(tmp_path: Path):
    """El contraejemplo del test anterior: `billing.apply_tax(...)` sí es el
    símbolo del repo, y si la definición se renombra y el uso cualificado no, el
    módulo deja de resolver."""
    build(tmp_path)
    (tmp_path / "pkg" / "report.py").write_text(
        "from pkg import billing\n"
        "\n"
        "\n"
        "def render(amount):\n"
        "    return billing.apply_tax(amount)\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = (tmp_path / "pkg" / "report.py").read_text(encoding="utf-8")
    assert f"billing.{result.renames['apply_tax']}(" in source
    assert "apply_tax" not in source


def test_a_keyword_argument_keeps_its_name(tmp_path: Path):
    """La palabra clave de `json.dumps(..., indent=...)` es la firma de otro,
    no un símbolo del repo. Renombrarla llama a dumps con un argumento que no
    existe, y el TypeError se lee como un agente que fracasa."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    path = pkg / "core.py"
    path.write_text(
        "import json\n"
        "\n"
        "indent = 2\n"
        "\n"
        "\n"
        "def render(data):\n"
        "    return json.dumps(data, indent=indent)\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert f"json.dumps(data, indent={result.renames['indent']})" in source


def test_a_name_that_is_also_a_parameter_stays_out_of_the_dictionary(tmp_path: Path):
    """Un parámetro es local y sus llamantes pueden pasarlo por palabra clave.
    Renombrar el símbolo del módulo y el parámetro a la vez rompe esas llamadas;
    renombrar solo uno de los dos, el cuerpo de la función. Con el mismo nombre
    significando dos cosas no hay renombrado resoluble estáticamente (§4.3.3)."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    path = pkg / "core.py"
    path.write_text(
        "def amount(x):\n"
        "    return x\n"
        "\n"
        "\n"
        "def charge(amount):\n"
        "    return amount * 2\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "amount" not in result.renames
    assert "def amount(x):" in source
    assert f"def {result.renames['charge']}(amount):" in source
    compile(source, "core.py", "exec")


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
