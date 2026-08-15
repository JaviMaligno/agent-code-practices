from __future__ import annotations

import ast
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
    return {name: _opaque(name, index) for index, name in enumerate(sorted(names))}


class _Rename(cst.CSTTransformer):
    def __init__(self, renames: dict[str, str]) -> None:
        self.renames = renames

    def leave_Name(self, original: cst.Name, updated: cst.Name) -> cst.Name:
        new = self.renames.get(updated.value)
        return updated.with_changes(value=new) if new else updated


def apply(root: Path) -> TransformResult:
    renames = collect_renames(root)
    changed = 0
    for path in iter_transformable_files(root):
        source = read_source(path)
        try:
            module = cst.parse_module(source)
        except cst.ParserSyntaxError:
            continue
        transformed = module.visit(_Rename(renames)).code
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return TransformResult(files_changed=changed, renames=renames)
