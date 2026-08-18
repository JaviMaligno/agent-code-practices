from pathlib import Path

from acp.transforms.base import TransformResult, copy_tree


def test_copy_tree_leaves_the_original_untouched(tmp_path: Path):
    source = tmp_path / "repo"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")

    destination = copy_tree(source, tmp_path / "work")
    (destination / "pkg" / "core.py").write_text("x = 2\n", encoding="utf-8")

    assert (source / "pkg" / "core.py").read_text(encoding="utf-8") == "x = 1\n"


def test_copy_tree_keeps_the_git_directory_out(tmp_path: Path):
    """El .git de un clon pesa más que el código y no se transforma nunca."""
    source = tmp_path / "repo"
    (source / ".git").mkdir(parents=True)
    (source / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (source / "pkg").mkdir()
    (source / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")

    destination = copy_tree(source, tmp_path / "work")

    assert (destination / "pkg" / "core.py").exists()
    assert not (destination / ".git").exists()


# Lo que un clon usado arrastra, con el rastro que cada cosa deja: no es una
# lista de nombres feos, es una lista de fugas. `build/lib/**` es una copia
# literal de las fuentes con los nombres y la jerarquía de antes de transformar;
# `.pytest_cache/v/cache/nodeids` y `*.egg-info/SOURCES.txt` nombran los tests
# que B4 acaba de sacar del árbol; `__pycache__` conserva el árbol de módulos
# que B2 aplana; `.tox`/`.venv` traen el paquete instalado, es decir el código
# original otra vez.
ARTIFACTS = {
    "__pycache__/core.cpython-312.pyc": "core original",
    "pkg/__pycache__/core.cpython-312.pyc": "core original",
    ".pytest_cache/v/cache/nodeids": '["tests/test_core.py::test_secreto"]',
    ".mypy_cache/3.12/pkg/core.json": "{}",
    ".ruff_cache/content": "x",
    ".tox/py312/lib/pkg/core.py": "def f(): return 1",
    ".nox/tests/lib/pkg/core.py": "def f(): return 1",
    ".eggs/setuptools_scm.egg/scm.py": "x = 1",
    ".venv/lib/pkg/core.py": "def f(): return 1",
    "venv/lib/pkg/core.py": "def f(): return 1",
    ".hypothesis/examples/deadbeef": "x",
    ".coverage": "sqlite",
    ".coverage.host.4242": "sqlite",
    "build/lib/pkg/core.py": "def f(): return 1",
    "dist/pkg-0.1.0.tar.gz": "tar",
    "pkg.egg-info/SOURCES.txt": "tests/test_core.py",
    "src/pkg.egg-info/SOURCES.txt": "tests/test_core.py",
    # Del propio pipeline: el entorno y la suite apartada viven fuera del árbol,
    # pero si alguna vez cayeran dentro, copiarlos sería enseñar el experimento.
    ".acp-venv-repo/bin/python": "x",
    ".acp-manifest.json": "{}",
    "repo.acp-tests/tests/test_core.py": "def test_secreto(): pass",
}

# Contenido del repositorio que empieza por punto o que se parece a un
# artefacto y sí tiene que viajar: sin `.coveragerc` o `.github` el árbol deja
# de ser el repositorio, y `vendor/` es código del que el repo depende para
# importar —no se transforma, pero borrarlo rompe la equivalencia—.
REPOSITORY_CONTENT = {
    "pkg/core.py": "def f():\n    return 1\n",
    ".coveragerc": "[run]\n",
    ".gitignore": "build/\n",
    ".github/workflows/ci.yml": "name: ci\n",
    "vendor/dep/__init__.py": "x = 1\n",
}


def write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_copy_tree_leaves_the_artifacts_of_a_used_clone_behind(tmp_path: Path):
    """Un clon sobre el que ya se corrió la suite lleva encima lo que la
    condición intenta quitar: copiarlo tal cual le devuelve al agente los
    nombres, la jerarquía y hasta la lista de tests que la transformación acaba
    de esconder."""
    source = tmp_path / "repo"
    write_tree(source, ARTIFACTS)
    write_tree(source, REPOSITORY_CONTENT)

    destination = copy_tree(source, tmp_path / "work")

    leaked = sorted(name for name in ARTIFACTS if (destination / name).exists())
    assert leaked == []


def test_copy_tree_still_carries_the_repository_content(tmp_path: Path):
    """El otro lado del mismo filtro: excluir de más deja al agente un repo que
    no compila, y una celda rota no mide nada. `vendor/` es el caso claro —no se
    transforma, pero el repo lo importa—."""
    source = tmp_path / "repo"
    write_tree(source, ARTIFACTS)
    write_tree(source, REPOSITORY_CONTENT)

    destination = copy_tree(source, tmp_path / "work")

    missing = sorted(name for name in REPOSITORY_CONTENT if not (destination / name).exists())
    assert missing == []


def test_copy_tree_drops_a_coverage_report_whatever_it_is_called(tmp_path: Path):
    """El HTML de coverage empotra el fuente entero: es el original con sus
    docstrings y sus nombres, servido en una carpeta que cada repo bautiza como
    quiere (`htmlcov`, `coverage`, ...). Por eso se reconoce por lo que contiene
    —`status.json` junto a `index.html`, que solo escribe coverage— y no por el
    nombre: un paquete que se llame `coverage` sí es del repositorio."""
    source = tmp_path / "repo"
    write_tree(
        source,
        {
            "cobertura/status.json": "{}",
            "cobertura/index.html": "<html>",
            "cobertura/core_py.html": "<html>def f(): return 1",
            "coverage/__init__.py": "x = 1\n",
            "coverage/control.py": "x = 1\n",
        },
    )

    destination = copy_tree(source, tmp_path / "work")

    assert not (destination / "cobertura").exists()
    assert (destination / "coverage" / "control.py").exists()


def test_a_result_reports_nothing_changed_by_default():
    assert TransformResult().files_changed == 0
    assert TransformResult().renames == {}


def test_transformable_files_include_the_repo_tests(tmp_path: Path):
    """§4.3.1: renombrar solo el código fuente deja los tests sin compilar, y
    entonces la condición mide otra cosa. Es justo lo contrario de lo que hace
    `iter_source_files`, que los excluye para no perfilarlos."""
    from acp.transforms.base import iter_transformable_files

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_core.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "generated.py").write_text("x = 1\n", encoding="utf-8")

    found = {path.relative_to(tmp_path).as_posix() for path in iter_transformable_files(tmp_path)}

    assert found == {"pkg/core.py", "tests/test_core.py"}


# Lo que escriben los OTROS formatos del mismo `coverage report`. El HTML lo
# reconoce `_is_coverage_report` por su par de ficheros, pero estos tres son
# ficheros sueltos con nombre fijo, y cada uno lleva dentro la lista de rutas
# del árbol de antes de transformar.
COVERAGE_REPORTS = {
    "coverage.xml": (
        '<?xml version="1.0" ?>\n<coverage>\n<packages><package name="pkg.sub">'
        '<classes><class filename="pkg/sub/deep.py"/></classes></package></packages>\n'
        "</coverage>\n"
    ),
    "coverage.json": '{"files": {"pkg/sub/deep.py": {"summary": {}}}}\n',
    "coverage.lcov": "SF:pkg/sub/deep.py\nend_of_record\n",
    "pkg/sub/deep.py,cover": "> def deep():\n>     return 1\n",
}


def test_copy_tree_drops_the_coverage_reports_that_are_not_html(tmp_path: Path):
    """El mismo comando que escribe el HTML escribe otros tres formatos, y el
    filtro solo tapaba el HTML.

    Los tres nombran los ficheros del repositorio por su ruta completa: dentro
    del árbol aplanado por B2, `coverage.xml` republica la jerarquía entera
    —`pkg/sub/deep.py`— que la condición acaba de destruir, y en un árbol sin
    suite (B4) el informe sigue listando los ficheros de test por su ruta. No
    es higiene: es la fuga que `NOT_COPYABLE` existe para tapar, y se cuela por
    el mismo sitio que las otras.

    `--cov-report=xml` es además el formato que escriben los repos en CI, así
    que llega en el clon sin que nadie corra nada aquí.
    """
    source = tmp_path / "repo"
    write_tree(source, COVERAGE_REPORTS)
    write_tree(source, REPOSITORY_CONTENT)

    destination = copy_tree(source, tmp_path / "work")

    leaked = sorted(name for name in COVERAGE_REPORTS if (destination / name).exists())
    assert leaked == []
    # El otro lado: la configuración de coverage la lee el agente y es del repo.
    assert (destination / ".coveragerc").exists()
