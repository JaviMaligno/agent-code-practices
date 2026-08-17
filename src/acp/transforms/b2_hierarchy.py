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

    moves: dict[str, str] = {}
    index = 0
    for path in iter_transformable_files(root):
        if package not in path.parents:
            continue
        module = _module_name(path, root)
        # El paquete raíz es el punto de entrada y no se toca (§5.6).
        if module == package.name:
            continue
        moves[module] = f"{package.name}.m{index}"
        index += 1
    return moves


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

    def __init__(self, moves: dict[str, str], package: str, current: str) -> None:
        self.moves = moves
        self.package = package
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
        """Los ejemplos de doctest que hay dentro de una docstring.

        Un doctest no es documentación: es suite. `stdnum/__init__.py` importa
        `stdnum.isbn` desde un ejemplo de su propia docstring, y el paquete raíz
        no se mueve pero el módulo que importa sí. Dejar el ejemplo atrás
        convierte un test en un fallo, y la condición se leería como un repo
        roto.
        """
        # Una cadena que es exactamente el nombre de un módulo del repo es una
        # ruta de import, no prosa: `stdnum/gs1_128.py` guarda las suyas en un
        # diccionario y las pasa a `__import__`. Resuelve estáticamente, así que
        # §4.3.3 no la excluye —excluye lo indecidible— y es el mismo criterio
        # con el que A2 sigue las cadenas de `__all__`. Se exige coincidencia
        # exacta: una frase que menciona el módulo es documentación, y
        # reescribirla sería B3 colándose dentro de B2.
        target = self.moves.get(updated.raw_value)
        if target is not None:
            return updated.with_changes(
                value=f"{updated.prefix}{updated.quote}{target}{updated.quote}"
            )
        if DOCTEST_PROMPT not in updated.value:
            return updated
        # Se opera sobre el literal entero, comillas incluidas: los prompts van
        # por dentro, así que los escapes quedan intactos.
        rewritten = rewrite_examples(updated.value, self.rewrite_snippet)
        return updated if rewritten == updated.value else updated.with_changes(value=rewritten)

    def rewrite_snippet(self, code: str) -> str | None:
        """El mismo reescrito sobre un trozo suelto, o None si no cuela.

        LibCST valida al construir el nodo, y esa excepción no es de parseo: sin
        capturarla, un ejemplo raro dejaría el fichero a medio transformar.
        """
        try:
            module = cst.parse_module(code)
            return module.visit(_RewriteImports(self.moves, self.package, self.current)).code
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

        # Un submódulo importado por su nombre deja de colgar de donde colgaba:
        # después de aplanar cuelga del paquete raíz, así que no puede venir del
        # mismo sitio que un nombre definido en el `__init__`.
        moved, kept = [], []
        for alias in updated.names:
            target = self.moves.get(f"{base}.{alias.name.value}")
            if target is None:
                kept.append(alias)
                continue
            moved.append(
                alias.with_changes(
                    name=cst.Name(target.split(".")[-1]),
                    asname=alias.asname or cst.AsName(name=cst.Name(alias.name.value)),
                    comma=cst.MaybeSentinel.DEFAULT,
                )
            )

        # Nada que reagrupar: solo cambia de dónde viene. Se dejan los nombres
        # exactamente como estaban, comas y saltos de línea incluidos. Rehacer
        # la lista aplastaría un `from x import (\n  a,\n  b)` en una sola
        # línea, que es formato —o sea A3— colándose dentro de B2, y dentro de
        # un doctest cambiar el número de líneas invalida el ejemplo entero.
        if not moved:
            return _absolute_import_from(updated, self.moves.get(base, base))

        statements = [_absolute_import_from(updated, self.package, moved)]
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


def _rewrite_file(path: Path, root: Path, moves: dict[str, str], package: str) -> bool:
    source = read_source(path)
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return False
    rewriter = _RewriteImports(moves, package, _containing_package(path, root))
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

    rewriter = _RewriteImports(moves, package.name, "")
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
        if _rewrite_file(path, root, moves, package.name):
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
