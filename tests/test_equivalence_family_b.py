"""Equivalencia de la familia B contra repos reales. Necesita Docker y red.

Es el criterio de cierre de la fase 2, y en la fase 1 fue donde apareció todo lo
que los fixtures no veían: A4 y los doctests, A2 y `getattr`, la versión
derivada del repositorio. Un arreglo verificado solo contra fixtures pequeños ha
resultado incompleto tres veces al pasarlo por un repo de verdad.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from acp.cli import transform_repo
from acp.equivalence import compare
from acp.suite import run_suite_in_docker
from acp.transforms import b2_hierarchy, b4_tests

pytestmark = pytest.mark.integration

REPOS = {
    "python-stdnum": "https://github.com/arthurdejong/python-stdnum",
    "pint": "https://github.com/hgrecco/pint",
}

# Lo que no se compara nunca: no es del repositorio, lo escribe el clonado o el
# intérprete, y contarlo como dosis diría que una transformación tocó algo
# cuando lo único que pasó fue que alguien importó un módulo.
IGNORED_DIRS = frozenset({".git", "__pycache__", ".pytest_cache"})


@dataclass(frozen=True)
class Cell:
    """Una celda de la matriz: un repo, una transformación y su fontanería.

    `install_repo` y `restore_suite` no son opciones de gusto. B2 destruye la
    estructura que declara el `pyproject`, así que una instalación editable
    dejaría de encontrar sus paquetes y la celda se leería como un fracaso total
    del agente cuando es fontanería rota (§5.6). B4 se lleva la suite fuera del
    árbol, así que hay que devolvérsela al contenedor —donde no hay agente— o no
    quedaría nada que verificar (§4.2).
    """

    repo: str
    transform: str
    install_repo: bool = True
    restore_suite: bool = False

    @property
    def id(self) -> str:
        return f"{self.repo}-{self.transform}"


# La matriz son las celdas donde la transformación tiene dosis. B2 va sobre
# pint, no sobre python-stdnum: pint es el único finalista con profundidad 3 y
# por tanto el único sitio donde se puede leer el eje de la jerarquía, y en
# python-stdnum B2 no aplica nada —ver
# `test_b2_does_not_apply_to_a_package_addressed_by_computed_name`—, así que esa
# celda solo compararía un árbol consigo mismo.
#
# Y B4 va sobre python-stdnum, no sobre pint, por el motivo simétrico: la suite
# de pint vive dentro del paquete (`pint/testsuite/`) y B4 no la toca por
# decisión declarada —ver
# `test_b4_does_not_apply_to_a_suite_nested_inside_the_package`—, así que esa
# celda tampoco mediría nada. Las dos ausencias están comprobadas
# porque una celda que no aplica y una celda que aplica y sale igual se leen
# idénticas desde el resultado, y solo la segunda es una prueba de
# equivalencia. B3 sí tiene dosis en los dos repos; la matriz gasta un solo
# repo por transformación y para B3 usa python-stdnum.
CELLS = [
    Cell(repo="pint", transform="B2", install_repo=False),
    Cell(repo="python-stdnum", transform="B3"),
    Cell(repo="python-stdnum", transform="B4", restore_suite=True),
]


@pytest.fixture(autouse=True)
def require_docker(request):
    if request.node.get_closest_marker("docker") and shutil.which("docker") is None:
        pytest.skip("docker no está instalado")


def clone_repo(url: str, destination: Path) -> Path:
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(destination)],
        check=True,
        capture_output=True,
    )
    return destination


def _files(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        if path.is_file():
            found[str(relative)] = path.read_bytes()
    return found


def tree_dose(before: Path, after: Path) -> list[str]:
    """Qué cambió del árbol original al transformado.

    Existe porque una celda sin dosis pasa la verificación de equivalencia por
    construcción: comparar un árbol consigo mismo sale verde y no prueba nada.
    Sin este guardarraíl la matriz daría el visto bueno a transformaciones que
    no se aplicaron —le pasa a B2 sobre python-stdnum—, que es la misma forma de
    fallar que ya tiene tapada `compare` cuando la suite de referencia no
    ejecuta ningún test.
    """
    original, transformed = _files(before), _files(after)
    changed = [name for name in sorted(set(original) - set(transformed))]
    changed += [name for name in sorted(set(transformed) - set(original))]
    changed += [
        name
        for name in sorted(set(original) & set(transformed))
        if original[name] != transformed[name]
    ]
    return changed


@pytest.mark.docker
@pytest.mark.parametrize("cell", CELLS, ids=lambda cell: cell.id)
def test_family_b_keeps_a_real_repo_equivalent(tmp_path: Path, cell: Cell):
    clone = clone_repo(REPOS[cell.repo], tmp_path / "repo")

    # Antes que las corridas, y no después, por dos razones: la dosis se mide
    # sobre el clon intacto, y una celda que no aplica nada no merece gastar dos
    # suites en contenedor para acabar comparando un árbol consigo mismo.
    work = transform_repo(clone, [cell.transform], tmp_path / "work")
    dose = tree_dose(clone, work)
    assert dose, f"{cell.id}: la transformación no cambió nada, la celda no mide"

    before = run_suite_in_docker(clone, timeout=1800)
    after = run_suite_in_docker(
        work,
        timeout=1800,
        install_repo=cell.install_repo,
        tests_from=b4_tests.kept_suite_path(work) if cell.restore_suite else None,
    )

    report = compare(before, after)
    assert report.equivalent is True, f"{cell.id}: {report.differences}"


def test_b2_does_not_apply_to_a_package_addressed_by_computed_name(tmp_path: Path):
    """Por qué python-stdnum no está en la matriz de B2, escrito y comprobado.

    `stdnum/util.py` resuelve el módulo de cada país con
    `__import__('stdnum.%s' % cc, ..., [name])`: el árbol de directorios *es* su
    tabla de búsqueda, y el prefijo calculado se come el paquete entero. La
    celda no es que salga igual, es que no aplica —igual que cuando no hay un
    paquete raíz claro—, y la diferencia entre las dos cosas solo la separa
    `computed_module_prefixes`: un `plan_moves` vacío a secas tiene dos causas.

    Sin este test la matriz del plan pasaba con un `moves` vacío y la celda se
    leía como "B2 preserva python-stdnum" cuando lo que hubo fue una copia.
    """
    clone = clone_repo(REPOS["python-stdnum"], tmp_path / "repo")

    assert b2_hierarchy.computed_module_prefixes(clone) == {"stdnum."}
    assert b2_hierarchy.plan_moves(clone) == {}


def test_b4_does_not_apply_to_a_suite_nested_inside_the_package(tmp_path: Path):
    """Por qué pint no está en la matriz de B4, escrito y comprobado.

    La suite de pint es `pint/testsuite/` —dentro del paquete—, y ese es el
    límite declarado de B4 (§4.2): un directorio de tests que el propio código
    puede importar no se mueve, porque la verificación restaura la suite antes
    de correr y un import roto por habérsela llevado no lo vería nadie.

    Sin este test, la ausencia de la celda descansaba en un comentario. Y
    `suite_paths` devolviendo `[]` tiene las mismas dos causas que un
    `plan_moves` vacío en B2: que el repo no tenga suite, o que B4 no la haya
    reconocido. Aquí se separan a mano: la suite existe, está un nivel adentro,
    y ninguno de los nombres que B4 busca aparece en la raíz.
    """
    clone = clone_repo(REPOS["pint"], tmp_path / "repo")

    suite = clone / "pint" / "testsuite"
    nested = sorted(path.name for path in suite.glob("test_*.py"))
    assert len(nested) > 20, f"pint ya no tiene su suite dentro del paquete: {nested}"
    assert not [
        name
        for name in b4_tests.SUITE_DIRS + b4_tests.SUITE_FILES
        if (clone / name).exists()
    ]
    assert b4_tests.suite_paths(clone) == []
