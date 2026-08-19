from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from pathlib import Path

from acp.metrics.size import iter_source_files, module_name, parse_source


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


def relocate_symbols(
    symbols: dict[str, Location],
    root: Path,
    moves: dict[str, str] | None = None,
    symbol_moves: dict[str, str] | None = None,
    renames: dict[str, str] | None = None,
) -> dict[str, Location]:
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

    Y el nombre visible se lee del código, no se deduce del diccionario de
    renombrados: A2 renombra por ámbito y restaura lo que define el cuerpo de
    una clase, así que el mismo nombre desnudo puede haber cambiado en un sitio
    y no en otro. Un manifiesto que anuncia `f1` donde el fichero sigue diciendo
    `info` señala un símbolo que no existe.

    Lo que no aparece en el árbol transformado se cae del mapa: un rango que no
    se puede verificar contra lo que ve el agente no es procedencia, es ruido
    con pinta de dato. Y si el módulo no tiene la misma forma en los dos árboles
    —alguien añadió o quitó una definición— no se publica ninguno de sus
    símbolos: las hermanas que vienen detrás se habrían corrido un puesto y cada
    una heredaría el rango de otra, que es justo la mentira que este mapa existe
    para no contar. Antes de mentir, callar.

    `moves` —módulo original → módulo destino— es lo que permite emparejar
    cuando la transformación mueve los símbolos ENTRE módulos y no solo dentro
    de cada uno: la familia B renombra y reubica ficheros, y sin seguir el
    movimiento el módulo original no aparece en el árbol transformado y todos
    sus símbolos se caerían del mapa a la vez. Solo la transformación sabe qué
    movió, así que el dato tiene que llegar de fuera; lo que no venga en el
    diccionario se busca donde siempre, y si tampoco está ahí, se calla.

    `symbol_moves` —clave del mapa → módulo destino— es lo mismo un escalón más
    abajo, y hace falta porque B1 no mueve ficheros sino DEFINICIONES SUELTAS:
    dos símbolos que vivían juntos acaban en ficheros distintos, y un mapa por
    módulo no sabe decir eso. Manda sobre `moves` por ser lo más específico.

    Y con él cambia el criterio de emparejamiento, que aquí son dos y distintos
    a propósito:

      - Cuando el módulo viaja entero, POSICIÓN: ninguna de esas
        transformaciones añade ni quita definiciones dentro del módulo, así que
        el índice entre hermanas es estable y sobrevive al renombrado de A2.
      - Cuando un símbolo viaja solo, NOMBRE: su módulo original pierde unas
        definiciones y recibe otras a la vez, así que la primera definición del
        fichero ya no es la que estaba y la posición deja de ser una identidad
        —emparejar por ella publicaría el rango del vecino bajo la clave del
        símbolo, que es peor que callar porque nadie lo ve venir—. Por eso, en
        cuanto un módulo tiene UN símbolo en `symbol_moves`, todos los suyos
        pasan a buscarse por nombre, se hayan movido o no.

    El nombre por el que se busca es el que el símbolo tenga en el árbol
    transformado: A2 corre antes que la familia B (`CANONICAL_ORDER`), así que
    el símbolo llega al destino ya renombrado y buscarlo por su nombre original
    no encontraría nada. De ahí `renames` —el mismo diccionario que publica el
    manifiesto—, que solo se usa para SABER POR QUÉ NOMBRE PREGUNTAR; el nombre
    que se publica se sigue leyendo del código, nunca del diccionario.
    """
    moves = moves or {}
    symbol_moves = symbol_moves or {}
    renames = renames or {}
    current = _definitions_by_module(root)
    relocated: dict[str, Location] = {}
    for module, entries in _grouped_by_module(symbols).items():
        if any(_symbol_destination(key, module, symbol_moves) for key, _ in entries):
            relocated.update(
                _by_name(symbols, entries, module, current, moves, symbol_moves, renames)
            )
            continue
        # Dónde hay que ir a buscar ahora, que puede no ser su propio módulo.
        found = current.get(moves.get(module, module), {})
        by_position = {
            position: found[qualified]
            for qualified, position in _positions(list(found)).items()
        }
        positions = _positions([qualified for _, qualified in entries])
        if set(positions.values()) != set(by_position):
            continue
        for key, qualified in entries:
            location = by_position[positions[qualified]]
            relocated[key] = replace(
                symbols[key],
                path=location.path,
                start=location.start,
                end=location.end,
                current_name=location.current_name,
            )
    return relocated


def _symbol_destination(key: str, module: str, symbol_moves: dict[str, str]) -> str | None:
    """El destino que `symbol_moves` declara para un símbolo, o para su dueño.

    Un método no viaja por su cuenta: se va con la clase que lo contiene, y una
    transformación que mueve definiciones de nivel de módulo solo anuncia la
    clase. Buscar por el prefijo más largo es lo que hace que sus miembros no se
    queden esperando en un módulo del que la clase ya se fue.
    """
    parts = key.split(".")
    depth = len(module.split(".")) + 1
    for cut in range(len(parts), depth - 1, -1):
        destination = symbol_moves.get(".".join(parts[:cut]))
        if destination is not None:
            return destination
    return None


def _by_name(
    symbols: dict[str, Location],
    entries: list[tuple[str, str]],
    module: str,
    current: dict[str, dict[str, Location]],
    moves: dict[str, str],
    symbol_moves: dict[str, str],
    renames: dict[str, str],
) -> dict[str, Location]:
    """Emparejamiento por nombre, para los módulos que B1 desarmó.

    Aquí no hay guardia de forma como en el emparejamiento posicional: el nombre
    identifica al símbolo por sí solo, así que cada uno se publica o se calla
    por separado y que falte un vecino no arrastra a los demás.
    """
    relocated: dict[str, Location] = {}
    for key, qualified in entries:
        target = _symbol_destination(key, module, symbol_moves) or moves.get(module, module)
        # El nombre cualificado tal y como se escribe hoy en el árbol: cada
        # tramo puede haberlo renombrado A2, la clase que lo contiene incluida.
        renamed = ".".join(renames.get(part, part) for part in qualified.split("."))
        location = current.get(target, {}).get(renamed)
        if location is None:
            continue
        relocated[key] = replace(
            symbols[key],
            path=location.path,
            start=location.start,
            end=location.end,
            current_name=location.current_name,
        )
    return relocated


def _definitions_by_module(root: Path) -> dict[str, dict[str, Location]]:
    """Las definiciones de cada módulo, en orden de recorrido y por su nombre."""
    modules: dict[str, dict[str, Location]] = {}
    for path in iter_source_files(root):
        tree = parse_source(path)
        if tree is None:
            continue
        # El mismo nombre que usa la familia B para anunciar sus movimientos: si
        # las dos formas no coinciden, `moves` no encuentra nada y el módulo
        # entero se cae del mapa (`relocate_symbols`).
        module = module_name(path, root)
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
