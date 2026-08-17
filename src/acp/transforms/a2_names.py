from __future__ import annotations

import ast
import builtins
from pathlib import Path

import libcst as cst

from acp.metrics.size import iter_source_files, parse_source, read_source
from acp.transforms.base import TransformResult, iter_transformable_files

# La regla de qué trozo de un texto es un ejemplo de doctest, y qué ficheros los
# ejecutan, es la misma para A2 y para B2 y por eso vive en su propio módulo.
from acp.transforms.doctests import (
    DOCTEST_PROMPT,
    doctest_examples,
    doctest_files,
)

# Las definiciones de un módulo que use cualquiera de estas salen del
# diccionario: se alcanzan por cadena desde su propio fichero y renombrarlas
# rompe el programa (§4.3.3). Ojo: esto solo cubre lo que el módulo dinámico
# define; lo que ese getattr ALCANZA en otros ficheros lo cubre
# `_names_written_as_strings`.
DYNAMIC_ACCESS = {"getattr", "setattr", "hasattr", "globals", "locals", "vars", "eval", "exec"}


OPAQUE_PREFIXES = ("f", "K", "C")


def _opaque(name: str, index: int) -> str:
    if name.isupper():
        return f"C{index}"
    if name[:1].isupper():
        return f"K{index}"
    return f"f{index}"


def _identifiers(root: Path) -> set[str]:
    """Todo identificador que ya aparece escrito en el repo.

    Los nombres opacos se generan por índice, y `f0` o `C3` no son nombres
    imposibles —en código científico salen solos—. Si el generado ya existe, el
    símbolo renombrado y el que estaba se confunden bajo la misma etiqueta: el
    programa cambia de comportamiento sin dar un error, que es la peor forma de
    romperse.
    """
    found: set[str] = set()
    for path in iter_transformable_files(root):
        tree = parse_source(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                found.add(node.id)
            elif isinstance(node, ast.arg):
                found.add(node.arg)
            elif isinstance(node, ast.Attribute):
                found.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.add(node.name)
            elif isinstance(node, ast.keyword) and node.arg:
                found.add(node.arg)
            elif isinstance(node, ast.alias):
                found.add(node.asname or node.name.split(".")[0])
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                found.update(node.names)
    return found


def _opaque_names(names: list[str], taken: set[str]) -> dict[str, str]:
    """Diccionario de renombrado saltándose los índices que ya están ocupados.

    Se salta el índice entero, no solo el prefijo: es más barato de explicar en
    el artículo que un contador por prefijo, y lo único que cuesta es que la
    numeración tenga huecos.
    """
    renames: dict[str, str] = {}
    index = 0
    for name in names:
        while any(f"{prefix}{index}" in taken for prefix in OPAQUE_PREFIXES):
            index += 1
        renames[name] = _opaque(name, index)
        index += 1
    return renames


def _uses_dynamic_access(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in DYNAMIC_ACCESS
        for node in ast.walk(tree)
    )


def repo_modules(root: Path) -> set[str]:
    """Rutas con punto de los módulos y paquetes que define el propio repo.

    Es lo que permite distinguir `billing.apply_tax(...)` —un símbolo del repo
    llegado por su módulo, que hay que renombrar— de `','.join(...)`, que se
    llama igual por casualidad.
    """
    modules: set[str] = set()
    for path in iter_transformable_files(root):
        parts = path.relative_to(root).with_suffix("").parts
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        for cut in range(1, len(parts) + 1):
            modules.add(".".join(parts[:cut]))
    return modules


def _import_base(node: ast.ImportFrom, package: str) -> str:
    """Módulo del que cuelga un `from ... import`, resolviendo los puntos."""
    if not node.level:
        return node.module or ""
    parts = package.split(".") if package else []
    base = ".".join(parts[: len(parts) - (node.level - 1)])
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base


def module_aliases(tree: ast.Module, path: Path, root: Path, modules: set[str]) -> dict[str, str]:
    """Nombres locales de un fichero que apuntan a un módulo del propio repo."""
    package = ".".join(path.relative_to(root).parts[:-1])
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    if alias.name in modules:
                        aliases[alias.asname] = alias.name
                else:
                    # `import pkg.billing` liga solo `pkg`; el resto de la
                    # cadena se resuelve al mirar el atributo.
                    head = alias.name.split(".")[0]
                    if head in modules:
                        aliases[head] = head
        elif isinstance(node, ast.ImportFrom):
            base = _import_base(node, package)
            for alias in node.names:
                candidate = f"{base}.{alias.name}" if base else alias.name
                if candidate in modules:
                    aliases[alias.asname or alias.name] = candidate
    return aliases


def _parameter_names(root: Path) -> set[str]:
    """Todo lo que en algún sitio del repo es un parámetro.

    Se mira el árbol entero, tests incluidos: la llamada que pasa el argumento
    por palabra clave puede vivir en la suite.
    """
    names: set[str] = set()
    for path in iter_transformable_files(root):
        tree = parse_source(path)
        if tree is None:
            continue
        names.update(node.arg for node in ast.walk(tree) if isinstance(node, ast.arg))
    return names


def _all_literals(tree: ast.Module) -> set[int]:
    """Identidad de las cadenas que son elementos de un `__all__`.

    Es la única cadena del repo que sí se sigue —decide qué trae un `import *`
    y se resuelve estáticamente—, así que no puede contar como prueba de que el
    símbolo se alcanza por cadena: lo excluiría a sí mismo del diccionario y
    `__all__` dejaría de renombrarse nunca.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            found.update(
                id(child)
                for child in ast.walk(node)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            )
    return found


def _names_written_as_strings(root: Path) -> set[str]:
    """Nombres que en algún sitio del repo aparecen escritos como cadena.

    §4.3.3 excluye lo alcanzable por cadenas, y una cadena que dice exactamente
    el nombre de un símbolo del repo es la forma en que se alcanza: el registro
    de holidays guarda `("Spain", "ES", "ESP")` en una tabla y resuelve la clase
    con `getattr(módulo, entrada)`, y la clase vive en otro fichero que no usa
    getattr. Excluir el módulo que hace el getattr no la salva; el nombre en la
    tabla sí lo delata.

    Es deliberadamente indiscriminado —no se mira quién usa la cadena ni para
    qué—, porque atar la cadena a su uso exige ejecutar el programa. El precio
    se paga en dosis: se renombra de menos. Un repo roto, en cambio, se lee
    igual que un agente que fracasa (§11).
    """
    found: set[str] = set()
    for path in iter_transformable_files(root):
        tree = parse_source(path)
        if tree is None:
            continue
        public = _all_literals(tree)
        found.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in public
        )
    return found


# Nombres locales por los que una clase se alcanza a sí misma. `cls.__name__`
# dentro de un classmethod y `self.__class__.__name__` dentro de un método son
# la misma cosa: el nombre de la clase convertido en dato.
SELF_REFERENCES = {"cls", "klass", "__class__"}


def _classes_that_publish_their_own_name(root: Path) -> set[str]:
    """Clases cuyo nombre es un dato del programa, y sus subclases.

    sqlglot tiene las dos formas. Una es la metaclase que registra
    `cls._classes[clsname.lower()]`. La otra no deja ni esa pista: `Func`
    devuelve `camel_to_snake_case(cls.__name__)` como nombre SQL público, así que
    `class PosexplodeOuter` publica la función `posexplode_outer` sin que la
    cadena aparezca escrita en ninguna parte. Lo hereda cada subclase, y por eso
    la contaminación se propaga por el grafo de herencia del repo.

    Se resuelve por nombre desnudo de la base —`Func` y `exp.Func` son la misma—
    porque es lo que el resto del módulo ya puede resolver estáticamente. De
    equivocarse, se equivoca renombrando de menos.
    """
    bases: dict[str, set[str]] = {}
    metaclasses: dict[str, str] = {}
    publishing: set[str] = set()
    for path in iter_transformable_files(root):
        tree = parse_source(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            entry = bases.setdefault(node.name, set())
            entry.update(
                dotted.rsplit(".", 1)[-1]
                for dotted in (_dotted_ast(base) for base in node.bases)
                if dotted
            )
            for keyword in node.keywords:
                if keyword.arg == "metaclass":
                    dotted = _dotted_ast(keyword.value)
                    if dotted:
                        metaclasses[node.name] = dotted.rsplit(".", 1)[-1]
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute) and child.attr == "__name__":
                    base = _dotted_ast(child.value) or ""
                    if base.rsplit(".", 1)[-1] in SELF_REFERENCES:
                        publishing.add(node.name)
    # Una metaclase recibe el nombre de la clase como argumento de `__new__`: es
    # el patrón del registro de dialectos, y lo que hace con él no se puede
    # saber sin ejecutarlo. Quien la declara queda contaminado.
    metaclass_classes = {name for name, entries in bases.items() if "type" in entries}
    publishing.update(
        name for name, meta in metaclasses.items() if meta in metaclass_classes
    )

    tainted = set(publishing)
    growing = True
    while growing:
        growing = False
        for name, entries in bases.items():
            if name not in tainted and entries & tainted:
                tainted.add(name)
                growing = True
    return tainted


def _names_bound_by_external_imports(root: Path, modules: set[str]) -> set[str]:
    """Nombres que en algún fichero los trae un import de fuera del repo.

    `from json import dumps` liga `dumps` a algo que json exporta y este repo no
    define, por mucho que otro fichero tenga su propio `def dumps`. Como el
    diccionario va por nombre desnudo, renombrarlo escribe `from json import f0`
    y el repo deja de importar: es el caso real del setup.py de sqlglot con
    `from setuptools.command.build_ext import build_ext`.

    Igual con `import collections` sin alias: liga `collections` al módulo de la
    biblioteca estándar. Con alias no hace falta —el nombre ligado es el alias, y
    ese sí se mueve entero con sus usos—, y la ruta del módulo la protege
    `_Rename`.
    """
    found: set[str] = set()
    for path in iter_transformable_files(root):
        tree = parse_source(path)
        if tree is None:
            continue
        package = ".".join(path.relative_to(root).parts[:-1])
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    head = alias.name.split(".")[0]
                    if alias.asname is None and head not in modules:
                        found.add(head)
            elif isinstance(node, ast.ImportFrom):
                # Un import relativo siempre es del propio repo.
                if node.level or _import_base(node, package) in modules:
                    continue
                found.update(alias.name for alias in node.names if alias.name != "*")
    return found


def _dotted_ast(node: ast.expr) -> str | None:
    """La cadena `a.b.c` de una cadena de atributos, sobre el árbol de `ast`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted_ast(node.value)
        return f"{head}.{node.attr}" if head else None
    return None


def _attribute_names_on_unresolved_bases(root: Path, modules: set[str]) -> set[str]:
    """Nombres que en algún sitio se leen como atributo de algo sin resolver.

    `billing.apply_tax(...)` se resuelve —`billing` es un módulo del repo— y por
    eso se renombra. `mod.validate(...)` no: `mod` puede ser cualquier cosa, y en
    python-stdnum es literalmente un módulo elegido en tiempo de ejecución
    (`__import__('stdnum.%s' % cc)` y `getattr(mod, 'validate')`). La definición
    de `validate` está a la vista, pero la llamada no se puede mover con ella
    porque no hay forma estática de saber a qué fichero apunta: entra en lo
    indecidible de §4.3.3 y el símbolo entero sale del diccionario.

    Solo cuentan las bases que podrían tener dentro un módulo, es decir un
    nombre o una cadena de atributos. `','.join(...)` y `Klass().info()` no
    entran: un literal y el resultado de una llamada nunca son el módulo donde
    vive la definición, y excluirlos por ahí dejaría fuera del diccionario media
    biblioteca estándar —`join`, `split`, `read`— por pura coincidencia de
    nombre.
    """
    found: set[str] = set()
    for path in iter_transformable_files(root):
        tree = parse_source(path)
        if tree is None:
            continue
        aliases = module_aliases(tree, path, root, modules)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            dotted = _dotted_ast(node.value)
            if dotted is not None and not _resolves_to_module(dotted, aliases, modules):
                found.add(node.attr)
    return found


def _resolves_to_module(dotted: str | None, aliases: dict[str, str], modules: set[str]) -> bool:
    if dotted is None:
        return False
    head, _, rest = dotted.partition(".")
    target = aliases.get(head)
    if target is None:
        return False
    return (f"{target}.{rest}" if rest else target) in modules


def collect_renames(root: Path) -> dict[str, str]:
    """Diccionario de renombrado de los símbolos que define el propio repo.

    Solo entran definiciones de nivel de módulo —funciones, clases y constantes—
    porque son las que se pueden resolver estáticamente. Los métodos se dejan:
    una llamada `obj.metodo()` no se puede atribuir a una clase sin inferencia
    de tipos, y equivocarse rompe el repo en silencio.
    """
    names: set[str] = set()
    classes: set[str] = set()
    # El diccionario sale solo del código fuente: incluir los ficheros de test
    # metería `test_algo` en el renombrado, y pytest colecta por nombre — la
    # suite dejaría de encontrar sus propios tests. Aplicarlo, en cambio, se
    # aplica a todo (§4.3.1).
    for path in iter_source_files(root):
        tree = parse_source(path)
        if tree is None or _uses_dynamic_access(tree):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("__"):
                    names.add(node.name)
                    if isinstance(node, ast.ClassDef):
                        classes.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("__"):
                        names.add(target.id)
    # El nombre de un módulo o paquete no se renombra nunca —los ficheros siguen
    # donde estaban—, así que un símbolo que se llame igual que un módulo del
    # repo tendría dos significados bajo la misma entrada del diccionario y
    # rompería los imports. Sale fuera.
    modules = repo_modules(root)
    basenames = {module.rsplit(".", 1)[-1] for module in modules}
    names -= basenames
    # Un nombre que además es parámetro en algún sitio significa dos cosas: el
    # símbolo del módulo y una variable local. Renombrar las dos rompe las
    # llamadas por palabra clave; renombrar una sola, el cuerpo de la función.
    names -= _parameter_names(root)
    # Y un `def format(...)` propio no convierte en suyas las llamadas al
    # builtin `format` del resto del repo: renombrarlas daría NameError.
    names -= set(dir(builtins))
    # Lo que en algún sitio se lee como atributo de algo que no resuelve puede
    # ser este mismo símbolo llegando por una ruta que no se ve.
    names -= _attribute_names_on_unresolved_bases(root, modules)
    # Y lo que en algún fichero llega por un import de fuera no es de este repo
    # aunque se llame igual que algo de aquí: renombrarlo pide a la librería
    # ajena un nombre que no tiene.
    names -= _names_bound_by_external_imports(root, modules)
    # Y lo que en algún sitio está escrito como cadena se alcanza por cadena:
    # el renombrado movería la definición y dejaría la cadena atrás.
    written = _names_written_as_strings(root)
    names -= written
    # El nombre de una clase, además, puede ser la clave de un registro sin que
    # nadie lo escriba tal cual: en sqlglot una metaclase hace
    # `cls._classes[clsname.lower()] = klass`, así que `class Postgres` publica
    # la clave 'postgres' y no hay getattr ni cadena idéntica que lo delate. Una
    # clase cuyo nombre aparece escrito con otra caja es exactamente ese caso, y
    # renombrarla cambia una API pública consumida desde fuera (§4.3.3).
    lowered = {literal.lower() for literal in written}
    names -= {name for name in classes if name.lower() in lowered}
    # Y la misma clave puede no estar escrita en ninguna cadena: basta con que
    # una clase base convierta `cls.__name__` en el nombre público.
    names -= _classes_that_publish_their_own_name(root)
    # El nombre generado tiene que ser nuevo de verdad: si ya existe en el repo,
    # el renombrado no oculta el nombre, lo funde con otro.
    return _opaque_names(sorted(names), _identifiers(root))


def rename_in_doctests(
    text: str, renames: dict[str, str], aliases: dict[str, str], modules: set[str],
    path: Path, root: Path,
) -> str:
    """Renombra dentro de los ejemplos de un doctest, y solo ahí.

    Un doctest no es documentación: es suite. python-stdnum corre la suya con
    `--doctest-modules`, y medido, dejar los ejemplos atrás convierte sus 413
    tests en 413 fallos. La línea de ejemplo es código y resuelve estáticamente,
    igual que `__all__`; la prosa y la salida esperada de alrededor no se tocan,
    porque reescribirlas sería documentación (A4/B3) colándose dentro de A2.
    """
    lines = text.split("\n")
    examples = doctest_examples(lines)
    if not examples:
        return text

    # Los alias se acumulan sobre el texto entero antes de tocar nada: el
    # `>>> from pkg import billing` vive en un ejemplo y el `billing.total(...)`
    # que lo usa, en otro. Resolviendo ejemplo a ejemplo, el segundo no sabría
    # que `billing` es un módulo del repo.
    combined = dict(aliases)
    for block in examples:
        try:
            tree = ast.parse("\n".join(item[2] for item in block))
        except SyntaxError:
            continue
        combined.update(module_aliases(tree, path, root, modules))

    changed = False
    for block in examples:
        renamed = _renamed_snippet(
            "\n".join(item[2] for item in block), renames, combined, modules, path, root
        )
        if renamed is None:
            continue
        new_lines = renamed.split("\n")
        # Renombrar no añade ni quita líneas. Si las cuentas no cuadran, algo se
        # entendió mal: se deja el ejemplo como estaba antes que a medias.
        if len(new_lines) != len(block):
            continue
        for (index, prefix, _), new_code in zip(block, new_lines):
            lines[index] = prefix + new_code
        changed = True
    return "\n".join(lines) if changed else text


def _renamed_snippet(
    code: str, renames: dict[str, str], aliases: dict[str, str], modules: set[str],
    path: Path, root: Path,
) -> str | None:
    """El mismo renombrado sobre un trozo suelto de código, o None si no cuela."""
    try:
        module = cst.parse_module(code)
        # LibCST valida al construir el nodo, y esa excepción no es de parseo:
        # sin capturarla aquí, un ejemplo raro dejaría el árbol a medio
        # transformar, que es la peor forma posible de fallar.
        return module.visit(_Rename(renames, aliases, modules, path, root)).code
    except (cst.ParserSyntaxError, cst.CSTValidationError):
        return None


def _dotted(node: cst.BaseExpression) -> str | None:
    """La cadena `a.b.c` de una cadena de atributos, o None si no lo es."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr.value}" if head else None
    return None


def _restored_import_names(before, after):
    """Devuelve a cada alias de un import el nombre que traía, no el alias."""
    return [
        alias.with_changes(name=original.name) for original, alias in zip(before, after)
    ]


class _Rename(cst.CSTTransformer):
    def __init__(
        self, renames: dict[str, str], aliases: dict[str, str], modules: set[str],
        path: Path, root: Path,
    ) -> None:
        self.renames = renames
        self.aliases = aliases
        self.modules = modules
        self.path = path
        self.root = root

    def leave_SimpleString(self, original: cst.SimpleString, updated: cst.SimpleString):
        # Se opera sobre el literal entero, comillas incluidas: los prompts van
        # por dentro, así que los escapes quedan intactos.
        if DOCTEST_PROMPT not in updated.value:
            return updated
        rewritten = rename_in_doctests(
            updated.value, self.renames, self.aliases, self.modules, self.path, self.root
        )
        return updated if rewritten == updated.value else updated.with_changes(value=rewritten)

    def leave_Name(self, original: cst.Name, updated: cst.Name) -> cst.Name:
        new = self.renames.get(updated.value)
        return updated.with_changes(value=new) if new else updated

    def leave_Import(self, original: cst.Import, updated: cst.Import) -> cst.Import:
        # Lo que hay en un `import` es una ruta de módulo, y los ficheros siguen
        # donde estaban: nada de esa ruta se renombra nunca. El alias, si lo hay,
        # sí es un nombre local y `leave_Name` lo mueve con todos sus usos.
        return updated.with_changes(names=_restored_import_names(original.names, updated.names))

    def leave_ImportFrom(self, original: cst.ImportFrom, updated: cst.ImportFrom):
        updated = updated.with_changes(module=original.module)
        if isinstance(updated.names, cst.ImportStar) or self._imports_from_the_repo(original):
            return updated
        # De un módulo ajeno solo se puede importar lo que ese módulo exporta:
        # el nombre importado es suyo, no del repo, y renombrarlo deja un
        # ImportError en tiempo de import.
        return updated.with_changes(names=_restored_import_names(original.names, updated.names))

    def _imports_from_the_repo(self, node: cst.ImportFrom) -> bool:
        if node.relative:
            return True
        dotted = _dotted(node.module) if node.module is not None else None
        return dotted is not None and dotted in self.modules

    def leave_Assign(self, original: cst.Assign, updated: cst.Assign) -> cst.Assign:
        if not any(
            isinstance(target.target, cst.Name) and target.target.value == "__all__"
            for target in updated.targets
        ):
            return updated
        # La única cadena que sí se sigue: `__all__` decide qué trae un
        # `import *`, se resuelve estáticamente y dejarla atrás dejaría al
        # importador sin el símbolo (§4.3.3 excluye lo *indecidible*, no esto).
        value = updated.value
        if not isinstance(value, (cst.List, cst.Tuple)):
            return updated
        return updated.with_changes(
            value=value.with_changes(
                elements=[
                    element.with_changes(value=self._renamed_string(element.value))
                    if isinstance(element.value, cst.SimpleString)
                    else element
                    for element in value.elements
                ]
            )
        )

    def _renamed_string(self, node: cst.SimpleString) -> cst.SimpleString:
        new = self.renames.get(node.raw_value)
        if new is None:
            return node
        return node.with_changes(value=f"{node.prefix}{node.quote}{new}{node.quote}")

    def leave_Arg(self, original: cst.Arg, updated: cst.Arg) -> cst.Arg:
        # La palabra clave de una llamada es la firma de quien la recibe, y casi
        # siempre es de fuera del repo. Como los nombres que además son
        # parámetro quedan fuera del diccionario, aquí nunca hay nada que
        # renombrar: lo que llegue renombrado viene de una coincidencia.
        return updated.with_changes(keyword=original.keyword)

    def leave_ClassDef(self, original: cst.ClassDef, updated: cst.ClassDef) -> cst.ClassDef:
        # Lo que se define en el cuerpo de una clase se usa como `obj.nombre`, y
        # esos usos se dejan pasar porque atribuirlos a su clase exigiría
        # inferencia de tipos. Renombrar solo la definición deja un
        # AttributeError, así que aquí se devuelve el nombre que `leave_Name`
        # cambió de camino.
        if not isinstance(original.body, cst.IndentedBlock) or not isinstance(
            updated.body, cst.IndentedBlock
        ):
            return updated
        return updated.with_changes(
            body=updated.body.with_changes(
                body=[
                    _restored_definition(before, after)
                    for before, after in zip(original.body.body, updated.body.body)
                ]
            )
        )

    def leave_Attribute(self, original: cst.Attribute, updated: cst.Attribute) -> cst.Attribute:
        if self._is_repo_module(original.value):
            return updated
        # `leave_Name` ya renombró el atributo: aquí se deshace, porque el
        # atributo de cualquier otra cosa —`self`, un objeto, un módulo de la
        # biblioteca estándar— solo comparte nombre por casualidad, y cambiarlo
        # deja un AttributeError que se lee como un agente que fracasa.
        return updated.with_changes(attr=original.attr)

    def _is_repo_module(self, node: cst.BaseExpression) -> bool:
        # Se pregunta sobre el nodo original: los hijos ya vienen renombrados y
        # su nombre nuevo no resuelve contra nada.
        return _resolves_to_module(_dotted(node), self.aliases, self.modules)


def _restored_definition(before: cst.BaseStatement, after: cst.BaseStatement) -> cst.BaseStatement:
    """Devuelve a una sentencia del cuerpo de una clase el nombre que definía."""
    if isinstance(after, (cst.FunctionDef, cst.ClassDef)) and isinstance(before, type(after)):
        return after.with_changes(name=before.name)
    if isinstance(after, cst.SimpleStatementLine) and isinstance(before, cst.SimpleStatementLine):
        return after.with_changes(
            body=[
                _restored_target(small_before, small_after)
                for small_before, small_after in zip(before.body, after.body)
            ]
        )
    return after


def _restored_target(before: cst.BaseSmallStatement, after: cst.BaseSmallStatement):
    """Solo el destino: el valor asignado sigue siendo código y sí se renombra."""
    if isinstance(after, cst.Assign) and isinstance(before, cst.Assign):
        return after.with_changes(
            targets=[
                target_after.with_changes(target=target_before.target)
                if isinstance(target_after.target, cst.Name)
                else target_after
                for target_before, target_after in zip(before.targets, after.targets)
            ]
        )
    if (
        isinstance(after, cst.AnnAssign)
        and isinstance(before, cst.AnnAssign)
        and isinstance(after.target, cst.Name)
    ):
        return after.with_changes(target=before.target)
    return after


def apply(root: Path) -> TransformResult:
    renames = collect_renames(root)
    modules = repo_modules(root)
    changed = 0
    for path in doctest_files(root):
        source = read_source(path)
        # El fichero entero es texto de doctest: no hay módulo que parsear, así
        # que los alias salen solo de los imports de sus propios ejemplos.
        transformed = rename_in_doctests(source, renames, {}, modules, path, root)
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    for path in iter_transformable_files(root):
        source = read_source(path)
        try:
            module = cst.parse_module(source)
        except cst.ParserSyntaxError:
            continue
        tree = parse_source(path)
        aliases = {} if tree is None else module_aliases(tree, path, root, modules)
        transformed = module.visit(_Rename(renames, aliases, modules, path, root)).code
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return TransformResult(files_changed=changed, renames=renames)
