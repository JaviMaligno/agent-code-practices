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
    for module, located in _definitions_by_module(root).items():
        for qualified, location in located.items():
            symbols[f"{module}.{qualified}"] = location
    return symbols


def relocate_symbols(symbols: dict[str, Location], root: Path) -> dict[str, Location]:
    """Reasienta el mapa sobre el árbol transformado sin tocar la identidad.

    El mapa se publica para proyectar sobre él lo que el agente lee (§5.4.2), y
    el agente lee el árbol transformado, no el original: A1, A3 y A4 desplazan
    líneas, así que los rangos medidos sobre el original señalan otra región.
    Y una localización falsa es peor que ninguna, porque nadie la ve venir.

    El emparejamiento va por POSICIÓN estructural dentro del módulo —el índice
    entre hermanas en cada nivel de anidamiento—, nunca por nombre: A2 cambia
    justamente el nombre, que es lo que hay que volver a leer del árbol. Ninguna
    transformación añade ni quita definiciones, así que la posición es una
    identidad estable frente a las cuatro.

    Lo que no aparece en el árbol transformado se cae del mapa: un rango que no
    se puede verificar contra lo que ve el agente no es procedencia, es ruido
    con pinta de dato.
    """
    current = _definitions_by_module(root)
    relocated: dict[str, Location] = {}
    for module, entries in _grouped_by_module(symbols).items():
        found = current.get(module, {})
        by_position = {
            position: found[qualified]
            for qualified, position in _positions(list(found)).items()
        }
        positions = _positions([qualified for _, qualified in entries])
        for key, qualified in entries:
            location = by_position.get(positions[qualified])
            if location is None:
                continue
            relocated[key] = replace(
                symbols[key],
                path=location.path,
                start=location.start,
                end=location.end,
            )
    return relocated


def _definitions_by_module(root: Path) -> dict[str, dict[str, Location]]:
    """Las definiciones de cada módulo, en orden de recorrido y por su nombre."""
    modules: dict[str, dict[str, Location]] = {}
    for path in iter_source_files(root):
        tree = parse_source(path)
        if tree is None:
            continue
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        relative = path.relative_to(root).as_posix()
        located: dict[str, Location] = {}
        for node, qualified in _walk_definitions(tree):
            located[qualified] = Location(
                module=module,
                path=relative,
                start=node.lineno,
                end=node.end_lineno or node.lineno,
                current_name=node.name,
            )
        modules[module] = located
    return modules


def _grouped_by_module(symbols: dict[str, Location]) -> dict[str, list[tuple[str, str]]]:
    """Cada símbolo bajo su módulo, como (clave del mapa, nombre cualificado).

    El orden de inserción del mapa es el del recorrido, y eso es lo que hace
    comparables las posiciones de los dos árboles.
    """
    grouped: dict[str, list[tuple[str, str]]] = {}
    for key, location in symbols.items():
        qualified = key[len(location.module) + 1 :]
        grouped.setdefault(location.module, []).append((key, qualified))
    return grouped


def _positions(qualified_names: list[str]) -> dict[str, tuple[int, ...]]:
    """Posición de cada definición: índice entre hermanas, nivel a nivel.

    Se calcula igual en los dos árboles y con los nombres de cada uno, así que
    `Widget.render` y `K0.render` acaban en la misma posición aunque A2 haya
    renombrado la clase de en medio.
    """
    positions: dict[str, tuple[int, ...]] = {}
    counters: dict[str, int] = {}
    for qualified in qualified_names:
        parent, _, _ = qualified.rpartition(".")
        index = counters.get(parent, 0)
        counters[parent] = index + 1
        positions[qualified] = positions.get(parent, ()) + (index,)
    return positions


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
