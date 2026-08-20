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
from acp.transforms import b2_hierarchy, b4_tests, b5_size

pytestmark = pytest.mark.integration

REPOS = {
    "python-stdnum": "https://github.com/arthurdejong/python-stdnum",
    "pint": "https://github.com/hgrecco/pint",
    "sqlglot": "https://github.com/tobymao/sqlglot",
    "holidays": "https://github.com/vacanza/holidays",
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


# La matriz son las celdas donde la transformación tiene dosis. Una celda que no
# aplica nada y una celda que aplica y sale igual se leen idénticas desde el
# resultado, y solo la segunda es una prueba de equivalencia; por eso las dos
# ausencias que quedan están comprobadas a mano más abajo.
#
# B2 va sobre sqlglot y pint. sqlglot es el repo del sustrato principal donde B2
# tiene dosis —empaqueta sus tests y sus benchmarks, y mientras eso contó como
# "dos paquetes de primer nivel" la transformación era un no-op silencioso—, y
# pint se queda porque es el único finalista con profundidad 3 y donde más se
# aplana. En python-stdnum B2 sigue sin aplicar nada, y eso es correcto: su
# árbol de directorios *es* su tabla de búsqueda —ver
# `test_b2_does_not_apply_to_a_package_addressed_by_computed_name`—.
#
# B4 va sobre python-stdnum y sobre pint. La de pint es la celda que el límite
# de primer nivel dejaba a cero: su suite vive dentro del paquete
# (`pint/testsuite/`) y ahora sale del árbol como cualquier otra, con el
# guardarraíl puesto en si el programa la importa y no en dónde está.
# B3 tiene dosis en los dos repos; la matriz gasta un solo repo para ella.
CELLS = [
    Cell(repo="sqlglot", transform="B2", install_repo=False),
    Cell(repo="pint", transform="B2", install_repo=False),
    Cell(repo="python-stdnum", transform="B3"),
    Cell(repo="python-stdnum", transform="B4", restore_suite=True),
    Cell(repo="pint", transform="B4", restore_suite=True),
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

    # Que el paquete raíz SÍ se encuentre es parte de lo que se afirma: si no,
    # esta dosis cero se confundiría con la otra —la de un repo cuya forma B2 no
    # sabe leer—, que es la que se acaba de arreglar y no es correcta.
    assert b2_hierarchy._package_root(clone) == clone / "stdnum"
    assert b2_hierarchy.computed_module_prefixes(clone) == {"stdnum."}
    assert b2_hierarchy.plan_moves(clone) == {}


def test_b2_does_not_apply_to_holidays_whose_catalogue_is_addressed_by_computed_name(
    tmp_path: Path,
):
    """La otra dosis cero de B2 en el sustrato, la que cambió de causa sin cambiar
    de resultado.

    holidays es el caso que este fichero existe para separar. Antes,
    `_package_root` contaba `scripts/` y `tests/` como paquetes de primer nivel,
    no encontraba raíz, y B2 era un no-op silencioso: la causa mala, la misma
    que dejaba a sqlglot sin dosis. Ahora la raíz sí se encuentra y la dosis
    sigue siendo cero, pero por la causa buena: `holidays/registry.py` construye
    `f"holidays.{prefix}.{module}.{entity}"` y se lo pasa a `import_module`, así
    que el árbol de directorios *es* su catálogo de países. Desde el resultado
    —árbol idéntico— las dos causas se leen igual, y sin esto un cambio en la
    guarda de prefijos devolvería la celda a la causa mala sin que nadie lo
    viera.

    Que la guarda hace falta está medido, no supuesto: desactivándola, B2 mueve
    327 módulos y el paquete deja de importarse en la primera llamada pública
    —`ModuleNotFoundError: No module named 'holidays.countries'`— mientras que
    el mismo árbol sin aplanar responde.
    """
    clone = clone_repo(REPOS["holidays"], tmp_path / "repo")

    # Que la raíz SÍ se encuentre es la mitad que se arregló, y va primero: si
    # volviera a ser None la dosis seguiría siendo cero, así que un test que
    # solo mirara `plan_moves` pasaría con la transformación otra vez muerta.
    assert b2_hierarchy._package_root(clone) == clone / "holidays"
    assert "holidays." in b2_hierarchy.computed_module_prefixes(clone)
    assert b2_hierarchy.plan_moves(clone) == {}


def test_b4_finds_a_suite_nested_inside_the_package(tmp_path: Path):
    """La celda de pint, que estuvo a cero mientras B4 solo miró el primer nivel.

    Se comprueba sobre el repo de verdad porque el fixture no puede reproducir
    lo que la hacía invisible: en la raíz de pint no hay ningún nombre de los
    que B4 busca —ni `tests/`, ni `test/`, ni `conftest.py`—, la suite entera
    son 35 ficheros dentro de `pint/testsuite/`, y con la regla vieja el repo se
    leía como uno sin suite. `suite_paths` devolviendo `[]` tenía las mismas dos
    causas que un `plan_moves` vacío en B2 —no haber suite, o no reconocerla— y
    aquí se separan a mano.
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
    assert b4_tests.suite_paths(clone) == [suite]


def test_b2_finds_the_package_of_a_repo_that_packages_its_own_tests(tmp_path: Path):
    """La celda de sqlglot, que estuvo a cero por la misma clase de motivo.

    sqlglot tiene tres directorios de primer nivel con `__init__.py`
    —`benchmarks/`, `sqlglot/` y `tests/`—, y mientras los tres contaron como
    candidatos a paquete raíz B2 no aplicaba nada. Se comprueba sobre el repo
    real porque lo que falla aquí es la lectura de una forma de repositorio, y
    esa forma es justo lo que un fixture da por supuesto.
    """
    clone = clone_repo(REPOS["sqlglot"], tmp_path / "repo")

    packaged = sorted(
        path.name for path in clone.iterdir() if (path / "__init__.py").exists()
    )
    assert packaged == ["benchmarks", "sqlglot", "tests"], packaged
    assert b2_hierarchy._package_root(clone) == clone / "sqlglot"
    assert b2_hierarchy.plan_moves(clone)


def test_b5_does_not_apply_to_a_package_whose_modules_are_a_duck_protocol(
    tmp_path: Path,
):
    """Por qué la celda de B5 no está sobre python-stdnum, con las dos causas
    separadas.

    Es la misma propiedad que deja a B2 sin dosis aquí —`stdnum/util.py`
    resuelve el módulo de cada país con `__import__('stdnum.%s' % cc)`, así que
    el árbol de módulos *es* la tabla de búsqueda— y por eso la guarda de
    prefijos calculados se come el paquete entero antes de mirar nada más.

    Pero aquí hay una segunda causa que sola bastaría, y conviene tenerla
    medida: quitando la guarda, B5 sigue sin absorber nada, porque 239 módulos
    empiezan por `from stdnum.exceptions import *` y un `import *` trae nombres
    que no se saben sin ejecutar el otro módulo —no hay forma de comprobar si
    pisan a los del vecino, que es justo el fallo silencioso que B5 evita—.
    Detrás de esas dos todavía queda una tercera, que ya no llega a medirse:
    cada módulo de stdnum define `validate`, `is_valid`, `compact` y `format`,
    así que ninguna pareja podría fundirse sin que la segunda definición tapara
    a la primera.

    Sin esto, la ausencia de python-stdnum en la matriz de B5 sería una
    afirmación sin datos, y un `candidates` a cero tiene demasiadas causas.
    """
    clone = clone_repo(REPOS["python-stdnum"], tmp_path / "repo")

    # Que el paquete raíz SÍ se encuentre va primero, igual que en B2: si no,
    # esta dosis cero se confundiría con la de un repositorio cuya forma B5 no
    # sabe leer, que no sería correcta.
    assert b5_size._package_root(clone) == clone / "stdnum"
    assert b5_size.computed_module_prefixes(clone) == {"stdnum."}
    assert b5_size.plan(clone, target_lines=max(b5_size.CURVE)).candidates == 0


def test_b5_would_break_holidays_without_the_computed_name_guard(
    tmp_path: Path, monkeypatch
):
    """La otra dosis cero de B5, y la única de las dos que la guarda sostiene sola.

    En holidays no hay segunda causa: desactivando la guarda, B5 encuentra 317
    candidatos y absorbe 299 —el catálogo de países entero— y el paquete deja de
    responder. Medido, no supuesto: sobre el árbol así fundido,
    `holidays.country_holidays('ES', years=2026)` muere con
    `ModuleNotFoundError: No module named 'holidays.countries.spain'`, mientras
    que el mismo árbol sin tocar contesta `Año Nuevo`. La causa es la de B2:
    `holidays/registry.py` construye `f"holidays.{prefix}.{module}.{entity}"` y
    se lo pasa a `import_module`.

    Que la mutación esté DENTRO del test es deliberado: desde el resultado —cero
    absorbidos— no se distingue «la guarda hizo su trabajo» de «la
    transformación está muerta», y esa confusión es exactamente la que dejó a B2
    en no-op silencioso durante toda la fase 2.
    """
    clone = clone_repo(REPOS["holidays"], tmp_path / "repo")

    assert b5_size._package_root(clone) == clone / "holidays"
    assert "holidays." in b5_size.computed_module_prefixes(clone)
    assert b5_size.plan(clone, target_lines=max(b5_size.CURVE)).candidates == 0

    monkeypatch.setattr(b5_size, "computed_module_prefixes", lambda root: set())
    unguarded = b5_size.plan(clone, target_lines=max(b5_size.CURVE))

    assert unguarded.absorbed > 200, dict(unguarded.unmerged.most_common(5))
