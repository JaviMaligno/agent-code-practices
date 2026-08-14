from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from acp.metrics.size import iter_source_files, parse_source
from acp.models import DomainMetrics

BRANCHING = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert)
MIN_COMPLEXITY = 3
MAX_SAMPLES = 15


def _own_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Descendientes de una función, sin entrar en las funciones que anida.

    `measure` recorre el árbol con `ast.walk` y ya cuenta cada función anidada
    como función propia: si sus ramas se sumaran también a la que la contiene,
    se contarían dos veces.
    """
    for child in ast.iter_child_nodes(node):
        yield child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield from _own_nodes(child)


def cyclomatic_complexity(node: ast.AST) -> int:
    score = 1
    for child in _own_nodes(node):
        if isinstance(child, BRANCHING):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            score += 1 + len(child.ifs)
        elif isinstance(child, ast.match_case):
            score += 1
    return score


SELF_NAMES = {"self", "cls"}


def _importable_root(root: Path, path: Path) -> str:
    """Primer componente de la ruta que es realmente importable.

    Un layout src/ mete por delante un componente que no es ningún paquete: sin
    saltarlo, el único nombre "local" del repo sería `src` y ningún import
    interno casaría. Solo se salta cuando ese componente no es paquete y el
    siguiente sí lo es, para no romper los paquetes de espacio de nombres.
    """
    parts = path.relative_to(root).parts
    index = 0
    while index + 1 < len(parts) - 1:
        current = root.joinpath(*parts[: index + 1])
        following = root.joinpath(*parts[: index + 2])
        if (current / "__init__.py").exists() or not (following / "__init__.py").exists():
            break
        index += 1
    return parts[index].removesuffix(".py")


def _local_names(root: Path, files: list[Path]) -> set[str]:
    """Nombres de módulo de primer nivel que pertenecen al propio repo."""
    return {_importable_root(root, path) for path in files}


def _own_definitions(tree: ast.Module) -> set[str]:
    """Funciones y clases definidas en el propio fichero.

    Llamarlas es tan interno como llamar a un módulo hermano: sin contarlas, un
    repo que concentre la lógica en un fichero mide densidad de dominio cero.
    """
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _calls_internal(node: ast.AST, local: set[str], sibling_defs: set[str]) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name) and func.id in sibling_defs:
            return True
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in local or func.value.id in sibling_defs:
                return True
            # self._helper(...) es una llamada interna por definición: es el
            # estilo de holidays, donde el dominio vive en métodos.
            if func.value.id in SELF_NAMES:
                return True
    return False


def measure(root: Path) -> DomainMetrics:
    files = iter_source_files(root)
    local = _local_names(root, files)

    complex_count = 0
    candidates: list[str] = []
    total_functions = 0

    for path in files:
        tree = parse_source(path)
        if tree is None:
            continue

        module = ".".join(path.relative_to(root).with_suffix("").parts)
        imported_local = {
            alias.asname or alias.name.split(".")[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in local
            for alias in node.names
        }
        internal_names = imported_local | _own_definitions(tree)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            total_functions += 1
            if cyclomatic_complexity(node) < MIN_COMPLEXITY:
                continue
            complex_count += 1
            if _calls_internal(node, local, internal_names):
                candidates.append(f"{module}.{node.name}")

    return DomainMetrics(
        complex_functions=complex_count,
        domain_candidate_functions=len(candidates),
        domain_density=len(candidates) / (total_functions or 1),
        samples=candidates[:MAX_SAMPLES],
    )
