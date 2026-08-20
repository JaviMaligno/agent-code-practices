"""Lo que la suite AFIRMA no lo reescribe ninguna transformación.

La verificación de toda la campaña es «la suite del repo da el mismo resultado
antes y después». §4.3.1 obliga a transformar también la suite —si no, no
compila y se mide otra cosa—, así que sus imports y las rutas de módulo que usa
como maquinaria SÍ se reescriben. Pero si lo que se reescribe es el valor que la
aserción compara, la expectativa se mueve con el programa y el criterio se
vuelve una tautología justo donde más falta hace.

Aquí está fijada la línea, con los dos lados en el mismo fixture: el objetivo de
un `patch` y la comparación que decide qué se colecta son maquinaria y viajan;
lo que un `assert` afirma se queda, y el módulo que nombra no se mueve.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from acp.transforms import b2_hierarchy, b5_size

# Las dos que mueven módulos y reescriben rutas dentro de cadenas. B1 mueve
# definiciones sueltas y solo toca cadenas para reescribir ejemplos de doctest,
# donde la salida esperada —lo que el ejemplo afirma— ya está fuera de alcance
# por construcción (`rewrite_examples`).
MOVERS = {
    "B2": b2_hierarchy.apply,
    "B5": lambda root: b5_size.apply(root, target_lines=2000),
}


def build(root: Path) -> None:
    """Un repo diminuto con las tres formas de nombrar un módulo desde la suite.

    `pkg.zzz_named` es lo que la suite AFIRMA: su test compara el `__name__` del
    módulo con la ruta escrita a mano. `pkg.bbb_helper` y `pkg.deep.tool` son
    maquinaria: uno es el objetivo de un `patch` y el otro decide qué módulos
    recorre la colecta.
    """
    pkg = root / "pkg"
    (pkg / "deep").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "aaa_host.py").write_text("def host():\n    return 1\n", encoding="utf-8")
    (pkg / "bbb_helper.py").write_text(
        "CONST = 1\n\n\ndef helped():\n    return CONST\n", encoding="utf-8"
    )
    (pkg / "zzz_named.py").write_text(
        "def who_now():\n    return __name__\n", encoding="utf-8"
    )
    (pkg / "deep" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "deep" / "tool.py").write_text("def tool():\n    return 2\n", encoding="utf-8")

    tests = root / "tests"
    tests.mkdir()
    (tests / "test_claims.py").write_text(
        "import unittest\n"
        "import pkgutil\n"
        "from unittest.mock import patch\n"
        "\n"
        "from pkg.bbb_helper import helped\n"
        "from pkg.zzz_named import who_now\n"
        "\n"
        "\n"
        "def known():\n"
        "    import pkg\n"
        "    return [info.name for info in pkgutil.walk_packages(pkg.__path__, 'pkg.')]\n"
        "\n"
        "\n"
        "def test_the_module_knows_its_own_name():\n"
        "    assert who_now() == 'pkg.zzz_named'\n"
        "\n"
        "\n"
        "class NameTest(unittest.TestCase):\n"
        "    def test_the_same_thing_the_unittest_way(self):\n"
        "        self.assertEqual(who_now(), 'pkg.zzz_named')\n"
        "\n"
        "\n"
        "@patch('pkg.bbb_helper.CONST', 7)\n"
        "def test_the_patch_target_is_machinery():\n"
        "    assert helped() == 7\n"
        "\n"
        "\n"
        "def test_the_name_that_decides_what_runs_is_machinery():\n"
        "    seen = [name for name in known() if name == 'pkg.deep.tool']\n"
        "    assert len(seen) == 1\n",
        encoding="utf-8",
    )


def claimed_literals(path: Path) -> list[str]:
    """Las cadenas que viven dentro de una aserción, en orden."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        call = isinstance(node, ast.Call) and _callee(node).startswith("assert")
        if not isinstance(node, ast.Assert) and not call:
            continue
        found += [
            sub.value
            for sub in ast.walk(node)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
        ]
    return found


def _callee(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return node.func.id if isinstance(node.func, ast.Name) else ""


@pytest.mark.parametrize("transform", sorted(MOVERS))
def test_what_the_suite_claims_comes_out_untouched(tmp_path: Path, transform: str):
    """El invariante, y la razón de que el criterio de equivalencia valga algo.

    Reproducido en laboratorio antes de arreglarlo: B5 absorbió el módulo y
    reescribió `assert who_now() == 'pkg.zzz_named'` al nombre del anfitrión. La
    suite quedaba en verde (1 passed antes y después) mientras el valor
    observable del programa había cambiado, porque la expectativa se movió con
    él.
    """
    build(tmp_path)
    antes = claimed_literals(tmp_path / "tests" / "test_claims.py")

    MOVERS[transform](tmp_path)

    assert claimed_literals(tmp_path / "tests" / "test_claims.py") == antes


@pytest.mark.parametrize("transform", sorted(MOVERS))
def test_a_module_the_suite_names_in_an_assertion_stays_where_it_is(
    tmp_path: Path, transform: str
):
    """La salida no es dejar la cadena quieta y ya: sería la suite en rojo por
    algo que causó la transformación. Es no mover el módulo, que es lo que ya se
    hace cuando la ruta viene dentro de una frase (`modules_named_by_the_suite`).
    """
    build(tmp_path)

    MOVERS[transform](tmp_path)

    assert (tmp_path / "pkg" / "zzz_named.py").exists()
    assert "def who_now" in (tmp_path / "pkg" / "zzz_named.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("transform", sorted(MOVERS))
def test_the_suite_still_passes_without_having_been_edited(tmp_path: Path, transform: str):
    """Verde y sin tocar el oráculo: es la diferencia entre una equivalencia
    verificada y una autocumplida."""
    build(tmp_path)

    MOVERS[transform](tmp_path)

    proceso = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=os.environ | {"PYTHONPATH": str(tmp_path)},
    )
    assert proceso.returncode == 0, proceso.stdout + proceso.stderr


def test_a_patch_target_inside_a_test_follows_its_module(tmp_path: Path):
    """El control negativo, sacado de casos reales: `@patch("pint.compat.X")` y
    `mock.patch("sqlglot.m22.logger")` son la forma en que un test llega al
    código, no lo que afirma. Sin reescribirlos, el parche apunta a un módulo
    que ya no existe y la suite se cae por la transformación."""
    build(tmp_path)

    b5_size.apply(tmp_path, target_lines=2000)

    fuente = (tmp_path / "tests" / "test_claims.py").read_text(encoding="utf-8")
    assert "'pkg.bbb_helper.CONST'" not in fuente
    assert "'pkg.aaa_host.CONST'" in fuente


def test_a_comparison_that_decides_what_to_run_is_not_a_claim(tmp_path: Path):
    """Sacado de `tests/test_docs.py` de sqlglot: `if info.name ==
    "sqlglot.__main__": continue` decide qué módulos recorre la colecta de
    doctests. Es maquinaria: sin reescribirla el filtro deja de filtrar. La
    línea está en lo que la suite AFIRMA, no en cualquier comparación."""
    build(tmp_path)

    b2_hierarchy.apply(tmp_path)

    fuente = (tmp_path / "tests" / "test_claims.py").read_text(encoding="utf-8")
    linea = next(line for line in fuente.splitlines() if "seen = " in line)
    assert "'pkg.deep.tool'" not in linea
