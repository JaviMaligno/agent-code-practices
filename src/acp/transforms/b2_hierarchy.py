"""B2 — jerarquía: aplanar los directorios del paquete y renombrar los ficheros.

Lo que se destruye aquí es la señal de dónde mirar (§4.2 del spec): en un repo
con jerarquía, `stdnum/es/nif.py` te dice a la vez el país y el documento sin
abrir nada. Aplanado y renombrado a `stdnum/m17.py`, esa información ya no
existe y el agente tiene que ir a buscarla. Es la celda que se cruza con la
dotación pobre (§5.2): sin grep, encontrar el sitio depende exactamente de lo
que B2 quita.

El directorio del paquete raíz sobrevive (§5.6). Es lo único que mantiene
válidos a la vez la instalación de dependencias, los imports desde fuera y el
comando de test; lo que se aplana es todo lo de dentro.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files
from acp.transforms.doctests import DOCTEST_PROMPT, doctest_files, rewrite_examples


def _package_root(root: Path) -> Path | None:
    """El directorio del paquete, que es lo único que no se aplana.

    Se exige que haya exactamente uno: con dos paquetes de primer nivel no está
    claro cuál es el punto de entrada que hay que conservar, y aplanar el
    equivocado deja el repo sin forma de importarse. Sin candidato claro, B2 no
    hace nada y la celda se declara como no aplicable a ese repo.
    """
    candidates = [
        path
        for path in sorted(root.iterdir())
        if path.is_dir() and (path / "__init__.py").exists()
    ]
    return candidates[0] if len(candidates) == 1 else None


def _module_name(path: Path, root: Path) -> str:
    """El módulo que este fichero es, en la forma en que se importa.

    Se calcula relativo a la raíz del árbol porque es lo que hay en
    `sys.path` cuando la suite corre —el repo se alcanza por ruta, no por
    instalación (§5.6)—, y también es la clave con la que `build_symbol_map`
    nombra los módulos: si las dos formas no coincidieran, el mapa de identidad
    no podría seguir ningún movimiento.
    """
    relative = path.relative_to(root).with_suffix("")
    parts = relative.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


DYNAMIC_IMPORTERS = ("__import__", "import_module")


def _literal_head(node: cst.BaseExpression) -> str | None:
    """La parte fija de un nombre de módulo que se termina de construir al correr.

    None cuando el nombre es literal entero —ahí no hay nada dinámico— o cuando
    no se puede leer nada fijo. Una cadena vacía significa «podría ser
    cualquiera», y eso no es evidencia de que alcance a este repo: pint importa
    clases de terceros con `import_module(module_name)`, y tratar eso como una
    amenaza dejaría sin aplicar B2 al único finalista con jerarquía profunda.
    """
    if isinstance(node, cst.BinaryOperation) and isinstance(node.left, cst.SimpleString):
        text = node.left.raw_value
        if isinstance(node.operator, cst.Modulo):
            return text.split("%")[0]
        if isinstance(node.operator, cst.Add):
            return text
    if (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and node.func.attr.value == "format"
        and isinstance(node.func.value, cst.SimpleString)
    ):
        return node.func.value.raw_value.split("{")[0]
    if isinstance(node, cst.FormattedString):
        head = ""
        for part in node.parts:
            if not isinstance(part, cst.FormattedStringText):
                return head
            head += part.value
        return None
    return None


class _CollectComputedPrefixes(cst.CSTVisitor):
    def __init__(self) -> None:
        self.prefixes: set[str] = set()

    def visit_Call(self, node: cst.Call) -> None:
        name = node.func.attr.value if isinstance(node.func, cst.Attribute) else None
        if isinstance(node.func, cst.Name):
            name = node.func.value
        if name not in DYNAMIC_IMPORTERS or not node.args:
            return
        head = _literal_head(node.args[0].value)
        # La cadena vacía es «cualquier módulo»: sin prefijo fijo no hay nada
        # que diga que esta llamada alcanza a este repo.
        if head:
            self.prefixes.add(head)


def computed_module_prefixes(root: Path) -> set[str]:
    """Prefijos desde los que el repo arma nombres de módulo en ejecución.

    Es público a propósito: un `moves` vacío tiene dos causas —no hay un paquete
    raíz claro, o el repo se busca a sí mismo por nombre construido— y la dosis
    real de una condición se declara con datos, no deduciéndola de un contador
    que marca cero.
    """
    found: set[str] = set()
    for path in iter_transformable_files(root):
        try:
            module = cst.parse_module(read_source(path))
        except cst.ParserSyntaxError:
            continue
        collector = _CollectComputedPrefixes()
        module.visit(collector)
        found.update(collector.prefixes)
    return found


def plan_moves(root: Path) -> dict[str, str]:
    """Módulo original → módulo destino, todos colgando del paquete raíz.

    Determinista y por orden alfabético de ruta: la condición tiene que ser la
    misma en dos corridas distintas, o los resultados no se pueden comparar
    entre seeds.

    Los módulos que ya cuelgan del paquete también se renombran: si no, la mitad
    del árbol conserva sus nombres y B2 mide media dosis.
    """
    package = _package_root(root)
    if package is None:
        return {}

    # Un módulo al que el repo llega por un nombre que no existe hasta que corre
    # no se puede mover: no hay import que reescribir, porque no hay import.
    # python-stdnum despacha por código de país con `__import__('stdnum.%s' % cc)`
    # y su árbol de directorios *es* la tabla de búsqueda; medido, aplanarlo deja
    # 10 tests en rojo. Es el criterio de §4.3.3 —lo indecidible queda fuera— y
    # la política que dejó escrita la fase 1: se saca del diccionario lo que
    # rompa y se declara la dosis real.
    unreachable = computed_module_prefixes(root)
    scoped = _conftest_scopes(package)

    moves: dict[str, str] = {}
    index = 0
    for path in iter_transformable_files(root):
        if package not in path.parents:
            continue
        module = _module_name(path, root)
        # El paquete raíz es el punto de entrada y no se toca (§5.6).
        if module == package.name:
            continue
        if any(module.startswith(prefix) for prefix in unreachable):
            continue
        if any(directory in path.parents for directory in scoped):
            continue
        if _locates_itself(path):
            continue
        moves[module] = f"{package.name}.{_opaque_name(path, index)}"
        index += 1
    return moves


# Lo que un módulo usa para saber dónde está él mismo. `__name__` no entra: casi
# siempre es identidad —el nombre del logger— y excluir por él dejaría a B2 sin
# aplicar en casi todo el árbol.
SELF_LOCATING = ("__file__", "__package__")


def _locates_itself(path: Path) -> bool:
    """Si el módulo resuelve rutas contando desde su propia posición.

    pint carga su registro de unidades con
    `Path(__file__).parent.parent.parent / "default_en.txt"`: los tres saltos son
    la profundidad del fichero, o sea justo lo que B2 cambia. Movido a la raíz
    del paquete, la cuenta se sale del árbol. Medido sobre el clon: 2.024 tests
    pasan a 623 con 1.289 errores, todos de ahí. Ajustar los saltos sería
    adivinar —hay muchas formas de escribir esa cuenta y equivocarse rompe en
    silencio—, así que el módulo se queda donde está y se declara la dosis.
    """
    source = read_source(path)
    return any(marker in source for marker in SELF_LOCATING)


def _conftest_scopes(package: Path) -> set[Path]:
    """Directorios cuya posición es una declaración para el ejecutor de tests.

    Un `conftest.py` no es un módulo cualquiera: pytest lo busca por nombre
    exacto y su directorio decide qué tests ven sus fixtures. Moverlo cambia ese
    alcance y renombrarlo lo hace invisible, así que ni él ni lo que cuelga de
    su directorio se aplanan. pint tiene dos dentro del paquete.
    """
    return {path.parent for path in package.rglob("conftest.py")}


def _opaque_name(path: Path, index: int) -> str:
    """El nombre nuevo del módulo, opaco pero todavía colectable.

    pint tiene sus 35 ficheros de test dentro del paquete, y pytest los colecta
    por el prefijo del nombre: renombrarlos a `mN.py` no los esconde, los saca
    de la suite —cero tests, ningún fallo—. Se conserva solo lo que la
    herramienta lee, igual que A2 no renombra las funciones de test y A4 no
    borra los comentarios que lee una herramienta. Lo que decía de qué trata el
    fichero se pierde igual, que es la dosis de B2.
    """
    if path.name.startswith("test_"):
        return f"test_m{index}"
    if path.stem.endswith("_test"):
        return f"m{index}_test"
    return f"m{index}"


def _dotted(node: cst.BaseExpression) -> str:
    """La forma con puntos de un `a.b.c`, o vacío si no es un nombre con puntos."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr.value}" if base else ""
    return ""


def _containing_package(path: Path, root: Path) -> str:
    """El paquete al que pertenece el fichero, en forma de módulo con puntos.

    Es lo que hace falta para resolver un import relativo: `from ..util import
    clean` no significa nada sin saber desde dónde se cuenta.
    """
    parts = path.relative_to(root).with_suffix("").parts
    # El `__init__` no está *en* su paquete: es su paquete.
    return ".".join(parts[:-1])


class _RewriteImports(cst.CSTTransformer):
    """Reescribe los imports para que apunten a donde va a estar cada módulo.

    Se hace antes de mover nada: el diccionario de destinos ya está decidido, y
    reescribir primero evita tener que reconstruirlo leyendo un árbol a medio
    mover.

    Los alias locales se conservan (`from pkg.es import nif` sale como
    `from pkg import m3 as nif`) por dos razones. La primera es que sin ellos el
    repo no arranca: el nombre corto está usado en el cuerpo del fichero. La
    segunda es que ahí no está la dosis de B2. Lo que B2 destruye es la señal de
    **qué fichero abrir** —el árbol ya no dice dónde está nada—; el nombre con
    el que un fichero ya abierto llama a lo que importa es materia de A2, y las
    dos condiciones se miden por separado y se pueden cruzar.
    """

    def __init__(
        self,
        moves: dict[str, str],
        package: str,
        current: str,
        stationary: frozenset[str] = frozenset(),
    ) -> None:
        self.moves = moves
        self.package = package
        # Módulos del paquete que NO se mueven: hay que poder distinguirlos de
        # un nombre cualquiera, porque se siguen alcanzando por su ruta de antes.
        self.stationary = stationary
        # El paquete desde el que se cuentan los puntos de un import relativo.
        self.current = current
        # Los imports relativos solo se resuelven dentro del paquete: un
        # `from . import x` en un directorio de tests de la raíz sigue siendo
        # válido después, porque ese fichero no se mueve.
        self.inside = current == package or current.startswith(f"{package}.")

    # Los hijos de un import no se visitan: la ruta de módulo se reescribe
    # entera aquí, con el contexto del import, y dejar que `leave_Attribute`
    # la tocara antes haría que la búsqueda en el diccionario ya no encontrara
    # nada.
    def visit_Import(self, node: cst.Import) -> bool:
        return False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        return False

    def leave_SimpleString(
        self, original: cst.SimpleString, updated: cst.SimpleString
    ) -> cst.SimpleString:
        """Las dos cosas que puede haber dentro de una cadena y hay que seguir.

        Una ruta de módulo escrita a mano —ver `_module_reference`—, y los
        ejemplos de doctest. Un doctest no es documentación: es suite.
        `stdnum/__init__.py` importa `stdnum.isbn` desde un ejemplo de su propia
        docstring, y el paquete raíz no se mueve pero el módulo que importa sí.
        Dejar el ejemplo atrás convierte un test en un fallo, y la condición se
        leería como un repositorio roto.
        """
        rewritten_path = self._module_reference(updated.raw_value)
        if rewritten_path is not None:
            return updated.with_changes(
                value=f"{updated.prefix}{updated.quote}{rewritten_path}{updated.quote}"
            )
        if DOCTEST_PROMPT not in updated.value:
            return updated
        # Se opera sobre el literal entero, comillas incluidas: los prompts van
        # por dentro, así que los escapes quedan intactos.
        rewritten = rewrite_examples(updated.value, self.rewrite_snippet)
        return updated if rewritten == updated.value else updated.with_changes(value=rewritten)

    def _module_reference(self, text: str) -> str | None:
        """La misma cadena con la ruta de módulo actualizada, o None si no lo es.

        Cubre dos formas de la misma cosa. La cadena que es exactamente un
        módulo: `stdnum/gs1_128.py` guarda las suyas en un diccionario y se las
        pasa a `__import__`. Y la que es un módulo más un atributo suyo: pint
        parchea con `patch("pint.compat.upcast_type_names")`, que `mock` resuelve
        importando `pint.compat`. Las dos resuelven estáticamente, así que
        §4.3.3 no las excluye —excluye lo indecidible— y es el criterio con el
        que A2 ya sigue las cadenas de `__all__`.

        Se exige que la cadena entera sea una cadena de identificadores con
        puntos, y se busca el prefijo de módulo **más largo**: una frase que
        menciona el módulo es documentación, y reescribirla sería B3 colándose
        dentro de B2.
        """
        parts = text.split(".")
        if len(parts) < 2 or not all(part.isidentifier() for part in parts):
            return None
        for cut in range(len(parts), 1, -1):
            target = self.moves.get(".".join(parts[:cut]))
            if target is not None:
                return ".".join([target, *parts[cut:]])
        return None

    def rewrite_snippet(self, code: str) -> str | None:
        """El mismo reescrito sobre un trozo suelto, o None si no cuela.

        LibCST valida al construir el nodo, y esa excepción no es de parseo: sin
        capturarla, un ejemplo raro dejaría el fichero a medio transformar.
        """
        try:
            module = cst.parse_module(code)
            return module.visit(
                _RewriteImports(self.moves, self.package, self.current, self.stationary)
            ).code
        except (cst.ParserSyntaxError, cst.CSTValidationError):
            return None

    def leave_Attribute(
        self, original: cst.Attribute, updated: cst.Attribute
    ) -> cst.BaseExpression:
        """`stdnum.bic` usado como expresión, no dentro de un import.

        Es lo que deja un `import stdnum.bic` sin alias: lo que queda ligado es
        `stdnum`, y el módulo se nombra después por su ruta entera. Reescribir
        solo la sentencia de import dejaría todos esos usos apuntando a un
        módulo que ya no existe. Se resuelve de dentro afuera, así que en
        `pkg.es.nif.validate` la cadena que se sustituye es `pkg.es.nif` y el
        `.validate` de fuera se queda donde está.

        La consulta se hace sobre el nodo **original** porque LibCST resuelve de
        dentro afuera y el hijo ya viene sustituido: en `pkg.es.nif`, el `pkg.es`
        de dentro también es un módulo que se movió, y preguntando por el nodo
        ya reescrito la cadena entera dejaría de encontrarse. Preguntando por el
        original gana siempre la coincidencia más larga, que es la correcta: el
        módulo es el fichero, no el directorio que lo contenía.
        """
        target = self.moves.get(_dotted(original))
        return cst.parse_expression(target) if target else updated

    def leave_Import(self, original: cst.Import, updated: cst.Import) -> cst.Import:
        names = [
            alias.with_changes(name=cst.parse_expression(self.moves[dotted]))
            if (dotted := _dotted(alias.name)) in self.moves
            else alias
            for alias in updated.names
        ]
        return updated.with_changes(names=names)

    def _absolute_base(self, node: cst.ImportFrom) -> str | None:
        """De dónde importa esta sentencia, en absoluto, o None si no se sabe."""
        tail = _dotted(node.module) if node.module is not None else ""
        if not node.relative:
            return tail or None
        if not self.inside:
            return None
        parts = self.current.split(".")
        # Un punto es el paquete propio; cada punto de más sube uno.
        kept = len(parts) - (len(node.relative) - 1)
        if kept < 1:
            return None
        base = parts[:kept]
        return ".".join([*base, *tail.split(".")]) if tail else ".".join(base)

    def leave_ImportFrom(self, original: cst.ImportFrom, updated: cst.ImportFrom):
        base = self._absolute_base(updated)
        if base is None:
            return updated
        # Fuera del paquete no hay nada que reescribir, y un import relativo de
        # un fichero que no se mueve sigue siendo correcto tal cual.
        if not (base == self.package or base.startswith(f"{self.package}.")):
            return updated

        if isinstance(updated.names, cst.ImportStar):
            return _absolute_import_from(updated, self.moves.get(base, base))

        # Cada nombre de la lista puede tener que venir de un sitio distinto.
        # Un submódulo que se movió cuelga ahora del paquete raíz. Uno que NO se
        # movió sigue colgando de su directorio, así que hay que seguir yendo a
        # buscarlo por la ruta de antes: rebasarlo al destino del padre lo manda
        # a buscar un submódulo dentro de un fichero plano. Y un nombre normal
        # —algo definido en el `__init__`— viene de donde fuera ese `__init__`.
        moved, stationary, kept = [], [], []
        for alias in updated.names:
            full = f"{base}.{alias.name.value}"
            target = self.moves.get(full)
            if target is not None:
                moved.append(
                    alias.with_changes(
                        name=cst.Name(target.split(".")[-1]),
                        asname=alias.asname or cst.AsName(name=cst.Name(alias.name.value)),
                        comma=cst.MaybeSentinel.DEFAULT,
                    )
                )
            elif full in self.stationary:
                stationary.append(alias)
            else:
                kept.append(alias)

        # Nada que repartir: solo cambia de dónde viene. Se dejan los nombres
        # exactamente como estaban, comas y saltos de línea incluidos. Rehacer
        # la lista aplastaría un `from x import (\n  a,\n  b)` en una sola
        # línea, que es formato —o sea A3— colándose dentro de B2, y dentro de
        # un doctest cambiar el número de líneas invalida el ejemplo entero.
        if not moved and not stationary:
            return _absolute_import_from(updated, self.moves.get(base, base))

        statements = []
        if moved:
            statements.append(_absolute_import_from(updated, self.package, moved))
        if stationary:
            statements.append(_absolute_import_from(updated, base, stationary))
        if kept:
            statements.append(_absolute_import_from(updated, self.moves.get(base, base), kept))
        if len(statements) == 1:
            return statements[0]
        return cst.FlattenSentinel(statements)


def _absolute_import_from(node: cst.ImportFrom, base: str, names=None) -> cst.ImportFrom:
    """El mismo import, en forma absoluta y apuntando a `base`.

    Siempre absoluto: al aplanar, todos los ficheros pasan a colgar del paquete
    raíz, así que cualquier import relativo de más de un punto se saldría del
    paquete. El nombre del paquete raíz es lo único que sigue siendo válido
    (§5.6), y por eso es la referencia desde la que se reescribe todo.
    """
    changes = {"module": cst.parse_expression(base), "relative": []}
    if names is not None:
        # La última no lleva coma: `with_changes` no la quita sola.
        changes["names"] = [
            *[alias.with_changes(comma=cst.MaybeSentinel.DEFAULT) for alias in names[:-1]],
            names[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT),
        ]
    return node.with_changes(**changes)


def stationary_modules(root: Path, moves: dict[str, str]) -> frozenset[str]:
    """Los módulos del repo que B2 deja donde están.

    Su ruta sigue siendo la buena, así que hay que poder reconocerlos: son la
    diferencia entre `from pkg.m2 import loader` —que busca un submódulo dentro
    de un fichero plano— y `from pkg.deep.inner import loader`, que es donde
    `loader` sigue estando.
    """
    package = _package_root(root)
    if package is None:
        return frozenset()
    return frozenset(
        _module_name(path, root)
        for path in iter_transformable_files(root)
        if package in path.parents and _module_name(path, root) not in moves
    )


def _rewrite_file(
    path: Path, root: Path, moves: dict[str, str], package: str, stationary: frozenset[str]
) -> bool:
    source = read_source(path)
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return False
    rewriter = _RewriteImports(moves, package, _containing_package(path, root), stationary)
    transformed = module.visit(rewriter).code
    if transformed == source:
        return False
    path.write_text(transformed, encoding="utf-8")
    return True


def _module_path(root: Path, module: str) -> Path:
    return root / Path(*module.split(".")).with_suffix(".py")


# Donde pytest lee su configuración. Son los únicos ficheros que pueden nombrar
# una ruta y con ella cambiar lo que la suite colecta.
PYTEST_CONFIG_FILES = ("setup.cfg", "pytest.ini", "tox.ini", "pyproject.toml")


def _rewrite_configured_paths(root: Path, moves: dict[str, str]) -> int:
    """Las rutas de fichero que la configuración de la suite nombra.

    Un import roto se ve: falla un test. Una ruta rota en la configuración no,
    y es peor. python-stdnum ignora `stdnum/iso9362.py` por ruta —es un módulo
    que se sustituye a sí mismo en `sys.modules`—; al aplanar, esa ruta deja de
    existir, el `--ignore` no tapa nada, pytest lo colecta y la corrida entera
    muere en la colecta. Medido: 413 tests pasan a 0 sin que falle ninguno, y la
    condición se leería como un repositorio que el agente destrozó.
    """
    replacements = {
        "/".join(original.split(".")) + ".py": "/".join(target.split(".")) + ".py"
        for original, target in moves.items()
    }
    changed = 0
    for name in PYTEST_CONFIG_FILES:
        path = root / name
        if not path.exists():
            continue
        source = read_source(path)
        transformed = source
        for old, new in replacements.items():
            transformed = transformed.replace(old, new)
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return changed


def apply(root: Path) -> TransformResult:
    moves = plan_moves(root)
    if not moves:
        return TransformResult()

    package = _package_root(root)
    assert package is not None  # `plan_moves` ya devolvió vacío si no lo había
    changed = 0
    # Los ficheros de doctest no son .py y no los recoge `iter_transformable_files`,
    # pero la suite del repo los ejecuta: en python-stdnum son 234 líneas de
    # ejemplo importando por ruta de módulo, o sea 234 fallos si se quedan atrás.
    changed += _rewrite_configured_paths(root, moves)

    stationary = stationary_modules(root, moves)
    rewriter = _RewriteImports(moves, package.name, "", stationary)
    for path in doctest_files(root):
        source = read_source(path)
        # El fichero entero es texto de doctest: no hay módulo que parsear.
        transformed = rewrite_examples(source, rewriter.rewrite_snippet)
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1

    # Alcance repo-wide, tests del repo incluidos (§4.3.1): un import sin
    # reescribir en la suite se lee como suite en rojo, o sea como fracaso.
    for path in iter_transformable_files(root):
        if _rewrite_file(path, root, moves, package.name, stationary):
            changed += 1

    for original, target in moves.items():
        source_path = _module_path(root, original)
        if not source_path.exists():
            # Un paquete es su `__init__.py`, no un fichero con su nombre.
            source_path = root / Path(*original.split(".")) / "__init__.py"
        destination = _module_path(root, target)
        if source_path != destination and source_path.exists():
            shutil.move(str(source_path), str(destination))
            changed += 1

    package = _package_root(root)
    if package is not None:
        # Solo los que quedan vacíos: un directorio con ficheros de datos dentro
        # sigue haciendo falta, porque quien los abre lo hace por ruta.
        for directory in sorted(package.rglob("*"), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()

    return TransformResult(files_changed=changed, moves=moves)
