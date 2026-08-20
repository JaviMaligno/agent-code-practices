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
from acp.transforms.modulegraph import components

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


def _reaches_into_the_import_system(source: str, bindings: dict[str, str]) -> bool:
    """Si el módulo se manipula a sí mismo como entrada del sistema de imports.

    `sys.modules[__name__] = algo` y el `__getattr__` de módulo (PEP 562) hacen
    lo mismo desde dos sitios: convierten el nombre del módulo en parte del
    programa. Al fundirlo, ese nombre desaparece y `__getattr__` pasa a
    contestar por los atributos del otro módulo también, que es un cambio de
    comportamiento que ningún import arregla.
    """
    return "sys.modules" in source or bool({"__getattr__", "__dir__"} & set(bindings))


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
    computed: frozenset[str]
    named: frozenset[str]
    star_targets: frozenset[str]


def _why_not(info: _Module, root: Path, frozen: _Frozen) -> str | None:
    """Por qué este módulo no puede fundirse con nadie, o None si puede."""
    if info.is_init:
        return "es el __init__ del paquete"
    if info.is_test:
        return "es suite del repositorio"
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
        return "la suite lo nombra dentro de un texto"
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
        package=_package_root(root),
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

    Fundir dos módulos contrae dos nodos en uno, y eso crea un ciclo en cuanto
    hubiera un camino entre ellos que pase por fuera del grupo. Se parte de la
    condensación —cada componente fuertemente conexa es ya un nodo— porque un
    repositorio puede importarse en círculo y sobrevivir; sin tolerar lo que ya
    estaba, un solo ciclo de partida rechazaría todas las fusiones y la celda
    saldría con dosis cero, que se lee igual que una que preserva el repo.
    """

    def __init__(self, modules: dict[str, _Module]) -> None:
        edges = [
            (name, target)
            for name, info in modules.items()
            for target in sorted(info.graph_deps)
            if target in modules
        ]
        labels = components(edges)
        self.node = {name: f"c{labels[name]}" if name in labels else name for name in modules}
        self.out: dict[str, set[str]] = {}
        for origin, target in edges:
            source, destination = self.node[origin], self.node[target]
            if source != destination:
                self.out.setdefault(source, set()).add(destination)

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
