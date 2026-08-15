from __future__ import annotations

import ast
import builtins
from pathlib import Path

import libcst as cst

from acp.metrics.size import iter_source_files, parse_source, read_source
from acp.transforms.base import TransformResult, iter_transformable_files

# Un módulo que use cualquiera de estas queda fuera del renombrado entero: sus
# nombres se alcanzan por cadena y renombrarlos rompe el programa (§4.3.3).
DYNAMIC_ACCESS = {"getattr", "setattr", "hasattr", "globals", "locals", "vars", "eval", "exec"}


def _opaque(name: str, index: int) -> str:
    if name.isupper():
        return f"C{index}"
    if name[:1].isupper():
        return f"K{index}"
    return f"f{index}"


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


def collect_renames(root: Path) -> dict[str, str]:
    """Diccionario de renombrado de los símbolos que define el propio repo.

    Solo entran definiciones de nivel de módulo —funciones, clases y constantes—
    porque son las que se pueden resolver estáticamente. Los métodos se dejan:
    una llamada `obj.metodo()` no se puede atribuir a una clase sin inferencia
    de tipos, y equivocarse rompe el repo en silencio.
    """
    names: set[str] = set()
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
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("__"):
                        names.add(target.id)
    # El nombre de un módulo o paquete no se renombra nunca —los ficheros siguen
    # donde estaban—, así que un símbolo que se llame igual que un módulo del
    # repo tendría dos significados bajo la misma entrada del diccionario y
    # rompería los imports. Sale fuera.
    basenames = {module.rsplit(".", 1)[-1] for module in repo_modules(root)}
    names -= basenames
    # Un nombre que además es parámetro en algún sitio significa dos cosas: el
    # símbolo del módulo y una variable local. Renombrar las dos rompe las
    # llamadas por palabra clave; renombrar una sola, el cuerpo de la función.
    names -= _parameter_names(root)
    # Y un `def format(...)` propio no convierte en suyas las llamadas al
    # builtin `format` del resto del repo: renombrarlas daría NameError.
    names -= set(dir(builtins))
    return {name: _opaque(name, index) for index, name in enumerate(sorted(names))}


def _dotted(node: cst.BaseExpression) -> str | None:
    """La cadena `a.b.c` de una cadena de atributos, o None si no lo es."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr.value}" if head else None
    return None


class _Rename(cst.CSTTransformer):
    def __init__(
        self, renames: dict[str, str], aliases: dict[str, str], modules: set[str]
    ) -> None:
        self.renames = renames
        self.aliases = aliases
        self.modules = modules

    def leave_Name(self, original: cst.Name, updated: cst.Name) -> cst.Name:
        new = self.renames.get(updated.value)
        return updated.with_changes(value=new) if new else updated

    def leave_Arg(self, original: cst.Arg, updated: cst.Arg) -> cst.Arg:
        # La palabra clave de una llamada es la firma de quien la recibe, y casi
        # siempre es de fuera del repo. Como los nombres que además son
        # parámetro quedan fuera del diccionario, aquí nunca hay nada que
        # renombrar: lo que llegue renombrado viene de una coincidencia.
        return updated.with_changes(keyword=original.keyword)

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
        dotted = _dotted(node)
        if dotted is None:
            return False
        head, _, rest = dotted.partition(".")
        target = self.aliases.get(head)
        if target is None:
            return False
        return (f"{target}.{rest}" if rest else target) in self.modules


def apply(root: Path) -> TransformResult:
    renames = collect_renames(root)
    modules = repo_modules(root)
    changed = 0
    for path in iter_transformable_files(root):
        source = read_source(path)
        try:
            module = cst.parse_module(source)
        except cst.ParserSyntaxError:
            continue
        tree = parse_source(path)
        aliases = {} if tree is None else module_aliases(tree, path, root, modules)
        transformed = module.visit(_Rename(renames, aliases, modules)).code
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return TransformResult(files_changed=changed, renames=renames)
