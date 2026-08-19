"""B1 — cohesión: repartir las definiciones entre los ficheros que ya existen.

Lo que se destruye aquí es la señal de **qué vive con qué** (§4.2). En un repo
cohesionado, abrir `billing.py` te da el impuesto, la tarifa y el total de una
sentada: el fichero es la unidad de sentido. Repartidas al azar, esas tres
piezas siguen estando y siguen funcionando, pero ya no se encuentran juntas y el
agente tiene que reconstruir a mano la relación que el autor había escrito.

Mismo número de ficheros y mismo tamaño aproximado: el tamaño es B5, y si las
dos cosas cambiaran a la vez ninguna de las dos celdas sería atribuible.

**Lo difícil no es mover, es que siga arrancando.** Una definición usa imports,
constantes y otras definiciones de su módulo, así que mudarla obliga a llevarse
eso o a importarlo en el destino; y en cuanto dos ficheros se importan el uno al
otro en tiempo de módulo, Python muere en el `import` —no en la primera
llamada— y el repositorio entero se lee igual que un agente que fracasa (§11).
Por eso aquí se mueve **de menos**: cada definición que no se puede mudar sin
arriesgar eso se queda donde está, y `plan()` publica cuántas son y por qué,
porque esa dosis perdida es un dato del experimento y no una nota al pie.

Las tres decisiones de alcance, declaradas:

- **Se reparte dentro de cada directorio**, no por todo el paquete. Un módulo
  arrastra imports relativos (`from . import util`, `from .util import *`) y un
  hermano del mismo directorio los resuelve igual sin reescribir nada; un primo
  de otra rama no. Repartir más lejos no rompe más cohesión —el fichero deja de
  ser la unidad de sentido igual— y sí abre una clase entera de fallos.
- **Los `__init__.py` no dan ni reciben definiciones.** Son el punto de entrada
  del paquete, la misma razón por la que §5.6 no toca el nombre del paquete
  raíz: lo que definen es la superficie pública y cargarlos antes de tiempo
  cambia el orden de importación de todo lo que cuelga de ellos.
- **El grafo de imports de módulo se mantiene acíclico.** Un ciclo no da una
  dosis rara, da un `ImportError` al cargar.
"""

from __future__ import annotations

import ast
import builtins
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst

from acp.metrics.size import module_name as _module_name
from acp.metrics.size import read_source
from acp.transforms.b2_hierarchy import _package_root
from acp.transforms.base import (
    PYTEST_CONFIG_FILES,
    TransformResult,
    iter_transformable_files,
)
from acp.transforms.doctests import DOCTEST_PROMPT, doctest_files, rewrite_examples
from acp.transforms.dependencies import (
    annotation_names,
    free_names,
    module_bindings,
    star_imports,
)

_BUILTINS = frozenset(dir(builtins))

# Dunders del módulo: no son un nombre que se pueda importar, son el fichero
# donde está la definición. `__file__` y `__path__` cambian de valor al mudarse;
# `__name__` es peor, porque hay repos que construyen con él el nombre de sus
# submódulos (`f"{__name__}.{x}"`) y ahí mudar la definición manda el import a
# un módulo que no existe. Se sacan del reparto, no se les inventa un import.
_MODULE_DUNDERS = frozenset({
    "__file__", "__name__", "__package__", "__path__", "__spec__",
    "__loader__", "__doc__", "__builtins__", "__all__",
})


# --- lo que una definición le pide a su módulo -----------------------------


@dataclass(frozen=True)
class _Need:
    """Un nombre que alguien necesita, y de dónde salía antes de mover nada."""

    owner: str          # módulo que liga el nombre
    name: str
    kind: str           # "import" | "assign" | "def"
    guarded: bool       # ligado solo bajo `if TYPE_CHECKING`
    annotation_only: bool
    # Si hay que escribir un import nuevo en el destino, o si ya hay una
    # sentencia en el fichero que el reescritor de imports va a arreglar sola.
    emit: bool = True

    @property
    def key(self) -> str:
        return f"{self.owner}.{self.name}"


@dataclass
class _Holder:
    """Algo que vive en un módulo y necesita nombres de otros sitios.

    Son las definiciones que se reparten y también el código de nivel de módulo
    y los imports de cada fichero, que no se mueven pero sí generan aristas: si
    `billing.py` sigue llamando a la función que se acaba de ir, `billing.py`
    pasa a importar a su destino, y esa arista cuenta para el ciclo igual que
    las demás.
    """

    key: str
    host: str
    needs: tuple[_Need, ...]


@dataclass
class _Definition(_Holder):
    origin: str = ""
    name: str = ""
    index: int = 0                       # posición en el cuerpo del módulo
    node: cst.BaseStatement | None = None
    lines: int = 0
    needs_stars: bool = False


# --- lectura de un módulo --------------------------------------------------


@dataclass
class _ModuleInfo:
    path: Path
    name: str
    package: str                          # paquete contenedor, para los relativos
    directory: Path
    code: cst.Module
    tree: ast.Module
    bindings: dict[str, str] = field(default_factory=dict)
    guarded: frozenset[str] = frozenset()
    stars: tuple[str, ...] = ()
    imports: dict[str, str] = field(default_factory=dict)
    exported: frozenset[str] = frozenset()
    opaque_exports: bool = False
    # Símbolos de otros módulos del repo que este fichero ya importa por su
    # nombre. Manda una definición aquí y el import se convierte en un
    # `from <yo> import <yo mismo>` que revienta al cargar.
    imported_symbols: frozenset[str] = frozenset()
    is_init: bool = False
    lines: int = 0


def _read_modules(root: Path, package: Path) -> dict[str, _ModuleInfo]:
    modules: dict[str, _ModuleInfo] = {}
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
        info = _ModuleInfo(
            path=path,
            name=name,
            package=_module_name(path.parent / "__init__.py", root),
            directory=path.parent,
            code=code,
            tree=tree,
            bindings=module_bindings(tree),
            guarded=_type_checking_bindings(tree),
            stars=tuple(_star_statements(tree)),
            imports=_import_statements(tree),
            is_init=path.name == "__init__.py",
            lines=source.count("\n") + 1,
        )
        info.exported, info.opaque_exports = _exported_names(tree)
        modules[name] = info
    return modules


def _type_checking_bindings(tree: ast.Module) -> frozenset[str]:
    """Nombres que el módulo liga SOLO cuando corre un comprobador de tipos.

    Es la trampa que el análisis de dependencias dejó anotada: bajo
    `if TYPE_CHECKING` el import no se ejecuta nunca, y copiarlo desnudo al
    destino convierte un repo que arranca en uno que no —normalmente porque el
    import estaba ahí justo para romper un ciclo—. Con la guarda sí viaja.
    """
    found: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.If) and _is_type_checking(statement.test):
            block = ast.Module(body=statement.body, type_ignores=[])
            found.update(module_bindings(block))
    return frozenset(found)


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _import_statements(tree: ast.Module) -> dict[str, str]:
    """Nombre ligado → la sentencia mínima que lo trae, en texto.

    Se reconstruye una sentencia por nombre en vez de copiar la original entera
    porque la original puede traer cinco nombres de los que el destino solo
    necesita uno, y arrastrarlos todos es ensuciar el destino con dependencias
    que nadie pidió. El texto se conserva relativo si lo era: el destino está en
    el mismo directorio, así que los puntos siguen contando desde donde
    contaban.
    """
    statements: dict[str, str] = {}
    for statement in _module_scope(tree):
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                bound = (alias.asname or alias.name).split(".")[0]
                tail = f" as {alias.asname}" if alias.asname else ""
                statements[bound] = f"import {alias.name}{tail}"
        elif isinstance(statement, ast.ImportFrom):
            origin = "." * statement.level + (statement.module or "")
            for alias in statement.names:
                if alias.name == "*":
                    continue
                tail = f" as {alias.asname}" if alias.asname else ""
                bound = alias.asname or alias.name
                statements[bound] = f"from {origin} import {alias.name}{tail}"
    return statements


def _star_statements(tree: ast.Module) -> list[str]:
    """Las sentencias `import *` del módulo, en texto y en orden.

    Viajan enteras con la definición que las necesita: lo que traen no se sabe
    sin importarlas, así que no hay forma de escribir un import más estrecho. En
    python-stdnum es de donde sale el 18% de lo que sus definiciones piden.
    """
    return [f"from {origin} import *" for origin in star_imports(tree)]


def _module_scope(tree: ast.Module) -> list[ast.stmt]:
    """Sentencias que corren en el ámbito del módulo, guardas incluidas.

    La mitad de los imports de un repo real viven dentro de un
    `try/except ImportError` o de un `if`, y leerlos solo en el primer nivel de
    indentación deja esa mitad sin explicación.
    """
    found: list[ast.stmt] = []

    def descend(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            found.append(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # otro ámbito
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.stmt, ast.ExceptHandler, ast.match_case)):
                    descend([child])  # type: ignore[list-item]

    descend(tree.body)
    return found


def _exported_names(tree: ast.Module) -> tuple[frozenset[str], bool]:
    """Lo que dice `__all__`, y si se puede leer entero.

    Un nombre listado en `__all__` no puede irse del módulo: `from x import *`
    lo busca ahí por nombre y falla con AttributeError si no está. Y si el
    `__all__` se construye con algo que no son literales, no se sabe qué
    promete, así que del módulo no sale nada.
    """
    names: set[str] = set()
    opaque = False
    for statement in _module_scope(tree):
        values: list[ast.expr] = []
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in statement.targets
        ):
            values = [statement.value]
        elif (
            isinstance(statement, ast.AugAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "__all__"
        ):
            values = [statement.value]
        for value in values:
            if not isinstance(value, (ast.List, ast.Tuple)):
                opaque = True
                continue
            for element in value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    names.add(element.value)
                else:
                    opaque = True
    return frozenset(names), opaque


# --- referencias que no se pueden reescribir -------------------------------


# Una cadena de identificadores unida por puntos, mire donde mire: en código
# (`pkg.util.clean`), en un texto que un test compara, o en una docstring.
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")

# Fuera del código el separador también puede ser dos puntos: es como el
# empaquetado nombra un símbolo concreto de un módulo
# (`console_scripts = stdnum = stdnum.cli:main`), y ese nombre no está en
# ningún import que se pueda reescribir.
_DOTTED_OR_COLON = re.compile(r"[A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)+")


def _texts_outside_the_code(root: Path) -> list[str]:
    """Lo que nombra símbolos y no es un `.py`: doctests y empaquetado.

    Los dos son ejecutables aunque no lo parezcan —python-stdnum corre 234
    líneas de ejemplo desde ficheros `.doctest`, y un `console_scripts` roto es
    una instalación que falla— y ninguno de los dos pasa por el reescritor de
    imports de LibCST.
    """
    found: list[str] = []
    for path in doctest_files(root):
        found.append(read_source(path))
    for name in (*PYTEST_CONFIG_FILES, "setup.py"):
        path = root / name
        if path.is_file():
            found.append(read_source(path))
    return found


def _mentioned_attributes(modules: dict[str, _ModuleInfo], root: Path) -> set[str]:
    """`modulo.simbolo` escrito en cualquier sitio del repo, texto incluido.

    Un `from x import y` se reescribe con el árbol y no ata a nadie. Lo que ata
    es la otra forma de nombrar lo mismo: `util.clean(...)` después de un
    `from . import util`, `pkg.util.clean(...)` después de un `import pkg.util`,
    o la ruta escrita dentro de una cadena que un test compara. Las tres se
    reconocen igual de mal —haría falta resolver a qué módulo apunta cada nombre
    local— así que se buscan por texto y lo que aparezca se saca del reparto.
    Es dosis perdida a cambio de no publicar un repo que no importa.

    Se recorre cada fichero UNA vez y se cruza por la última parte del nombre
    del módulo: cruzar cada módulo contra cada fichero es cuadrático, y con los
    1.390 ficheros del sustrato eso son dos millones de búsquedas por corrida.
    """
    tails: dict[str, list[str]] = {}
    for name in modules:
        tails.setdefault(name.rsplit(".", 1)[-1], []).append(name)
    found: set[str] = set()
    for info in modules.values():
        for chain in _DOTTED.findall(read_source(info.path)):
            parts = chain.split(".")
            for head, attribute in zip(parts, parts[1:]):
                for owner in tails.get(head, ()):
                    # Dentro de su propio fichero la referencia es local y se
                    # mueve con la definición; lo que ata es nombrarla de fuera.
                    if owner != info.name:
                        found.add(f"{owner}.{attribute}")
    for text in _texts_outside_the_code(root):
        for chain in _DOTTED_OR_COLON.findall(text):
            parts = re.split(r"[.:]", chain)
            for head, attribute in zip(parts, parts[1:]):
                for owner in tails.get(head, ()):
                    found.add(f"{owner}.{attribute}")
    return found


def _star_importers(modules: dict[str, _ModuleInfo]) -> set[str]:
    """Módulos del repo de los que alguien hace `import *`.

    De ahí no puede salir nada: quien hace `from x import *` se queda con los
    nombres que x tenga en ese momento, y no hay import que reescribir porque
    el nombre no está escrito en ninguna parte.
    """
    found: set[str] = set()
    for info in modules.values():
        for origin in star_imports(info.tree):
            resolved = _resolve_relative(origin, info.package)
            if resolved in modules:
                found.add(resolved)
    return found


def _resolve_relative(origin: str, package: str) -> str:
    """Un origen de import (con puntos o sin ellos) en forma absoluta."""
    level = len(origin) - len(origin.lstrip("."))
    tail = origin[level:]
    if level == 0:
        return tail
    parts = package.split(".") if package else []
    kept = len(parts) - (level - 1)
    if kept < 0:
        return tail
    base = parts[:kept]
    return ".".join([*base, *tail.split(".")]) if tail else ".".join(base)


# --- el plan ---------------------------------------------------------------


@dataclass
class Plan:
    """Lo que B1 va a mover y lo que no, con el porqué de cada exclusión.

    Se publica porque la dosis perdida es un resultado del experimento: si en un
    repositorio real la mayoría de las definiciones no se puede mover, B1 mide
    mucho menos de lo que el spec supone y eso hay que poder decirlo con un
    número delante.
    """

    symbol_moves: dict[str, str] = field(default_factory=dict)
    candidates: int = 0
    excluded: Counter[str] = field(default_factory=Counter)


def plan(root: Path, seed: int = 0) -> Plan:
    built = _build(root, seed)
    return built[0] if built else Plan()


def _build(root: Path, seed: int):
    """Todo lo que hace falta para escribir el árbol nuevo, o None si no aplica."""
    package = _package_root(root)
    if package is None:
        return None
    modules = _read_modules(root, package)
    if not modules:
        return None

    # Quién puede dar y recibir definiciones: ficheros del paquete que no son el
    # `__init__`, agrupados por directorio. Un directorio con un solo módulo no
    # tiene con quién repartir.
    inside = {
        name: info
        for name, info in modules.items()
        if not info.is_init and (package == info.path.parent or package in info.path.parents)
    }
    siblings: dict[Path, list[str]] = {}
    for name, info in inside.items():
        siblings.setdefault(info.directory, []).append(name)
    for names in siblings.values():
        names.sort()

    imported = {name: _imported_symbol_needs(info, modules) for name, info in modules.items()}
    for name, info in modules.items():
        info.imported_symbols = frozenset(need.key for need in imported[name])

    frozen = _star_importers(modules)
    mentioned = _mentioned_attributes(modules, root)

    report = Plan()
    definitions: list[_Definition] = []
    holders: list[_Holder] = []
    for name in sorted(modules):
        info = modules[name]
        movable = (
            name in inside
            and len(siblings.get(info.directory, ())) > 1
            and name not in frozen
            and not info.opaque_exports
        )
        holders.append(_module_level_holder(info, imported[name]))
        for definition in _definitions_of(info, modules):
            if not movable:
                report.excluded["módulo congelado"] += 1
                holders.append(definition)
                continue
            report.candidates += 1
            reason = _why_not(definition, info, mentioned)
            if reason is not None:
                report.excluded[reason] += 1
            else:
                definitions.append(definition)
            holders.append(definition)

    graph = _Graph(holders)
    _assign(definitions, siblings, modules, graph, seed, report)

    for definition in definitions:
        if definition.host != definition.origin:
            report.symbol_moves[definition.key] = definition.host
    return report, modules, definitions, holders, graph, root


def _definitions_of(info: _ModuleInfo, modules: dict[str, _ModuleInfo]) -> list[_Definition]:
    """Las definiciones de nivel de módulo, emparejando `ast` con LibCST.

    Los dos árboles leen el mismo texto y recorren el cuerpo en el mismo orden,
    así que la n-ésima definición de uno es la n-ésima del otro: `ast` responde
    a las preguntas de ámbito y LibCST guarda el texto exacto que hay que mover.
    """
    trees = [
        node
        for node in info.tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    positions = [
        index
        for index, node in enumerate(info.code.body)
        if isinstance(node, (cst.FunctionDef, cst.ClassDef))
    ]
    if len(trees) != len(positions):
        return []
    found: list[_Definition] = []
    for node, index in zip(trees, positions):
        needs, stars, unresolved = _needs_of(node, info)
        found.append(
            _Definition(
                key=f"{info.name}.{node.name}",
                host=info.name,
                needs=tuple(needs),
                origin=info.name,
                name=node.name,
                index=index,
                node=info.code.body[index],
                lines=(node.end_lineno or node.lineno) - node.lineno + 1,
                needs_stars=stars,
            )
        )
        if unresolved:
            found[-1].needs = (*found[-1].needs, _Need(info.name, "", "unresolved", False, False))
    return found


def _needs_of(node: ast.AST, info: _ModuleInfo) -> tuple[list[_Need], bool, bool]:
    """Lo que la definición le pide a su módulo, clasificado."""
    own = getattr(node, "name", "")
    # `free_names` incluye el propio nombre de la definición —la liga el ámbito
    # de fuera— y los builtins; quien mueve sabe que ninguno de los dos hay que
    # importarlo.
    wanted = free_names(node) - _BUILTINS - {own}
    annotations = annotation_names(node)
    needs: list[_Need] = []
    stars = False
    unresolved = False
    for name in sorted(wanted):
        if name in _MODULE_DUNDERS:
            unresolved = True
            continue
        kind = info.bindings.get(name)
        if kind is None:
            # Lo único que puede explicar un nombre que el módulo no liga es un
            # `import *`; si no lo hay, no hay nada que llevarse al destino.
            if info.stars:
                stars = True
            else:
                unresolved = True
            continue
        needs.append(
            _Need(
                owner=info.name,
                name=name,
                kind=kind,
                guarded=name in info.guarded,
                annotation_only=name in annotations,
            )
        )
    return needs, stars, unresolved


def _imported_symbol_needs(info: _ModuleInfo, modules: dict[str, _ModuleInfo]) -> list[_Need]:
    """Los `from <módulo del repo> import <símbolo>` que el fichero ya tiene.

    No hay que escribirlos —el reescritor los manda solo al destino cuando el
    símbolo se muda— pero sí cuentan como arista: al mudarse el símbolo, este
    fichero pasa a importar a otro, y ese es exactamente el tipo de arista que
    cierra un ciclo. Contar solo los imports que B1 escribe dejaría fuera del
    control justo los que B1 redirige.
    """
    needs: list[_Need] = []
    for statement in _module_scope(info.tree):
        if not isinstance(statement, ast.ImportFrom):
            continue
        origin = _resolve_relative("." * statement.level + (statement.module or ""), info.package)
        source = modules.get(origin)
        if source is None:
            continue
        for alias in statement.names:
            if alias.name == "*":
                continue
            # Un submódulo no es un símbolo: `from . import util` importa el
            # fichero, y ese no se mueve. Contarlo como arista dura además
            # inventaría el ciclo paquete↔submódulo que Python sí tolera.
            if f"{origin}.{alias.name}" in modules or alias.name in source.guarded:
                continue
            needs.append(
                _Need(origin, alias.name, source.bindings.get(alias.name, "assign"),
                      False, False, emit=False)
            )
    return needs


def _module_level_holder(info: _ModuleInfo, imported: list[_Need]) -> _Holder:
    """Lo que el fichero necesita fuera de sus definiciones.

    Dos cosas distintas que producen la misma arista: el código de nivel de
    módulo que llama a una función suya —si esa función se va, el fichero pasa a
    importarla— y los imports de símbolo que ya tiene escritos.
    """
    plain = [
        statement
        for statement in info.tree.body
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    block = ast.Module(body=plain, type_ignores=[])
    needs: list[_Need] = []
    for name in sorted(free_names(block) - _BUILTINS - _MODULE_DUNDERS):
        kind = info.bindings.get(name)
        if kind is not None:
            needs.append(_Need(info.name, name, kind, name in info.guarded, False))
    return _Holder(key=f"{info.name}::module", host=info.name, needs=(*needs, *imported))


def _why_not(definition: _Definition, info: _ModuleInfo, mentioned: set[str]) -> str | None:
    """Por qué esta definición no entra en el reparto, o None si entra."""
    if any(need.kind == "unresolved" for need in definition.needs):
        return "necesita algo que no se puede importar"
    if definition.name in info.exported:
        return "listada en __all__"
    if definition.key in mentioned:
        return "referenciada como atributo o dentro de un texto"
    if _declares_global(definition, info):
        return "usa global"
    return None


def _declares_global(definition: _Definition, info: _ModuleInfo) -> bool:
    """Si la definición declara `global`, o sea si muta el estado del módulo.

    No pide un nombre, pide el diccionario del módulo donde está escrita: al
    mudarla, el `global X` empieza a escribir en OTRO módulo y quien lee X sigue
    leyendo el de antes. No hay import que arregle eso.
    """
    for node in info.tree.body:
        if getattr(node, "name", None) != definition.name:
            continue
        return any(isinstance(child, ast.Global) for child in ast.walk(node))
    return False


# --- el grafo de imports de módulo -----------------------------------------


class _Graph:
    """Aristas módulo → módulo que produce el reparto, y si tienen ciclo.

    Un ciclo de imports de nivel de módulo no degrada nada: mata el `import`. Y
    con el reparto al azar es lo normal, no lo raro —basta con que `a` mande una
    función que usa una constante de `a` a `b`, y `b` otra a `a`—, así que la
    comprobación no es una guarda de borde, es la que decide la dosis.
    """

    def __init__(self, holders: list[_Holder]) -> None:
        self.holders = {holder.key: holder for holder in holders}
        self.home: dict[str, str] = {}
        self.dependents: dict[str, list[str]] = {}
        for holder in holders:
            for need in holder.needs:
                self.dependents.setdefault(need.key, []).append(holder.key)
        self.edges: Counter[tuple[str, str]] = Counter()
        self.by_holder: dict[str, set[tuple[str, str]]] = {}
        for holder in holders:
            self._refresh(holder.key)

    def resolve(self, need: _Need) -> str:
        return self.home.get(need.key, need.owner)

    def _refresh(self, key: str) -> None:
        holder = self.holders[key]
        fresh = {
            (holder.host, self.resolve(need))
            for need in holder.needs
            if need.kind != "unresolved" and self.resolve(need) != holder.host
        }
        for edge in self.by_holder.get(key, set()) - fresh:
            self.edges[edge] -= 1
            if self.edges[edge] <= 0:
                del self.edges[edge]
        for edge in fresh - self.by_holder.get(key, set()):
            self.edges[edge] += 1
        self.by_holder[key] = fresh

    def try_move(self, definition: _Definition, target: str) -> bool:
        """Mueve si el grafo sigue siendo acíclico; si no, lo deja como estaba."""
        previous = definition.host
        touched = [definition.key, *self.dependents.get(definition.key, ())]
        definition.host = target
        self.home[definition.key] = target
        for key in touched:
            self._refresh(key)
        if not self._has_cycle():
            return True
        definition.host = previous
        self.home[definition.key] = previous
        for key in touched:
            self._refresh(key)
        return False

    def _has_cycle(self) -> bool:
        adjacency: dict[str, set[str]] = {}
        indegree: Counter[str] = Counter()
        nodes: set[str] = set()
        for origin, target in self.edges:
            adjacency.setdefault(origin, set()).add(target)
            indegree[target] += 1
            nodes.update((origin, target))
        queue = [node for node in nodes if not indegree[node]]
        seen = 0
        while queue:
            node = queue.pop()
            seen += 1
            for neighbour in adjacency.get(node, ()):
                indegree[neighbour] -= 1
                if not indegree[neighbour]:
                    queue.append(neighbour)
        return seen != len(nodes)


# --- el reparto ------------------------------------------------------------


def _assign(
    definitions: list[_Definition],
    siblings: dict[Path, list[str]],
    modules: dict[str, _ModuleInfo],
    graph: _Graph,
    seed: int,
    report: Plan,
) -> None:
    """Reparte al azar pero con seed fijo: dos corridas de la misma celda tienen
    que dar el mismo árbol o los seeds del 2×2 no son comparables (§5.4.4)."""
    rng = random.Random(seed)
    load = {name: info.lines for name, info in modules.items()}
    taken = {name: set(info.bindings) for name, info in modules.items()}

    order = sorted(definitions, key=lambda item: item.key)
    rng.shuffle(order)
    for definition in order:
        candidates = [
            name
            for name in siblings[modules[definition.origin].directory]
            if name != definition.origin
        ]
        moved = False
        for target in _preference(rng, candidates, load):
            if _blocked(definition, target, modules, taken):
                continue
            if graph.try_move(definition, target):
                # El nombre no se libera en el origen: si el módulo sigue
                # usándolo se le vuelve a importar ahí, así que sigue ocupado.
                taken[target].add(definition.name)
                load[target] += definition.lines
                load[definition.origin] -= definition.lines
                moved = True
                break
        if not moved:
            report.excluded["no cabía sin cerrar un ciclo de imports"] += 1


def _preference(rng: random.Random, candidates: list[str], load: dict[str, int]) -> list[str]:
    """Los destinos en orden aleatorio, con más probabilidad los más vacíos.

    El azar es lo que rompe la cohesión; el peso es lo que mantiene el tamaño de
    los ficheros parecido al de antes, que es la mitad de la definición de B1
    (§4.2) y lo que la separa de B5.
    """
    pending = list(candidates)
    order: list[str] = []
    while pending:
        weights = [1.0 / (1 + max(load[name], 0)) for name in pending]
        chosen = rng.choices(pending, weights=weights, k=1)[0]
        pending.remove(chosen)
        order.append(chosen)
    return order


def _blocked(
    definition: _Definition,
    target: str,
    modules: dict[str, _ModuleInfo],
    taken: dict[str, set[str]],
) -> bool:
    """Lo que hace inviable un destino concreto, antes de mirar el ciclo."""
    origin = modules[definition.origin]
    destination = modules[target]
    if definition.name in taken[target]:
        return True  # taparía una definición del destino
    if definition.key in destination.imported_symbols:
        # El destino ya importa este símbolo por su nombre, con o sin alias:
        # llevárselo ahí convierte ese import en un `from yo import yo mismo`
        # que se ejecuta antes de que la definición exista.
        return True
    if definition.needs_stars and not set(origin.stars) <= set(destination.stars):
        # Los `import *` del origen viajan con la definición, pero solo si el
        # destino no tiene los suyos: dos juegos de estrellas y quién gana
        # depende del orden, que es exactamente lo que no se puede razonar.
        if destination.stars:
            return True
    for need in definition.needs:
        if need.guarded:
            # El import viaja con su `if TYPE_CHECKING`, y eso solo es
            # equivalente si el destino tampoco evalúa las anotaciones.
            if not need.annotation_only or not _defers_annotations(destination):
                return True
        if need.name in taken[target] and need.owner != target:
            # El destino ya liga ese nombre con otra cosa: el import que
            # tendríamos que escribir arriba lo taparía su propia definición y
            # la función movida acabaría llamando a otra cosa, en silencio.
            if origin.imports.get(need.name) != destination.imports.get(need.name):
                return True
    return False


def _defers_annotations(info: _ModuleInfo) -> bool:
    """Si el módulo tiene `from __future__ import annotations` (PEP 563).

    Con él las anotaciones no se evalúan nunca, que es lo único que hace
    equivalente copiar un import bajo `if TYPE_CHECKING`; sin él, la anotación
    se evalúa al definir y ese import guardado no basta.
    """
    return any(
        isinstance(statement, ast.ImportFrom) and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in info.tree.body
    )


# --- escribir el árbol nuevo ------------------------------------------------


@dataclass
class _Emission:
    """Los imports que hay que añadir en la cabecera de un fichero."""

    stars: list[str] = field(default_factory=list)
    plain: dict[str, str] = field(default_factory=dict)
    guarded: dict[str, str] = field(default_factory=dict)
    from_home: dict[str, set[str]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.stars or self.plain or self.guarded or self.from_home)


def _emissions(
    modules: dict[str, _ModuleInfo], holders: list[_Holder], graph: _Graph
) -> dict[str, _Emission]:
    """Qué import le falta a cada fichero después del reparto.

    Sale de la misma tabla que las aristas del grafo, y a propósito: si el
    control de ciclos y los imports que se escriben se calcularan por separado,
    el día que discreparan el repositorio se publicaría con un ciclo que nadie
    vio venir. Aquí una arista y un import son lo mismo mirado dos veces.
    """
    found: dict[str, _Emission] = {}
    for holder in holders:
        host = holder.host
        for need in holder.needs:
            if not need.emit or need.kind == "unresolved":
                continue
            if need.kind == "import":
                if need.owner == host:
                    continue
                text = modules[need.owner].imports.get(need.name)
                if text is None or modules[host].imports.get(need.name) == text:
                    continue
                emission = found.setdefault(host, _Emission())
                target = emission.guarded if need.guarded else emission.plain
                target[need.name] = text
                continue
            resolved = graph.resolve(need)
            if resolved == host:
                continue
            emission = found.setdefault(host, _Emission())
            emission.from_home.setdefault(resolved, set()).add(need.name)
    for holder in holders:
        if not isinstance(holder, _Definition) or holder.host == holder.origin:
            continue
        if not holder.needs_stars:
            continue
        emission = found.setdefault(holder.host, _Emission())
        for text in modules[holder.origin].stars:
            if text not in modules[holder.host].stars and text not in emission.stars:
                emission.stars.append(text)
    return found


def _emitted_statements(
    emission: _Emission, info: _ModuleInfo
) -> list[cst.BaseStatement]:
    """Las sentencias de import nuevas, en el orden en que tienen que quedar.

    Primero las estrellas y luego lo concreto: al insertarse todo en la cabecera,
    lo que va después gana, y lo que la definición movida pidió por su nombre
    tiene que ganarle a lo que un `import *` traiga por casualidad.
    """
    config = info.code.config_for_parsing
    statements: list[cst.BaseStatement] = [
        cst.parse_statement(text, config=config) for text in emission.stars
    ]
    for name in sorted(emission.plain):
        statements.append(cst.parse_statement(emission.plain[name], config=config))
    for origin in sorted(emission.from_home):
        names = ", ".join(sorted(emission.from_home[origin]))
        statements.append(cst.parse_statement(f"from {origin} import {names}", config=config))
    if emission.guarded:
        if "TYPE_CHECKING" not in info.bindings:
            statements.append(
                cst.parse_statement("from typing import TYPE_CHECKING", config=config)
            )
        block = "if TYPE_CHECKING:\n" + "".join(
            f"    {emission.guarded[name]}\n" for name in sorted(emission.guarded)
        )
        statements.append(cst.parse_statement(block, config=config))
    return statements


def _header_end(body: list[cst.BaseStatement]) -> int:
    """Dónde acaba la cabecera intocable: docstring y `from __future__`.

    El futuro import tiene que seguir siendo la primera sentencia del fichero o
    Python lo rechaza, así que lo que se añade va justo después y nunca antes.
    """
    index = 0
    for position, statement in enumerate(body):
        if not isinstance(statement, cst.SimpleStatementLine) or len(statement.body) != 1:
            break
        small = statement.body[0]
        if (
            position == 0
            and isinstance(small, cst.Expr)
            and isinstance(small.value, (cst.SimpleString, cst.ConcatenatedString))
        ):
            index = position + 1
            continue
        if (
            isinstance(small, cst.ImportFrom)
            and isinstance(small.module, cst.Name)
            and small.module.value == "__future__"
        ):
            index = position + 1
            continue
        break
    return index


def _spaced(node: cst.BaseStatement) -> cst.BaseStatement:
    """La definición con dos líneas en blanco delante, como la dejaría un humano.

    Se tiran las líneas en blanco que traía y se conservan sus comentarios: el
    comentario de encima de una función es suyo y viaja con ella; el hueco que
    la separaba de la anterior era del fichero de antes.
    """
    lines = list(node.leading_lines)
    while lines and lines[0].comment is None:
        lines.pop(0)
    return node.with_changes(leading_lines=[cst.EmptyLine(), cst.EmptyLine(), *lines])


def _rebuilt(
    info: _ModuleInfo,
    departed: set[int],
    received: list[_Definition],
    emission: _Emission | None,
) -> str | None:
    body = list(info.code.body)
    kept = [node for index, node in enumerate(body) if index not in departed]
    statements = _emitted_statements(emission, info) if emission else []
    incoming = [_spaced(definition.node) for definition in received if definition.node]
    if not statements and not incoming and not departed:
        return None
    cut = _header_end(kept)
    return info.code.with_changes(
        body=[*kept[:cut], *statements, *kept[cut:], *incoming]
    ).code


# --- los imports del resto del repositorio ---------------------------------


class _RewriteMovedSymbols(cst.CSTTransformer):
    """Manda al destino los `from x import y` que nombran un símbolo movido.

    Solo esta forma: la otra —`x.y` como expresión— exige saber a qué módulo
    apunta cada nombre local, y las definiciones que se nombran así ya se
    quedaron fuera del reparto (`_mentioned_attributes`). Preferir mover de
    menos a reescribir a ciegas.
    """

    def __init__(self, moves: dict[str, str], package: str, current: str) -> None:
        self.moves = moves
        self.package = package
        self.current = current

    def visit_Import(self, node: cst.Import) -> bool:
        return False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        return False

    def leave_SimpleString(
        self, original: cst.SimpleString, updated: cst.SimpleString
    ) -> cst.SimpleString:
        """Un doctest no es documentación, es suite.

        python-stdnum corre la suya con `--doctest-modules`, así que un ejemplo
        que importa un símbolo desde el módulo de antes es un test en rojo, y
        una celda con la suite en rojo se lee como un repositorio roto y no como
        la práctica que la celda quería quitar.
        """
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
            return module.visit(
                _RewriteMovedSymbols(self.moves, self.package, self.current)
            ).code
        except (cst.ParserSyntaxError, cst.CSTValidationError):
            return None

    def leave_ImportFrom(self, original: cst.ImportFrom, updated: cst.ImportFrom):
        if isinstance(updated.names, cst.ImportStar):
            return updated
        origin = "." * len(updated.relative) + (
            _dotted(updated.module) if updated.module is not None else ""
        )
        base = _resolve_relative(origin, self.package) if updated.relative else origin
        if not base:
            return updated
        groups: dict[str, list[cst.ImportAlias]] = {}
        for alias in updated.names:
            target = self.moves.get(f"{base}.{alias.name.value}", base)
            groups.setdefault(target, []).append(alias)
        if list(groups) == [base]:
            return updated
        statements = [
            _import_from(updated, target, aliases) for target, aliases in groups.items()
        ]
        return statements[0] if len(statements) == 1 else cst.FlattenSentinel(statements)


def _dotted(node: cst.BaseExpression) -> str:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted(node.value)}.{node.attr.value}"
    return ""


def _import_from(
    node: cst.ImportFrom, base: str, aliases: list[cst.ImportAlias]
) -> cst.ImportFrom:
    """El mismo import apuntando a `base`, en forma absoluta.

    Absoluta porque el símbolo puede haber caído en un módulo que desde aquí no
    se alcanza con los mismos puntos, y el nombre del paquete raíz es lo único
    que sigue valiendo igual desde cualquier sitio (§5.6).
    """
    trimmed = [alias.with_changes(comma=cst.MaybeSentinel.DEFAULT) for alias in aliases]
    return node.with_changes(
        module=cst.parse_expression(base), relative=[], names=trimmed
    )


def apply(root: Path, seed: int = 0) -> TransformResult:
    built = _build(root, seed)
    if built is None:
        return TransformResult()
    report, modules, definitions, holders, graph, root = built
    if not report.symbol_moves:
        return TransformResult()

    emissions = _emissions(modules, holders, graph)
    departed: dict[str, set[int]] = {}
    received: dict[str, list[_Definition]] = {}
    for definition in sorted(definitions, key=lambda item: item.key):
        if definition.host == definition.origin:
            continue
        departed.setdefault(definition.origin, set()).add(definition.index)
        received.setdefault(definition.host, []).append(definition)

    changed = 0
    for name in sorted(set(departed) | set(received) | set(emissions)):
        info = modules[name]
        rebuilt = _rebuilt(
            info, departed.get(name, set()), received.get(name, []), emissions.get(name)
        )
        if rebuilt is None or rebuilt == read_source(info.path):
            continue
        info.path.write_text(rebuilt, encoding="utf-8")
        changed += 1

    # Alcance repo-wide, suite del repo incluida (§4.3.1): un import que se
    # quede apuntando al módulo de antes es un fallo de colecta, y una celda con
    # la suite en rojo no mide una práctica, mide un repositorio roto.
    package = _package_root(root)
    assert package is not None
    loose = _RewriteMovedSymbols(report.symbol_moves, package.name, "")
    for path in doctest_files(root):
        source = read_source(path)
        # El fichero entero es texto de doctest: aquí no hay módulo que parsear.
        transformed = rewrite_examples(source, loose.rewrite_snippet)
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1

    for path in iter_transformable_files(root):
        source = read_source(path)
        try:
            code = cst.parse_module(source)
        except cst.ParserSyntaxError:
            continue
        rewriter = _RewriteMovedSymbols(
            report.symbol_moves,
            package.name,
            _module_name(path.parent / "__init__.py", root),
        )
        transformed = code.visit(rewriter).code
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1

    return TransformResult(
        files_changed=changed, symbol_moves=dict(sorted(report.symbol_moves.items()))
    )
