"""B5 — tamaño: concatenar módulos hasta un techo de líneas por fichero.

Lo que varía aquí es **cuánto código hay que atravesar para llegar a una pieza**
(§4.2). No es una celda sino una curva —original, ~500, ~2.000 y ~10.000 líneas—
porque es la única parte del diseño que busca un umbral en vez de una diferencia:
la sospecha es que por debajo de cierto tamaño da igual y por encima no (§6.3).

Y por eso no toca la organización: los módulos que se funden son **hermanos del
mismo directorio**, así que qué vive con qué se conserva —lo que cambia es que
ahora viven en el mismo fichero—. Romper la cohesión es B1, y si las dos cosas
cambiaran a la vez ninguna de las dos sería atribuible.

**Lo difícil no es concatenar, es que el programa siga siendo el mismo.** Dos
módulos que definen `validate` puestos en un fichero dejan una sola `validate`,
la segunda, y el programa cambia **en silencio**: no hay error de sintaxis, no
hay ImportError, hay otro resultado. Por eso aquí se funde **de menos**: cada
pareja que no se puede juntar sin arriesgar eso se queda separada, y `plan()`
publica cuántas son y por qué, porque esa dosis perdida es un dato del
experimento y no una nota al pie.

Las decisiones de alcance, declaradas:

- **Solo dentro de un directorio.** Es lo que mantiene válido todo lo que se
  cuenta desde la posición del fichero: los imports relativos (`from ..util
  import x` sube los mismos escalones) y `Path(__file__).parent`, que es como
  pint encuentra su tabla de unidades. Fundir entre directorios no daría un
  fichero más grande de otra manera, solo abriría esa clase de fallos.
- **Los `__init__.py` no se funden ni absorben**: son el punto de entrada del
  paquete (§5.6) y cargarlos antes de tiempo cambia el orden de importación de
  todo lo que cuelga de ellos.
- **La suite del repo tampoco**, aunque sus imports sí se reescriben (§4.3.1).
  En un fichero de test, dónde vive una definición *es* semántica de pytest: un
  `pytest.importorskip` de primer nivel convierte el módulo entero en un salto,
  así que fundir dos ficheros de test puede saltarse los tests del otro sin que
  nada falle. Medido en B1 sobre pint: repartir dentro de su `testsuite/` dejó
  la colecta en cero. El tamaño de la suite es materia de B4.
- **El grafo de imports se mantiene acíclico.** Fundir dos módulos contrae dos
  nodos en uno, y eso crea un ciclo en cuanto había un camino entre ellos por
  fuera. Un ciclo no da una dosis rara, mata el `import`.

Lo que se conserva a propósito: el número de definiciones, el nombre de cada una
y el directorio donde vive. Eso es lo que separa esta curva de B1 y de B2.

**La dosis, medida sobre los cuatro finalistas.** Candidatos son los módulos que
pueden participar; absorbidos, los que desaparecen dentro de otro:

| repo | candidatos | absorbidos 500 / 2.000 / 10.000 | puntos | qué se queda fuera |
|---|---|---|---|---|
| python-stdnum | 0 de 368 | 0 / 0 / 0 | **1** | 251 se alcanzan por nombre construido |
| holidays | 0 de 658 | 0 / 0 / 0 | **1** | 323 se alcanzan por nombre construido |
| pint | 56 de 110 | 12 / 20 / 20 | **3** | 38 son suite, 15 son `__init__` |
| sqlglot | 88 de 252 | 33 / 55 / 58 | **4** | 69 por nombre construido, 60 suite, 12 con `import *` |

La columna de puntos es la que hay que leer antes de gastar una celda, y la
publica `curve_points()` sobre cualquier árbol sin escribir nada: cuenta las
condiciones DISTINTAS que produce la curva ahí, contando el original. §6.3 supone
cuatro y solo sqlglot los tiene. En pint el techo de 10.000 produce el mismo
árbol byte a byte que el de 2.000 —el mismo plan de fusiones, 20 módulos
absorbidos los dos—, y en python-stdnum y holidays los tres techos son el árbol
original. Pedir un punto que este repositorio no tiene está rechazado
(`_reject_a_curve_point_this_repo_does_not_have` del CLI): la corrida no falla
sola —termina en verde, con el manifiesto diciendo `B5-10000`— y la curva saldría
publicada con un punto que es otro repetido.

Y el eje que de verdad se mueve, en el punto de 2.000 líneas y sobre los
ficheros de código (sin la suite, que no se funde):

| repo | ficheros | mediana | p90 | líneas totales |
|---|---|---|---|---|
| sqlglot | 184 → 129 | 163 → 193 | 836 → 1.932 | −0,2% |
| pint | 69 → 49 | 147 → 158 | 524 → 1.187 | −0,5% |

La mediana casi no se mueve y el p90 se dobla, que es exactamente la forma que
tiene esta transformación: los ficheros que no encuentran con quién fundirse se
quedan como estaban, y el crecimiento se concentra arriba. Quien lea la curva
tiene que leerla así y no como «todos los ficheros son ahora de 2.000 líneas».

Y las líneas totales se conservan dentro del 1%: lo que se pierde son los
imports internos al grupo, que sobran, y los repetidos. Eso es lo que sostiene
que la curva mide el tamaño del fichero y no la cantidad de código.

Cuatro cosas que hay que saber antes de gastar una celda aquí:

- **python-stdnum y holidays no aplican.** Los dos resuelven sus módulos por un
  nombre que no existe hasta que el programa corre —`__import__('stdnum.%s' % cc)`,
  `f"holidays.{prefix}.{module}.{entity}"`—, o sea la MISMA propiedad que deja a
  B2 sin dosis sobre ellos y por la misma razón: el árbol de módulos *es* su
  tabla de búsqueda. En python-stdnum hay además una segunda razón que sola
  bastaría: todos sus módulos definen `validate`, `is_valid`, `compact` y
  `format`, así que ninguna pareja suya podría fundirse sin taparse.
- **La curva satura pronto.** En pint, 2.000 y 10.000 producen el mismo árbol, y
  en sqlglot casi —entre esos dos techos solo se ganan 3 módulos, 55 contra 58—:
  con módulos que ya son pequeños, lo que limita deja de ser el techo y pasa a
  ser la compatibilidad entre hermanos, que no depende de él. El cuarto punto de
  §6.3 puede no existir en estos repositorios, y eso hay que decirlo antes de
  interpretar una curva plana como «el tamaño no importa»: entre dos puntos que
  son el mismo árbol, una línea plana no dice nada del agente, dice que se midió
  dos veces la misma condición. Y donde la dosis es cero en los tres techos no
  hay curva ninguna: lo que se compararía es el repositorio consigo mismo.
- **Verificado en repo real, no solo en fixtures**, con la suite entera antes y
  después: pint 2.024 pasan / 0 fallan en las dos, con 20 módulos absorbidos;
  sqlglot 1.231 / 0 en las dos, con 55. Hizo falta: sqlglot salió sin poder
  importarse dos veces seguidas, las dos por el ciclo que el repositorio ya
  tenía, y ninguno de los dieciocho fixtures que había entonces lo enseñaba.
  Sale más barato buscarlo con un `import` y media docena de llamadas que con
  dos corridas de suite: las dos veces el fallo era un ImportError en la
  primera línea.
- **Lo que se queda fuera y no se puede arreglar sin ejecutar el programa**: un
  decorador que registra por `__module__`, y el `__name__` de un módulo
  absorbido —que cambia—. Contra lo primero no hay guarda posible; contra lo
  segundo están las dos que ya usa B2 (nombre construido, y nombre escrito
  dentro de un texto de la suite), y son las que dejan a dos de los cuatro
  finalistas a cero.

Lo que esta transformación NO reescribe, declarado: las rutas de fichero que la
configuración de pytest nombra (`_rewrite_configured_paths` de B2). No hace
falta porque apuntan a la suite, y la suite no se funde; si algún día se fundiera
código nombrado ahí, haría falta.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst

from acp.metrics.size import is_test_file
from acp.metrics.size import module_name as _module_name
from acp.metrics.size import read_source
from acp.transforms.b1_cohesion import (
    _exported_names,
    _external_requirements,
    _module_scope,
    # La misma regla para las dos: un módulo que se suplanta en `sys.modules` no
    # se funde con nadie (B5) ni recibe definiciones de nadie (B1). Estaba
    # duplicada y B1 se enteró tarde —python-stdnum dejó de colectar—, así que
    # ahora hay una sola.
    _reaches_into_the_import_system,
    _resolve_relative,
)
from acp.transforms.b2_hierarchy import (
    _drop_stale_bytecode,
    _dotted,
    _moved_dotted,
    _outside_the_symbol_map,
    _package_root,
    _rewrite_entry_points,
    _rewrite_setup_script,
    computed_module_prefixes,
    modules_named_by_the_suite,
)
from acp.transforms.base import TransformResult, iter_transformable_files
from acp.transforms.dependencies import module_bindings
from acp.transforms.doctests import DOCTEST_PROMPT, doctest_files, rewrite_examples

# Los puntos de la curva que pide §6.3. El original es el cuarto punto y no
# necesita transformación: es el árbol tal cual.
CURVE = (500, 2000, 10000)
DEFAULT_TARGET_LINES = 2000


# --- lectura de un módulo --------------------------------------------------


@dataclass
class _Module:
    path: Path
    name: str
    package: str
    directory: Path
    source: str
    code: cst.Module
    tree: ast.Module
    lines: int
    bindings: dict[str, str] = field(default_factory=dict)
    # Nombre ligado → el texto exacto de la sentencia que lo importa. Es lo que
    # distingue «los dos importan `os`» —que se funde sin más— de «los dos
    # ligan `os` a cosas distintas», que es el fallo silencioso de esta
    # transformación.
    import_text: dict[str, str] = field(default_factory=dict)
    # Nombre ligado → módulo del repo del que viene, cuando viene sin alias.
    symbol_origin: dict[str, str] = field(default_factory=dict)
    futures: frozenset[str] = frozenset()
    externals: frozenset[str] = frozenset()
    has_all: bool = False
    opaque_all: bool = False
    stars: bool = False
    doc_has_doctest: bool = False
    dynamic: bool = False
    is_init: bool = False
    is_test: bool = False
    # Módulos del repo que este carga como OBJETO al importarse (`from . import
    # x`, `import pkg.x`): fundirlos con él dejaría el nombre apuntando a un
    # fichero que ya no existe, y no hay import que arregle eso desde dentro.
    module_deps: frozenset[str] = frozenset()
    # Módulos del repo de los que importa nombres sueltos al importarse.
    symbol_deps: frozenset[str] = frozenset()
    # Todo lo que carga al importarse: las aristas del grafo de ciclos.
    graph_deps: frozenset[str] = frozenset()


def _read_modules(root: Path) -> dict[str, _Module]:
    modules: dict[str, _Module] = {}
    for path in iter_transformable_files(root):
        source = read_source(path)
        try:
            tree = ast.parse(source)
            code = cst.parse_module(source)
        except (SyntaxError, ValueError, cst.ParserSyntaxError):
            # Un fichero que no parsea no se toca ni se cuenta: transformarlo a
            # ciegas es la única forma de romper algo que ya funcionaba.
            continue
        name = _module_name(path, root)
        _, opaque = _exported_names(tree)
        bindings = module_bindings(tree)
        modules[name] = _Module(
            path=path,
            name=name,
            package=_module_name(path.parent / "__init__.py", root),
            directory=path.parent,
            source=source,
            code=code,
            tree=tree,
            lines=source.count("\n") + 1,
            bindings=bindings,
            futures=_future_features(tree),
            externals=_external_requirements(tree),
            has_all="__all__" in bindings,
            opaque_all=opaque,
            doc_has_doctest=DOCTEST_PROMPT in (ast.get_docstring(tree, clean=False) or ""),
            dynamic=_reaches_into_the_import_system(source, bindings),
            is_init=path.name == "__init__.py",
            is_test=is_test_file(path, root),
        )
    roots = {name.split(".")[0] for name in modules}
    for info in modules.values():
        info.externals = frozenset(info.externals - roots)
        _read_imports(info, modules)
    return modules


def _future_features(tree: ast.Module) -> frozenset[str]:
    """Lo que el módulo pide de `__future__`.

    Tiene que coincidir entre los que se funden. `from __future__ import
    annotations` cambia si las anotaciones se evalúan o se guardan como texto,
    y es una propiedad del FICHERO: fundir un módulo que lo tiene con otro que
    no se lo impone al segundo, y lo que lee anotaciones en ejecución empieza a
    ver cadenas donde esperaba clases.
    """
    return frozenset(
        alias.name
        for statement in tree.body
        if isinstance(statement, ast.ImportFrom) and statement.module == "__future__"
        for alias in statement.names
    )


def _read_imports(info: _Module, modules: dict[str, _Module]) -> None:
    """Qué carga este fichero al importarse, y en qué forma.

    Solo el ámbito del módulo y sin las guardas de `if TYPE_CHECKING`: bajo la
    guarda el import no se ejecuta nunca, así que no es una arista —contarlo
    inventa ciclos que el intérprete jamás ve, que es lo que dejó a B1 con dosis
    cero sobre pint—. Lo de dentro de un `def` tampoco carga nada al importar, y
    además sobrevive a la fusión: el reescritor lo manda al fichero fundido y un
    `from .yo import x` dentro de una función se resuelve contra `sys.modules`.
    """
    module_deps: set[str] = set()
    symbol_deps: set[str] = set()
    graph_deps: set[str] = set()
    for statement, guarded in _module_scope(info.tree):
        # La guarda decide si el import es una ARISTA —bajo `if TYPE_CHECKING` no
        # se ejecuta nunca—, pero no si liga el nombre: para quien lee el módulo
        # lo liga igual, y sin anotarlo dos ficheros que comparten el mismo
        # import de tipos se leen como dos que se pisan. Medido sobre sqlglot:
        # así comparten `DialectType`, `Dialect` y `exp` sus dialectos.
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name in modules and not guarded:
                    module_deps.add(alias.name)
                    graph_deps.add(alias.name)
                info.import_text[(alias.asname or alias.name).split(".")[0]] = (
                    f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else "")
                )
            continue
        if not isinstance(statement, ast.ImportFrom):
            continue
        origin = _resolve_relative("." * statement.level + (statement.module or ""), info.package)
        written = "." * statement.level + (statement.module or "")
        for alias in statement.names:
            if alias.name == "*":
                info.stars = True
                if origin in modules and not guarded:
                    module_deps.add(origin)
                    graph_deps.add(origin)
                continue
            bound = alias.asname or alias.name
            tail = f" as {alias.asname}" if alias.asname else ""
            info.import_text[bound] = f"from {written} import {alias.name}{tail}"
            if guarded:
                continue
            submodule = f"{origin}.{alias.name}"
            if submodule in modules:
                # `from pkg import util` no trae un símbolo, trae un fichero, y
                # el nombre queda ligado al módulo entero.
                module_deps.add(submodule)
                graph_deps.add(submodule)
                continue
            if origin in modules:
                symbol_deps.add(origin)
                graph_deps.add(origin)
                if alias.asname is None:
                    info.symbol_origin[bound] = origin
    info.module_deps = frozenset(module_deps)
    info.symbol_deps = frozenset(symbol_deps)
    info.graph_deps = frozenset(graph_deps)


# --- quién puede participar ------------------------------------------------


@dataclass
class _Frozen:
    """Lo que hace intocable a un módulo, calculado una vez por repositorio."""

    package: Path | None
    # El paquete raíz como nombre de módulo, que no es siempre el del directorio:
    # en un layout `src/` el `__init__.py` de `src/pkg/` se llama `pkg`.
    root_module: str
    computed: frozenset[str]
    named: frozenset[str]
    star_targets: frozenset[str]


def _why_not(info: _Module, root: Path, frozen: _Frozen) -> str | None:
    """Por qué este módulo no puede fundirse con nadie, o None si puede."""
    if info.is_init:
        return "es el __init__ del paquete"
    if info.is_test:
        return "es suite del repositorio"
    if info.path.name == "__main__.py":
        # El guion de `python -m paquete`. Nadie lo importa, así que su código de
        # nivel de módulo corre cuando el paquete ya está entero; metido en un
        # fichero de la librería pasa a correr en mitad de la carga.
        return "es el guion de arranque del paquete"
    if frozen.root_module and frozen.root_module in info.module_deps:
        # `import paquete` desde dentro del propio paquete es la firma de «yo
        # corro cuando esto ya está montado»: lo que se lee después es un
        # atributo que el `__init__` todavía no ha puesto si el módulo se carga
        # antes de tiempo. Medido sobre sqlglot: `sqlglot/__main__.py` lee
        # `sqlglot.__version__`, y fundirlo con tres módulos de la librería
        # convirtió `import sqlglot` en un AttributeError —el repositorio entero
        # caído, con la suite en cero—.
        return "espera a que su propio paquete esté cargado"
    if frozen.package is None or not (
        frozen.package == info.path.parent or frozen.package in info.path.parents
    ):
        return "fuera del paquete raíz"
    if _outside_the_symbol_map(info.path, root):
        return "fuera del mapa de identidad"
    if any(info.name.startswith(prefix) for prefix in frozen.computed):
        return "se alcanza por un nombre construido al correr"
    if any(prefix.startswith(f"{info.name}.") for prefix in frozen.computed):
        return "es el paquete de nombres construidos al correr"
    if any(named == info.name or named.startswith(f"{info.name}.") for named in frozen.named):
        return "la suite lo nombra dentro de un texto o lo afirma"
    if info.name in frozen.star_targets:
        return "alguien le hace import *"
    if info.stars:
        # Un `import *` trae nombres que no se saben sin importar el otro
        # módulo, así que no hay forma de comprobar si pisan a los del vecino:
        # exactamente el fallo silencioso del que trata esta transformación.
        return "hace import *"
    if info.opaque_all:
        return "su __all__ no se puede leer"
    if info.dynamic:
        return "se manipula a sí mismo en sys.modules o con __getattr__"
    return None


def _frozen(root: Path, modules: dict[str, _Module]) -> _Frozen:
    package = _package_root(root)
    star_targets: set[str] = set()
    for info in modules.values():
        for statement, _ in _module_scope(info.tree):
            if isinstance(statement, ast.ImportFrom) and any(
                alias.name == "*" for alias in statement.names
            ):
                origin = _resolve_relative(
                    "." * statement.level + (statement.module or ""), info.package
                )
                if origin in modules:
                    star_targets.add(origin)
    return _Frozen(
        package=package,
        root_module=_module_name(package / "__init__.py", root) if package else "",
        # Las dos preguntas ya se las hace B2 por la misma razón —un módulo al
        # que se llega por un nombre que no existe hasta que corre no se puede
        # renombrar— y aquí el nombre del absorbido desaparece igual.
        computed=frozenset(computed_module_prefixes(root)),
        named=frozenset(modules_named_by_the_suite(root)),
        star_targets=frozenset(star_targets),
    )


# --- por qué dos módulos no se pueden juntar --------------------------------


def _incompatible(candidate: _Module, group: list[_Module], target_lines: int) -> str | None:
    """Por qué el candidato no cabe en este grupo, o None si cabe.

    El grupo está ordenado: cada miembro puede depender de los anteriores y de
    nadie más, así que en el fichero fundido todo lo que se usa ya está escrito
    más arriba. Es lo que evita tener que ordenar topológicamente un grupo que
    puede tener ciclos internos.
    """
    if sum(member.lines for member in group) + candidate.lines > target_lines:
        return "no cabe bajo el techo de líneas"
    host = group[0]
    if candidate.futures != host.futures:
        return "no coinciden en from __future__"
    if candidate.externals != host.externals:
        # Fundir un módulo que exige un extra opcional con uno del núcleo se lo
        # exige a todo el que importe el fichero. Medido en B1 sobre
        # `pint/matplotlib.py`: un `import pint` que funcionaba pasó a
        # ModuleNotFoundError para quien no tuviera el extra.
        return "exigen paquetes de terceros distintos"
    if candidate.doc_has_doctest:
        # La docstring del absorbido deja de ser la docstring del módulo y pasa
        # a ser una cadena suelta en medio del fichero, que doctest no recoge:
        # sus ejemplos dejarían de ejecutarse y la suite daría otro número.
        return "su docstring de módulo lleva doctests"
    if candidate.has_all and not host.has_all:
        return "define __all__ y el fichero que lo absorbe no"
    for member in group:
        if member.name in candidate.module_deps:
            return "carga a un miembro del grupo como módulo"
        if candidate.name in member.module_deps or candidate.name in member.symbol_deps:
            # El grupo se escribe en orden y el candidato va el último: si
            # alguien de dentro lo necesita, lo necesitaría antes de existir.
            return "alguien del grupo lo importa"
        collision = _collides(candidate, member)
        if collision is not None:
            return collision
    return None


def _collides(candidate: _Module, member: _Module) -> str | None:
    """El fallo silencioso: dos nombres iguales en el mismo espacio de nombres.

    Puestos en un fichero, el segundo tapa al primero sin error de ninguna
    clase. Las dos excepciones son las que hacen que la transformación tenga
    dosis: dos módulos que importan lo MISMO escrito igual, y el nombre que el
    candidato importa precisamente del otro miembro —ahí no hay dos objetos,
    hay uno—.
    """
    for name in sorted(set(candidate.bindings) & set(member.bindings)):
        if name == "__all__":
            continue  # se fusionan las listas al escribir
        text = candidate.import_text.get(name)
        if text is not None and text == member.import_text.get(name):
            continue
        if candidate.symbol_origin.get(name) == member.name:
            continue
        return f"los dos ligan {name}"
    return None


# --- el grafo contraído -----------------------------------------------------


class _Contracted:
    """El grafo de imports con cada grupo ya fundido en un solo nodo.

    Fundir dos módulos contrae dos nodos en uno, y eso cierra un ciclo en cuanto
    hubiera un camino entre ellos que pase por fuera del grupo. Un ciclo no da
    una dosis rara: mata el `import`.

    **Aquí no se tolera el ciclo que ya estaba**, y esa es la diferencia con B1.
    B1 añade una arista a un grafo, y dentro de un enredo que el intérprete ya
    sobrevive una arista más no cambia nada. B5 no añade una arista: cambia
    CUÁNDO corre cada línea. Dentro de un ciclo, el orden en que aparecen los
    nombres es lo único que lo hace sobrevivir —quien vuelve a entrar en el
    módulo a medias encuentra lo que ya se ejecutó y nada más—, y concatenar
    mueve las definiciones del absorbido detrás del punto por el que se vuelve a
    entrar. Medido sobre sqlglot: `sqlglot/dialects/dialect.py` importa
    `sqlglot/parsers/base.py`, que le importa de vuelta un nombre que la fusión
    acababa de mudar más abajo, y `import sqlglot` murió con un ImportError de
    import circular. Como la comprobación es local —¿el nodo fundido se alcanza
    a sí mismo?— no hace falta ninguna tolerancia para tener dosis: las fusiones
    que no tocan un ciclo se siguen aceptando.
    """

    def __init__(self, modules: dict[str, _Module]) -> None:
        self.node = {name: name for name in modules}
        self.out: dict[str, set[str]] = {}
        for name, info in modules.items():
            for target in sorted(info.graph_deps):
                if target in modules and target != name:
                    self.out.setdefault(name, set()).add(target)

    def would_cycle(self, group: list[_Module], candidate: _Module) -> bool:
        merged = {self.node[member.name] for member in group} | {self.node[candidate.name]}
        stack = [
            target
            for node in merged
            for target in self.out.get(node, ())
            if target not in merged
        ]
        seen = set(stack)
        while stack:
            node = stack.pop()
            for target in self.out.get(node, ()):
                if target in merged:
                    return True
                if target not in seen:
                    seen.add(target)
                    stack.append(target)
        return False

    def merge(self, group: list[_Module], candidate: _Module) -> None:
        canonical = self.node[group[0].name]
        absorbed = {self.node[member.name] for member in group} | {self.node[candidate.name]}
        for name, node in self.node.items():
            if node in absorbed:
                self.node[name] = canonical
        rebuilt: dict[str, set[str]] = {}
        for node, targets in self.out.items():
            source = canonical if node in absorbed else node
            for target in targets:
                destination = canonical if target in absorbed else target
                if source != destination:
                    rebuilt.setdefault(source, set()).add(destination)
        self.out = rebuilt


# --- el plan ----------------------------------------------------------------


@dataclass
class Plan:
    """Lo que B5 va a fundir y lo que no, con el porqué de cada exclusión.

    Se publica porque la dosis perdida es un resultado del experimento: si en un
    repositorio real casi nada se puede fundir, el punto de la curva no está
    donde dice el techo de líneas, y eso hay que poder decirlo con un número
    delante en vez de deducirlo de un contador a cero.
    """

    moves: dict[str, str] = field(default_factory=dict)
    symbol_moves: dict[str, str] = field(default_factory=dict)
    candidates: int = 0
    absorbed: int = 0
    files_before: int = 0
    files_after: int = 0
    excluded: Counter[str] = field(default_factory=Counter)
    unmerged: Counter[str] = field(default_factory=Counter)


def plan(root: Path, target_lines: int = DEFAULT_TARGET_LINES) -> Plan:
    built = _build(root, target_lines)
    return built[0] if built is not None else Plan()


@dataclass(frozen=True)
class CurvePoint:
    """Un punto de la curva de §6.3 **sobre un repositorio concreto**.

    Existe porque el número de puntos no es una propiedad del diseño sino del
    sustrato, y hasta ahora solo se sabía después de escribir los árboles y
    compararlos a mano. Medido sobre los cuatro finalistas: sqlglot tiene los
    cuatro puntos (0 / 33 / 55 / 58 módulos absorbidos), pint tres —2.000 y
    10.000 dan el mismo árbol byte a byte, 20 absorbidos los dos—, y
    python-stdnum y holidays uno solo, el original, porque su dosis es cero en
    los tres techos.
    """

    transform: str
    ceiling: int | None
    absorbed: int
    files_after: int
    # Qué punto anterior produce este mismo árbol; `None` si es uno nuevo. El
    # original cuenta como punto: un techo con dosis cero apunta a él, que es
    # exactamente lo que pasa en python-stdnum y en holidays.
    same_tree_as: str | None = None

    @property
    def distinct(self) -> bool:
        return self.same_tree_as is None

    def describe(self) -> str:
        if self.ceiling is None:
            return f"original ({self.files_after} módulos)"
        return f"B5-{self.ceiling} ({self.absorbed} absorbidos, {self.files_after} módulos)"


def curve_points(root: Path, ceilings: tuple[int, ...] = CURVE) -> list[CurvePoint]:
    """Cuántas condiciones DISTINTAS produce la curva sobre este árbol.

    Se decide con `plan`, que no escribe nada, y comparando el plan de fusiones
    en su orden: dos techos con el mismo plan producen el mismo fichero byte a
    byte —lo verifica `test_two_points_with_the_same_plan_write_the_same_tree`—,
    así que no hace falta escribir los árboles para saber si son el mismo. Y hay
    que saberlo antes: escribirlos y compararlos cuesta dos corridas de suite en
    contenedor, y la que sobra se lee como un punto de la curva.

    Las exclusiones no dependen del techo (`_why_not` no lo mira), así que lo
    único que puede cambiar entre puntos es qué hermanos entran en cada grupo.
    """
    plans = {ceiling: plan(root, target_lines=ceiling) for ceiling in sorted(ceilings)}
    files_before = next(iter(plans.values())).files_before if plans else 0

    # El original es el primer punto de la curva (§6.3) y el árbol sin tocar:
    # su plan de fusiones está vacío, así que un techo de dosis cero cae aquí
    # solo, sin caso especial.
    seen: dict[tuple, str] = {(): "original"}
    points = [CurvePoint(transform="original", ceiling=None, absorbed=0, files_after=files_before)]
    for ceiling, report in plans.items():
        name = f"B5-{ceiling}"
        # En su orden y no ordenado: el orden de `moves` es el de los grupos, y
        # el orden dentro del fichero fundido es parte del árbol resultante.
        fingerprint = tuple(report.moves.items())
        points.append(
            CurvePoint(
                transform=name,
                ceiling=ceiling,
                absorbed=report.absorbed,
                files_after=report.files_after,
                same_tree_as=seen.get(fingerprint),
            )
        )
        seen.setdefault(fingerprint, name)
    return points


def _build(root: Path, target_lines: int):
    modules = _read_modules(root)
    if not modules:
        return None
    frozen = _frozen(root, modules)
    if frozen.package is None:
        return None

    report = Plan(files_before=len(modules), files_after=len(modules))
    eligible: dict[Path, list[_Module]] = {}
    for name in sorted(modules):
        info = modules[name]
        reason = _why_not(info, root, frozen)
        if reason is not None:
            report.excluded[reason] += 1
            continue
        report.candidates += 1
        eligible.setdefault(info.directory, []).append(info)

    contracted = _Contracted(modules)
    groups: list[list[_Module]] = []
    for directory in sorted(eligible):
        candidates = _dependency_order(eligible[directory])
        open_groups: list[list[_Module]] = []
        refused: dict[str, str] = {}
        for info in candidates:
            reason = "no tiene hermanos con los que fundirse"
            for group in open_groups:
                reason = _incompatible(info, group, target_lines)
                if reason is None and contracted.would_cycle(group, info):
                    reason = "cerraría un ciclo de imports"
                if reason is None:
                    contracted.merge(group, info)
                    group.append(info)
                    break
            if reason is not None:
                # Ningún grupo abierto lo quiso: abre el suyo, que puede acabar
                # absorbiendo a otros. Solo cuenta como dosis perdida si al
                # final sigue solo, y se guarda por qué se quedó fuera del
                # último que probó: es lo que hay que poder leer en la tabla.
                refused[info.name] = reason
                open_groups.append([info])
        for group in open_groups:
            if len(group) > 1:
                groups.append(group)
            else:
                report.unmerged[refused[group[0].name]] += 1

    for group in groups:
        host = group[0]
        for member in group:
            if member is not host:
                report.moves[member.name] = host.name
                report.absorbed += 1
            for definition in member.tree.body:
                if isinstance(
                    definition, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    # También las del anfitrión, y a propósito: el mapa de
                    # identidad empareja por posición mientras el módulo viaje
                    # entero, y el anfitrión recibe definiciones nuevas, así que
                    # la n-ésima ya no es la que era. Anunciarlas todas es lo
                    # que hace que se busquen por nombre (§5.4.2).
                    report.symbol_moves[f"{member.name}.{definition.name}"] = host.name
    report.files_after = len(modules) - report.absorbed
    return report, modules, groups, frozen


def _dependency_order(candidates: list[_Module]) -> list[_Module]:
    """Los hermanos ordenados de importado a importador.

    El fichero fundido se lee de arriba abajo al importarse, así que un módulo
    solo puede ir detrás de los que necesita: si el que usa se escribe antes que
    el usado, su import interno —que ya no existe— se resolvería contra un
    nombre que todavía no está definido. Por orden alfabético eso pasa la mitad
    de las veces, y cada vez que pasa es una fusión que se rechaza: medido sobre
    pint, trece de las cuarenta y dos que se quedaron fuera eran solo esto.

    Kahn con desempate alfabético, para que dos corridas de la misma celda den
    el mismo árbol (§5.4.4). Si los hermanos se importan en círculo no hay
    primero posible: se rompe por orden alfabético y la comprobación de
    compatibilidad rechaza lo que no cuadre, que es la salida conservadora.
    """
    inside = {info.name: info for info in candidates}
    pending = {
        name: {target for target in info.graph_deps if target in inside and target != name}
        for name, info in inside.items()
    }
    order: list[_Module] = []
    while pending:
        ready = sorted(name for name, waiting in pending.items() if not waiting)
        if not ready:
            ready = [min(pending)]
        for name in ready:
            order.append(inside[name])
            del pending[name]
        for waiting in pending.values():
            waiting.difference_update(ready)
    return order


# --- escribir el fichero fundido --------------------------------------------


def _merged_source(group: list[_Module]) -> str:
    """Los módulos del grupo, uno detrás de otro, en un solo fichero."""
    host = group[0]
    members = {member.name for member in group}
    body: list[cst.BaseStatement] = []
    emitted: set[str] = set()
    for position, info in enumerate(group):
        written: list[cst.BaseStatement] = []
        for statement in info.code.body:
            kept = _adjusted(statement, info, host, members, emitted, position == 0)
            if kept is not None:
                written.append(kept)
        if not written:
            continue
        if position:
            # La cabecera del absorbido —licencia, comentarios de arriba— viaja
            # con su código: quitarla sería borrar documentación, y eso es B3.
            first = written[0]
            written[0] = first.with_changes(
                leading_lines=[
                    cst.EmptyLine(),
                    cst.EmptyLine(),
                    *info.code.header,
                    *first.leading_lines,
                ]
            )
        body.extend(written)
    footer = [line for info in group for line in info.code.footer]
    return host.code.with_changes(body=body, footer=footer).code


def _adjusted(
    statement: cst.BaseStatement,
    info: _Module,
    host: _Module,
    members: set[str],
    emitted: set[str],
    is_host: bool,
) -> cst.BaseStatement | None:
    """La sentencia tal y como queda en el fichero fundido, o None si sobra."""
    if not isinstance(statement, cst.SimpleStatementLine) or len(statement.body) != 1:
        # Con dos sentencias en la misma línea no se puede quitar una sin
        # reescribir la otra, y no vale la pena: dejarla es siempre correcto.
        return statement
    small = statement.body[0]

    if isinstance(small, cst.ImportFrom) and not isinstance(small.names, cst.ImportStar):
        origin = _resolve_relative(
            "." * len(small.relative) + (_dotted(small.module) if small.module else ""),
            info.package,
        )
        if origin == "__future__":
            # Uno solo, y arriba del todo: tiene que seguir siendo la primera
            # sentencia del fichero o Python lo rechaza. Todos los del grupo
            # piden lo mismo, así que el del anfitrión vale por todos.
            return statement if is_host else None
        if origin in members and origin != info.name:
            # El símbolo ya está en este fichero, unas líneas más arriba.
            return _rebound(statement, small)

    if isinstance(small, (cst.Import, cst.ImportFrom)):
        text = cst.Module(body=[statement.with_changes(leading_lines=[])]).code.strip()
        if text in emitted:
            return None  # el mismo import, ya escrito por otro miembro
        emitted.add(text)
        return statement

    if not is_host and host.has_all and _assigns_all(small):
        # Dos `__all__` en un fichero y el segundo pisa al primero: lo que el
        # anfitrión promete desaparecería. Sumados dicen lo que el fichero
        # fundido exporta de verdad.
        return statement.with_changes(
            body=[
                cst.AugAssign(
                    target=cst.Name("__all__"), operator=cst.AddAssign(), value=small.value
                )
            ]
        )
    return statement


def _assigns_all(small: cst.BaseSmallStatement) -> bool:
    return (
        isinstance(small, cst.Assign)
        and len(small.targets) == 1
        and isinstance(small.targets[0].target, cst.Name)
        and small.targets[0].target.value == "__all__"
    )


def _rebound(
    statement: cst.SimpleStatementLine, small: cst.ImportFrom
) -> cst.BaseStatement | None:
    """Un import interno al grupo, convertido en lo que quede de él.

    Nada, si los nombres venían tal cual —ya están definidos más arriba—; y las
    asignaciones que faltan, si venían con alias: `from .a import f as g` no
    liga `f`, liga `g`, y ese nombre tiene que seguir existiendo.
    """
    aliases = [alias for alias in small.names if alias.asname is not None]
    if not aliases:
        return None
    return statement.with_changes(
        body=[
            cst.Assign(
                targets=[cst.AssignTarget(target=cst.Name(_bound_name(alias)))],
                value=cst.Name(alias.name.value),
            )
            for alias in aliases
        ]
    )


def _bound_name(alias: cst.ImportAlias) -> str:
    assert alias.asname is not None
    return _dotted(alias.asname.name)


# --- los imports del resto del repositorio ----------------------------------


class _RewriteAbsorbedModules(cst.CSTTransformer):
    """Manda al fichero que lo absorbió todo lo que nombra un módulo fundido.

    Es la misma pregunta que resuelve B2 cuando renombra un fichero, y por eso
    reutiliza sus piezas: lo que cambia es que aquí el destino ya existe y tiene
    contenido propio. Un `from .a import x` que se queda atrás no da un aviso,
    da un ModuleNotFoundError al cargar.
    """

    def __init__(self, moves: dict[str, str], current: str) -> None:
        self.moves = moves
        # El paquete DESDE EL QUE se cuentan los puntos de un import relativo,
        # que es el que contiene al fichero y no el paquete raíz.
        self.current = current

    def visit_Import(self, node: cst.Import) -> bool:
        return False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        return False

    def leave_SimpleString(
        self, original: cst.SimpleString, updated: cst.SimpleString
    ) -> cst.SimpleString:
        """Una ruta de módulo escrita a mano, y los ejemplos de doctest.

        `mock.patch("pkg.a.thing")` resuelve importando `pkg.a`, y un doctest no
        es documentación sino suite: los dos son ejecutables aunque estén dentro
        de una cadena, y los dos dejan de funcionar si el módulo se fundió.
        """
        text = updated.raw_value
        if len(text.split(".")) > 1:
            moved = _moved_dotted(text, self.moves)
            if moved is not None:
                return updated.with_changes(
                    value=f"{updated.prefix}{updated.quote}{moved}{updated.quote}"
                )
        if DOCTEST_PROMPT not in updated.value:
            return updated
        rewritten = rewrite_examples(updated.value, self.rewrite_snippet)
        return updated if rewritten == updated.value else updated.with_changes(value=rewritten)

    def rewrite_snippet(self, code: str) -> str | None:
        """El mismo trozo reescrito, o None si no cuela.

        LibCST valida al construir, y esa excepción no es de parseo: sin
        capturarla un ejemplo raro dejaría el fichero a medio transformar.
        """
        try:
            module = cst.parse_module(code)
            return module.visit(_RewriteAbsorbedModules(self.moves, self.current)).code
        except (cst.ParserSyntaxError, cst.CSTValidationError):
            return None

    def leave_Attribute(
        self, original: cst.Attribute, updated: cst.Attribute
    ) -> cst.BaseExpression:
        """`pkg.a` usado como expresión, que es lo que deja un `import pkg.a`.

        Se pregunta por el nodo ORIGINAL porque LibCST resuelve de dentro afuera
        y el hijo ya viene sustituido: así gana la coincidencia más larga, que es
        la correcta —el módulo es el fichero, no el directorio que lo contenía—.
        """
        target = self.moves.get(_dotted(original))
        return cst.parse_expression(target) if target else updated

    def leave_Import(self, original: cst.Import, updated: cst.Import) -> cst.Import:
        return updated.with_changes(
            names=[
                alias.with_changes(name=cst.parse_expression(self.moves[dotted]))
                if (dotted := _dotted(alias.name)) in self.moves
                else alias
                for alias in updated.names
            ]
        )

    def leave_ImportFrom(self, original: cst.ImportFrom, updated: cst.ImportFrom):
        written = "." * len(updated.relative) + (
            _dotted(updated.module) if updated.module is not None else ""
        )
        base = _resolve_relative(written, self.current) if updated.relative else written
        if not base:
            return updated
        target = self.moves.get(base)
        if target is not None:
            # El destino está en el mismo directorio que el origen —B5 no funde
            # entre directorios—, así que la cuenta de puntos sigue valiendo y
            # solo cambia el último tramo. Conservarla es lo que evita convertir
            # medio repositorio a imports absolutos, que sería otra condición.
            return updated.with_changes(module=_replace_tail(updated.module, target))
        if isinstance(updated.names, cst.ImportStar):
            return updated
        names = []
        for alias in updated.names:
            moved = self.moves.get(f"{base}.{alias.name.value}")
            if moved is None:
                names.append(alias)
                continue
            # `from pkg import a` traía el fichero: ahora hay que traer el que
            # lo absorbió, y con el nombre de antes para no tocar el cuerpo.
            names.append(
                alias.with_changes(
                    name=cst.Name(moved.split(".")[-1]),
                    asname=alias.asname or cst.AsName(name=cst.Name(alias.name.value)),
                    comma=cst.MaybeSentinel.DEFAULT,
                )
            )
        if all(new is old for new, old in zip(names, updated.names)):
            return updated
        return updated.with_changes(
            names=[
                *[alias.with_changes(comma=cst.MaybeSentinel.DEFAULT) for alias in names[:-1]],
                names[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT),
            ]
        )


def _replace_tail(module: cst.BaseExpression | None, target: str) -> cst.BaseExpression:
    """El mismo nombre de módulo con el último tramo cambiado.

    Se toca solo la cola porque el destino es un hermano de directorio: el
    prefijo —los puntos de un relativo o el paquete de un absoluto— es el mismo.
    """
    tail = cst.Name(target.split(".")[-1])
    if isinstance(module, cst.Attribute):
        return module.with_changes(attr=tail)
    return tail


# --- aplicar ----------------------------------------------------------------


def apply(root: Path, target_lines: int = DEFAULT_TARGET_LINES) -> TransformResult:
    built = _build(root, target_lines)
    if built is None:
        return TransformResult()
    report, _, groups, frozen = built
    if not groups:
        return TransformResult()

    changed = 0
    for group in groups:
        group[0].path.write_text(_merged_source(group), encoding="utf-8")
        changed += 1
        for member in group[1:]:
            member.path.unlink()
            changed += 1

    moves = report.moves
    # El empaquetado nombra módulos con puntos y ningún test lo ejecuta, así que
    # sin esto la celda se declararía equivalente con la interfaz pública rota.
    changed += _rewrite_entry_points(root, moves)
    changed += _rewrite_setup_script(root, moves)

    loose = _RewriteAbsorbedModules(moves, "")
    for path in doctest_files(root):
        source = read_source(path)
        # El fichero entero es texto de doctest: aquí no hay módulo que parsear.
        transformed = rewrite_examples(source, loose.rewrite_snippet)
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1

    # Alcance repo-wide, suite del repo incluida (§4.3.1): un import que se
    # quede apuntando al módulo absorbido es un fallo de colecta, y una celda
    # con la suite en rojo no mide una práctica, mide un repositorio roto.
    for path in iter_transformable_files(root):
        source = read_source(path)
        try:
            code = cst.parse_module(source)
        except cst.ParserSyntaxError:
            continue
        rewriter = _RewriteAbsorbedModules(
            moves, _module_name(path.parent / "__init__.py", root)
        )
        transformed = code.visit(rewriter).code
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1

    if frozen.package is not None:
        # El bytecode del árbol de antes nombra a los módulos absorbidos y los
        # republica en un `ls`: la dosis de B5 deshecha en un directorio que
        # nadie mira.
        _drop_stale_bytecode(frozen.package)

    return TransformResult(
        files_changed=changed,
        moves=dict(sorted(moves.items())),
        symbol_moves=dict(sorted(report.symbol_moves.items())),
    )
