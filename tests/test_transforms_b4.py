from pathlib import Path

from acp.cli import transform_repo
from acp.transforms import b4_tests


def build(root: Path) -> None:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_core.py").write_text(
        "from pkg.core import f\n\n\ndef test_f():\n    assert f() == 1\n", encoding="utf-8"
    )
    (root / "conftest.py").write_text("", encoding="utf-8")


def test_the_suite_leaves_the_tree(tmp_path: Path):
    root = tmp_path / "work"
    build(root)

    b4_tests.apply(root)

    assert not (root / "tests").exists()


def test_the_suite_is_kept_outside_and_intact(tmp_path: Path):
    """Los tests de validación se ejecutan fuera del alcance del agente y no se
    tocan nunca (§4.2): ocultarlos no puede significar perderlos."""
    root = tmp_path / "work"
    build(root)

    b4_tests.apply(root)

    kept = tmp_path / "work.acp-tests" / "tests" / "test_core.py"
    assert kept.exists()
    assert "def test_f()" in kept.read_text(encoding="utf-8")


def test_the_kept_suite_never_lands_inside_the_tree(tmp_path: Path):
    """Dentro del árbol, el agente la encuentra con un `ls` y B4 no mide nada."""
    root = tmp_path / "work"
    build(root)

    b4_tests.apply(root)

    assert not any(path.name.startswith("acp-tests") for path in root.rglob("*"))


def test_the_package_itself_is_not_confused_with_the_suite(tmp_path: Path):
    """Un paquete que se llame `testing` o un módulo `test_utils.py` dentro del
    código fuente no son la suite: llevárselos cambiaría el programa."""
    root = tmp_path / "work"
    build(root)
    (root / "pkg" / "testing").mkdir()
    (root / "pkg" / "testing" / "__init__.py").write_text("HELPER = 1\n", encoding="utf-8")

    b4_tests.apply(root)

    assert (root / "pkg" / "testing" / "__init__.py").exists()


def test_the_root_conftest_leaves_with_the_suite(tmp_path: Path):
    """El conftest de la raíz es maquinaria de la suite —fixtures, plugins, los
    paths que registra—: dejarlo en el árbol le enseña al agente la mitad de lo
    que B4 esconde, y sacarlo sin conservarlo rompería la verificación."""
    root = tmp_path / "work"
    build(root)

    b4_tests.apply(root)

    assert not (root / "conftest.py").exists()
    assert (tmp_path / "work.acp-tests" / "conftest.py").exists()


def test_the_kept_suite_mirrors_the_tree_so_it_can_be_put_back(tmp_path: Path):
    """La verificación restaura la suite volcando lo guardado sobre la raíz del
    árbol dentro del contenedor. Si lo guardado no conservara la ruta relativa,
    los ficheros volverían a otro sitio y la configuración de pytest del repo
    —que nombra `tests` como ruta de colecta— dejaría de encontrarlos."""
    root = tmp_path / "work"
    build(root)

    b4_tests.apply(root)

    kept = b4_tests.kept_suite_path(root)
    assert sorted(path.relative_to(kept).as_posix() for path in kept.rglob("*")) == [
        "conftest.py",
        "tests",
        "tests/test_core.py",
    ]


def test_the_kept_suite_keeps_files_that_are_not_python(tmp_path: Path):
    """Comprobado contra python-stdnum: de los 170 ficheros de su suite, **ni
    uno solo es `.py`** —169 son `.doctest` y uno es un `.dat` binario—, así que
    una B4 que moviera solo módulos de Python se llevaría un directorio vacío y
    dejaría la suite entera sin restaurar. Nada lo pinchaba: el test del espejo
    compara nombres de ruta sobre un fixture que solo tiene `.py`.
    """
    root = tmp_path / "work"
    build(root)
    (root / "tests" / "test_nif.doctest").write_text(">>> 1 + 1\n2\n", encoding="utf-8")
    (root / "tests" / "numdb-test.dat").write_bytes(b"\x00\x01binario\xff")

    b4_tests.apply(root)

    kept = b4_tests.kept_suite_path(root) / "tests"
    assert kept.joinpath("test_nif.doctest").read_text(encoding="utf-8") == ">>> 1 + 1\n2\n"
    assert kept.joinpath("numdb-test.dat").read_bytes() == b"\x00\x01binario\xff"


def test_a_repo_without_a_suite_keeps_nothing(tmp_path: Path):
    """Un directorio guardado vacío es peor que ninguno: la verificación lo
    volcaría sin restaurar nada y la corrida se leería como una suite que no
    colecta, o sea como un fracaso, cuando lo que pasa es que B4 no encontró
    nada que esconder."""
    root = tmp_path / "work"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    result = b4_tests.apply(root)

    assert result.files_changed == 0
    assert not b4_tests.kept_suite_path(root).exists()


def test_a_suite_nested_inside_the_package_is_left_alone(tmp_path: Path):
    """Límite declarado, no descuido: es la forma de pint (`pint/testsuite/`).

    Un directorio de tests dentro del paquete puede ser importado por el propio
    código fuente, y como la verificación **restaura** la suite antes de correr,
    un import roto por habérsela llevado no lo vería nadie: el contenedor
    pasaría y el árbol del agente estaría roto en silencio. Se paga en dosis
    —en pint B4 no esconde nada— y quien escriba los resultados lo declara con
    `suite_paths`.
    """
    root = tmp_path / "work"
    build(root)
    (root / "pkg" / "testsuite").mkdir()
    (root / "pkg" / "testsuite" / "test_registry.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    b4_tests.apply(root)

    assert (root / "pkg" / "testsuite" / "test_registry.py").exists()


def test_the_dose_can_be_declared_before_applying(tmp_path: Path):
    """Igual que en B3: quien escriba los resultados tiene que poder decir qué
    se llevó B4 en cada repo sin deducirlo de un contador de ficheros."""
    root = tmp_path / "work"
    build(root)

    found = b4_tests.suite_paths(root)

    assert [path.relative_to(root).as_posix() for path in found] == [
        "conftest.py",
        "tests",
    ]


def build_with_a_readme_the_suite_verifies(root: Path) -> None:
    """La forma de holidays: su suite comprueba que las tablas del README listan
    todo lo soportado, así que ahí el README es contrato y B3 no lo toca."""
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# demo\n\n| país |\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_docs.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def test_the_readme_lists_everything():\n"
        "    assert 'país' in Path('README.md').read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )


def test_b3_still_sees_the_suite_when_b4_is_asked_for_first(tmp_path: Path):
    """B4 esconde la suite, y B3 decide mirándola si el README es contrato: en
    el orden en que se piden, `--apply B4,B3` deja a B3 sin nada que mirar,
    vacía un README que la suite verifica, y el fallo aparece en la corrida de
    validación —donde la suite sí existe— y no en el árbol. El orden canónico es
    parte de la condición, no del capricho de quien escribe los flags."""
    source = tmp_path / "repo"
    build_with_a_readme_the_suite_verifies(source)

    work = transform_repo(source, ["B4", "B3"], tmp_path / "work")

    assert (tmp_path / "work.acp-tests" / "tests" / "test_docs.py").exists()
    assert "país" in (work / "README.md").read_text(encoding="utf-8")
