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


def test_a_name_that_shadows_a_builtin_stays_out_of_the_dictionary(tmp_path: Path):
    """El repo define su propio `format`, pero en los demás módulos `format`
    sigue siendo el builtin. Como el diccionario va por nombre desnudo,
    renombrarlo convierte esas llamadas en un NameError."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "money.py").write_text(
        "def format(number):\n    return number\n",
        encoding="utf-8",
    )
    other = pkg / "report.py"
    other.write_text(
        "def render(number):\n    return format(number, '.2f')\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    assert "format" not in result.renames
    assert "format(number, '.2f')" in other.read_text(encoding="utf-8")
    assert "render" in result.renames


def test_the_public_list_follows_the_rename(tmp_path: Path):
    """`__all__` es una lista de cadenas, pero es la única de todas las cadenas
    que se resuelve estáticamente, y es la que decide qué trae un `import *`. Si
    no la seguimos, el import estrella deja de traer el símbolo y la suite falla
    por fontanería, no por el agente."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    billing = pkg / "billing.py"
    billing.write_text(
        '__all__ = ["apply_tax"]\n'
        "\n"
        "\n"
        "def apply_tax(number):\n"
        "    return number * 1.21\n",
        encoding="utf-8",
    )
    (pkg / "report.py").write_text(
        "from pkg.billing import *\n"
        "\n"
        "\n"
        "def render(number):\n"
        "    return apply_tax(number)\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = billing.read_text(encoding="utf-8")
    assert f'__all__ = ["{result.renames["apply_tax"]}"]' in source


def test_a_doctest_in_a_docstring_follows_the_rename(tmp_path: Path):
    """Un doctest no es documentación, es suite. python-stdnum corre la suya con
    `--doctest-modules`, y un `>>> apply_tax(...)` que apunta al nombre viejo es
    un NameError: medido, A2 sin esto pasa sus 413 tests a 413 fallos. La línea
    de ejemplo es código y resuelve estáticamente, como `__all__`."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    path = pkg / "billing.py"
    path.write_text(
        '"""Impuestos.\n'
        "\n"
        ">>> apply_tax(100)\n"
        "121.0\n"
        '"""\n'
        "\n"
        "\n"
        "def apply_tax(amount):\n"
        "    return amount * 1.21\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert f">>> {result.renames['apply_tax']}(100)" in source
    # La prosa de alrededor no es código y no se toca: eso sería A4.
    assert "Impuestos." in source


def test_the_prose_around_a_doctest_is_not_renamed(tmp_path: Path):
    """El diccionario va por nombre desnudo. Si se aplicara a la cadena entera,
    la palabra suelta de una frase cambiaría, y eso ya no es A2: es reescribir
    la documentación, que es A4/B3."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    path = pkg / "billing.py"
    path.write_text(
        '"""Usa apply_tax para el IVA.\n'
        "\n"
        ">>> apply_tax(100)\n"
        "121.0\n"
        '"""\n'
        "\n"
        "\n"
        "def apply_tax(amount):\n"
        "    return amount * 1.21\n",
        encoding="utf-8",
    )

    a2_names.apply(tmp_path)

    assert "Usa apply_tax para el IVA." in path.read_text(encoding="utf-8")


def test_a_multi_line_doctest_example_follows_the_rename(tmp_path: Path):
    """Las continuaciones `...` son parte del mismo ejemplo. Renombrar la
    primera línea y no el resto deja un ejemplo que no compila."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    path = pkg / "billing.py"
    path.write_text(
        '"""Impuestos.\n'
        "\n"
        ">>> for amount in (1, 2):\n"
        "...     print(apply_tax(amount))\n"
        '"""\n'
        "\n"
        "\n"
        "def apply_tax(amount):\n"
        "    return amount * 1.21\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert f"...     print({result.renames['apply_tax']}(amount))" in source


def test_an_expected_output_line_is_not_code(tmp_path: Path):
    """Lo que sigue a un ejemplo es lo que el programa imprime, no código. Un
    `...` de salida esperada dentro de un traceback no es una continuación, y
    tratarlo como tal rompería el ejemplo."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    path = pkg / "billing.py"
    path.write_text(
        '"""Impuestos.\n'
        "\n"
        ">>> apply_tax('x')\n"
        "Traceback (most recent call last):\n"
        "    ...\n"
        "TypeError: ...\n"
        '"""\n'
        "\n"
        "\n"
        "def apply_tax(amount):\n"
        "    return amount * 1.21\n",
        encoding="utf-8",
    )

    a2_names.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "Traceback (most recent call last):\n    ...\nTypeError: ...\n" in source


def test_a_doctest_file_the_suite_collects_follows_the_rename(tmp_path: Path):
    """python-stdnum tiene 17.465 líneas de doctest fuera de los .py, en los
    ficheros que sus `addopts` colectan con `--doctest-glob`. Son suite tanto
    como un test_*.py, y dejarlos atrás rompe el repo entero."""
    build(tmp_path)
    (tmp_path / "setup.cfg").write_text(
        "[tool:pytest]\naddopts = --doctest-modules --doctest-glob=\"*.doctest\"\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    doctest_file = tests / "test_billing.doctest"
    doctest_file.write_text(
        "Casos raros de facturación.\n"
        "\n"
        ">>> from pkg import billing\n"
        ">>> billing.apply_tax(100)\n"
        "121.0\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = doctest_file.read_text(encoding="utf-8")
    assert f">>> billing.{result.renames['apply_tax']}(100)" in source
    # La prosa sigue siendo prosa.
    assert "Casos raros de facturación." in source


def test_a_text_file_the_suite_does_not_collect_is_left_alone(tmp_path: Path):
    """Un README con ejemplos no lo ejecuta nadie, así que reescribirlo no
    arregla ninguna equivalencia — y sí contaminaría B3, que es justamente la
    condición sobre la documentación del repo."""
    build(tmp_path)
    readme = tmp_path / "README.rst"
    readme.write_text(">>> from pkg import billing\n>>> billing.apply_tax(100)\n", encoding="utf-8")

    a2_names.apply(tmp_path)

    assert ">>> billing.apply_tax(100)" in readme.read_text(encoding="utf-8")


def test_a_method_that_shares_a_name_with_a_module_level_function_keeps_it(tmp_path: Path):
    """El diccionario va por nombre desnudo, y en LibCST el nombre de un `def`
    también es un `Name`: el método de una clase se renombraba por parecerse a
    una función de otro módulo. Como sus llamadas son `obj.info()` y esas se
    dejan —no hay inferencia de tipos que las atribuya a su clase—, renombrar
    solo la definición deja un AttributeError. Medido en python-stdnum: 29 de
    los 46 fallos que quedaban."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "report.py").write_text("def info(number):\n    return number\n", encoding="utf-8")
    store = pkg / "store.py"
    store.write_text(
        "class Store:\n"
        "    def info(self):\n"
        "        return 1\n"
        "\n"
        "\n"
        "def describe():\n"
        "    return Store().info()\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = store.read_text(encoding="utf-8")
    assert "def info(self):" in source
    # La clase sí se mueve entera —definición y uso a la vez—, y el método que
    # cuelga de ella se queda donde estaba.
    assert f"{result.renames['Store']}().info()" in source
    # La función de nivel de módulo sí se renombra: es la definición y el uso a
    # la vez lo que tiene que moverse junto.
    assert "info" in result.renames


def test_a_class_attribute_that_shares_a_name_keeps_it(tmp_path: Path):
    """Mismo caso que el método: se lee como `Klass.LIMIT`, y el atributo de
    cualquier cosa se deja pasar."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "config.py").write_text("LIMIT = 10\n", encoding="utf-8")
    store = pkg / "store.py"
    store.write_text(
        "class Store:\n"
        "    LIMIT = 5\n"
        "\n"
        "\n"
        "def cap():\n"
        "    return Store.LIMIT\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    source = store.read_text(encoding="utf-8")
    assert "    LIMIT = 5" in source
    assert f"{result.renames['Store']}.LIMIT" in source


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


def test_a_name_that_another_module_reaches_by_getattr_is_left_alone(tmp_path: Path):
    """El guardarraíl de acceso dinámico sacaba del diccionario las definiciones
    del módulo que LLAMA a getattr, no las que el getattr ALCANZA. El símbolo
    vive en otro fichero que no usa getattr, así que se renombraba mientras la
    cadena se quedaba como estaba: AttributeError en ejecución con el árbol
    compilando entero (§4.3.3)."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    core = pkg / "core.py"
    core.write_text("def handler(x):\n    return x\n", encoding="utf-8")
    (pkg / "dispatch.py").write_text(
        "import importlib\n"
        "\n"
        "\n"
        "def run(x):\n"
        "    module = importlib.import_module('pkg.core')\n"
        "    return getattr(module, 'handler')(x)\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    assert "handler" not in result.renames
    assert "def handler(x):" in core.read_text(encoding="utf-8")


def test_a_name_that_a_table_of_strings_reaches_is_left_alone(tmp_path: Path):
    """El patrón de holidays: el registro guarda el nombre de la clase en una
    tabla de cadenas y lo resuelve con `getattr(módulo, entrada)`. El getattr no
    lleva el nombre dentro —lo lleva la tabla—, así que ni siquiera excluir el
    módulo que hace el getattr salva a la clase, que además vive en otro
    fichero."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    countries = pkg / "countries.py"
    countries.write_text(
        "class Spain:\n    def days(self):\n        return []\n", encoding="utf-8"
    )
    (pkg / "registry.py").write_text(
        "import importlib\n"
        "\n"
        "ENTITIES = {'spain': ('Spain', 'ES')}\n"
        "\n"
        "\n"
        "def load(code):\n"
        "    module = importlib.import_module('pkg.countries')\n"
        "    return getattr(module, ENTITIES[code][0])\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    assert "Spain" not in result.renames
    assert "class Spain:" in countries.read_text(encoding="utf-8")


def test_a_class_a_registry_indexes_by_its_own_name_is_left_alone(tmp_path: Path):
    """El patrón de sqlglot: una metaclase registra cada dialecto con
    `clsname.lower()`, así que el NOMBRE de la clase es una clave de la API
    pública y no aparece ningún getattr en ninguna parte. Renombrar `Postgres`
    deja `transpile(..., write='postgres')` sin dialecto, con un mensaje que se
    autocontradice."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "dialect.py").write_text(
        "class Registry(type):\n"
        "    classes = {}\n"
        "\n"
        "    def __new__(cls, clsname, bases, attrs):\n"
        "        klass = super().__new__(cls, clsname, bases, attrs)\n"
        "        Registry.classes[clsname.lower()] = klass\n"
        "        return klass\n"
        "\n"
        "\n"
        "class Dialect(metaclass=Registry):\n"
        "    pass\n",
        encoding="utf-8",
    )
    postgres = pkg / "postgres.py"
    postgres.write_text(
        "from pkg.dialect import Dialect\n"
        "\n"
        "\n"
        "class Postgres(Dialect):\n"
        "    pass\n",
        encoding="utf-8",
    )
    (pkg / "api.py").write_text(
        "from pkg.dialect import Registry\n"
        "\n"
        "\n"
        "def dialect(write):\n"
        "    return Registry.classes[write]\n"
        "\n"
        "\n"
        "def default():\n"
        "    return dialect('postgres')\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    assert "Postgres" not in result.renames
    assert "class Postgres(" in postgres.read_text(encoding="utf-8")


def test_a_symbol_reached_through_a_module_object_is_left_alone(tmp_path: Path):
    """El módulo que trae el símbolo se decide en tiempo de ejecución, así que
    `mod.validate` no se puede atribuir a ningún fichero sin ejecutar el
    programa: es alcanzable por cadena (§4.3.3). La definición está a la vista y
    resulta tentadora, pero renombrarla sola deja un AttributeError. Es el
    patrón de python-stdnum —`__import__('stdnum.%s' % cc)` y luego
    `getattr(mod, 'validate')`— y era la causa de los 14 fallos que quedaban."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    country = pkg / "fr.py"
    country.write_text("def validate(number):\n    return number\n", encoding="utf-8")
    registry = pkg / "registry.py"
    registry.write_text(
        "def lookup(cc, number):\n"
        "    mod = __import__('pkg.%s' % cc, globals(), locals(), ['validate'])\n"
        "    return mod.validate(number)\n",
        encoding="utf-8",
    )

    result = a2_names.apply(tmp_path)

    assert "validate" not in result.renames
    assert "def validate(number):" in country.read_text(encoding="utf-8")
    assert "mod.validate(number)" in registry.read_text(encoding="utf-8")
