from pathlib import Path

from acp.transforms import b2_hierarchy


def build(root: Path) -> None:
    pkg = root / "pkg"
    (pkg / "es").mkdir(parents=True)
    (pkg / "iso").mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "util.py").write_text("def clean(x):\n    return x.strip()\n", encoding="utf-8")
    (pkg / "es" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "es" / "nif.py").write_text(
        "from pkg.util import clean\n"
        "\n"
        "\n"
        "def validate(number):\n"
        "    return clean(number)\n",
        encoding="utf-8",
    )
    (pkg / "iso" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "iso" / "mod97.py").write_text(
        "from pkg.es.nif import validate\n"
        "\n"
        "\n"
        "def check(number):\n"
        "    return validate(number)\n",
        encoding="utf-8",
    )


def test_the_directories_inside_the_package_are_gone(tmp_path: Path):
    build(tmp_path)

    b2_hierarchy.apply(tmp_path)

    assert not (tmp_path / "pkg" / "es").exists()
    assert not (tmp_path / "pkg" / "iso").exists()


def test_the_root_package_survives(tmp_path: Path):
    """Es lo único que mantiene válidos a la vez los imports desde fuera y el
    comando de test (§5.6). Aplanarlo también dejaría el repo sin punto de
    entrada."""
    build(tmp_path)

    b2_hierarchy.apply(tmp_path)

    assert (tmp_path / "pkg" / "__init__.py").exists()


def test_files_are_renamed_to_opaque_names(tmp_path: Path):
    build(tmp_path)

    b2_hierarchy.apply(tmp_path)

    names = sorted(path.name for path in (tmp_path / "pkg").glob("*.py"))
    assert "__init__.py" in names
    assert any(name.startswith("m") and name[1:-3].isdigit() for name in names)
    assert "nif.py" not in names


def test_the_imports_are_rewritten_so_the_code_runs(tmp_path: Path):
    build(tmp_path)

    b2_hierarchy.apply(tmp_path)

    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import pkg; print('ok')"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    modules = list((tmp_path / "pkg").glob("m*.py"))
    joined = "\n".join(path.read_text(encoding="utf-8") for path in modules)
    assert "pkg.es.nif" not in joined
    assert "pkg.util" not in joined


def test_the_moves_travel_with_the_result(tmp_path: Path):
    """El mapa de identidad los necesita para no perder los símbolos (Task 2)."""
    build(tmp_path)

    result = b2_hierarchy.apply(tmp_path)

    assert result.moves["pkg.es.nif"].startswith("pkg.m")
    assert result.moves["pkg.util"].startswith("pkg.m")


# --- Las formas de import que traen los repos reales -------------------------
#
# El fixture de arriba solo tiene `from pkg.x import y`. Los dos finalistas con
# jerarquía traen otras tres formas, y cada una rompe de una manera distinta al
# aplanar: python-stdnum tiene un `import stdnum.bic` sin alias usado después
# como expresión (`stdnum/iso9362.py`), y pint tiene 320 imports relativos, que
# dejan de resolver en cuanto el fichero cambia de profundidad.

FORMS = {
    "pkg/__init__.py": "",
    "pkg/util.py": "def clean(x):\n    return x.strip()\n",
    "pkg/es/__init__.py": 'COUNTRY = "es"\n',
    "pkg/es/nif.py": (
        "from pkg.util import clean\n\n\ndef validate(number):\n    return clean(number)\n"
    ),
    # `import pkg.es.nif` sin alias: el nombre que queda ligado es `pkg`, y el
    # módulo se usa después por su ruta entera.
    "pkg/plain.py": (
        "import pkg.es.nif\n\n\ndef run(number):\n    return pkg.es.nif.validate(number)\n"
    ),
    "pkg/aliased.py": (
        "import pkg.es.nif as nif\n\n\ndef run(number):\n    return nif.validate(number)\n"
    ),
    # Mezcla a propósito: `nif` es un submódulo y `COUNTRY` un nombre del
    # `__init__`, así que después de aplanar no pueden venir del mismo sitio.
    "pkg/mixed.py": (
        "from pkg.es import nif, COUNTRY\n\n\n"
        "def run(number):\n    return COUNTRY + nif.validate(number)\n"
    ),
    "pkg/deep/__init__.py": "",
    "pkg/deep/inner/__init__.py": "",
    "pkg/deep/inner/tool.py": (
        "from ...util import clean\n"
        "from ... import es\n"
        "from .. import inner\n\n\n"
        "def run(number):\n    return es.COUNTRY + clean(number) + str(inner is not None)\n"
    ),
    "pkg/relative.py": (
        "from .es import nif\n"
        "from . import util\n\n\n"
        "def run(number):\n    return util.clean(nif.validate(number))\n"
    ),
}


def build_forms(root: Path) -> None:
    for relative, source in FORMS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def run_in(root: Path, code: str):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True
    )


def test_a_module_imported_by_its_full_path_still_resolves(tmp_path: Path):
    """`import stdnum.bic` sin alias, tal cual está en python-stdnum: el módulo
    se usa después como `stdnum.bic`, así que reescribir solo la sentencia de
    import deja el uso apuntando a un módulo que ya no existe."""
    build_forms(tmp_path)

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.plain"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "12"


def test_a_module_imported_with_an_alias_still_resolves(tmp_path: Path):
    build_forms(tmp_path)

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.aliased"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "12"


def test_relative_imports_survive_the_change_of_depth(tmp_path: Path):
    """En pint hay 320, y al aplanar todos los ficheros pasan a colgar del
    paquete: un `from ...util import clean` que antes subía tres niveles ahora
    se saldría del paquete. Sin resolverlos a absoluto, B2 no es aplicable al
    único finalista con jerarquía profunda."""
    build_forms(tmp_path)

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.deep.inner.tool"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "es12True"


def test_a_relative_import_of_a_sibling_module_still_resolves(tmp_path: Path):
    build_forms(tmp_path)

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.relative"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "12"


def test_a_submodule_and_a_name_imported_in_the_same_statement(tmp_path: Path):
    """`from pkg.es import nif, COUNTRY`: después de aplanar, `nif` es un módulo
    que cuelga del paquete raíz y `COUNTRY` sigue siendo un nombre del módulo en
    que se convirtió el `__init__`. Ya no pueden venir del mismo sitio."""
    build_forms(tmp_path)

    result = b2_hierarchy.apply(tmp_path)
    target = result.moves["pkg.mixed"].split(".")[-1]

    ran = run_in(tmp_path, f"from pkg.{target} import run; print(run(' 12 '))")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "es12"


def test_no_module_keeps_its_old_path_anywhere(tmp_path: Path):
    """Si queda una sola ruta vieja, o el repo no arranca o —peor— arranca y la
    dosis de B2 es menor de lo que dice la condición."""
    build_forms(tmp_path)

    b2_hierarchy.apply(tmp_path)

    joined = "\n".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "pkg").rglob("*.py")
    )
    for old in ("pkg.es.nif", "pkg.deep.inner", "pkg.util", "pkg.es "):
        assert old not in joined, old
