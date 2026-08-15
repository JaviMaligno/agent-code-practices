from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from pathlib import Path

from acp.metrics.size import iter_source_files, parse_source


@dataclass(frozen=True)
class Location:
    """Dónde vive un símbolo y cómo se llama ahora.

    `module` y el nombre original forman la clave; `current_name` es lo que el
    agente ve después de transformar. Separar las dos cosas es lo que permite
    medir localización en todas las condiciones con la misma vara.
    """

    module: str
    path: str
    start: int
    end: int
    current_name: str


def build_symbol_map(root: Path) -> dict[str, Location]:
    """Funciones, clases y métodos del árbol, con su fichero y rango de líneas."""
    symbols: dict[str, Location] = {}
    for path in iter_source_files(root):
        tree = parse_source(path)
        if tree is None:
            continue
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        relative = path.relative_to(root).as_posix()
        for node, qualified in _walk_definitions(tree):
            symbols[f"{module}.{qualified}"] = Location(
                module=module,
                path=relative,
                start=node.lineno,
                end=node.end_lineno or node.lineno,
                current_name=node.name,
            )
    return symbols


_Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _walk_definitions(tree: ast.Module) -> list[tuple[_Definition, str]]:
    """Definiciones con su nombre cualificado dentro del módulo."""
    found: list[tuple[_Definition, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = f"{prefix}{child.name}"
                found.append((child, qualified))
                visit(child, f"{qualified}.")

    visit(tree, "")
    return found


def apply_renames(
    symbols: dict[str, Location], renames: dict[str, str]
) -> dict[str, Location]:
    """Actualiza el nombre visible sin tocar la identidad."""
    return {
        key: replace(location, current_name=renames.get(location.current_name, location.current_name))
        for key, location in symbols.items()
    }
