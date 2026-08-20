"""El catálogo contra un repositorio de verdad. Necesita red, no Docker.

Tres fases anteriores dejaron la misma lección: un arreglo verificado solo
contra fixtures se cae al pasarlo por un repo real. Aquí lo que se comprueba no
es que las mutaciones sean buenas tareas —eso lo dice la validación de §3.3
corriendo la suite— sino las dos propiedades de las que depende el generador y
que un fixture de diez líneas no puede poner a prueba:

  1. Que el resultado sigue compilando, sobre código con decoradores, clases
     anidadas, `async`, comprensiones y strings raros.
  2. Que el parche toca UNA línea y esa línea cae dentro de la función que la
     tarea nombra. Es lo que hace deducible el conjunto `fail_to_pass`: si la
     mutación se colara en la función de al lado, la tarea rompería tests que no
     declara y la validación la tiraría —tarde y después de dos corridas de
     Docker—.

Y de paso mide algo que ningún fixture puede decir: cuántas funciones del
sustrato admite cada forma del catálogo. Si una forma casi no aplicara, el
reparto entre formas de §3.3.1 no sería posible y habría que saberlo antes de
generar las 12 tareas.
"""

from __future__ import annotations

import ast
import difflib
import subprocess
from pathlib import Path

import pytest

from acp.tasks.mutations import MUTATIONS, mutate

pytestmark = pytest.mark.integration

REPO = "https://github.com/arthurdejong/python-stdnum"

# python-stdnum es el más barato del sustrato y el que Task 3 usa para validar,
# así que es donde antes se notaría un desacuerdo entre catálogo y realidad.
# Se recorre un prefijo de los ficheros, ordenado, y no el repo entero: 300
# ficheros × todas sus funciones × cuatro formas son miles de parseos de LibCST
# para responder una pregunta que ya contesta una muestra. El orden fijo es lo
# que hace que la muestra sea la misma en cada corrida.
SAMPLED_FILES = 60


def _symbols(tree: ast.Module) -> list[tuple[str, int, int]]:
    """Nombre cualificado, primera y última línea de cada función del módulo.

    Los rangos salen del `ast` y no de LibCST porque es contra el fuente
    ORIGINAL contra lo que hay que situar el cambio, y el `ast` los da ya
    calculados.
    """
    found: list[tuple[str, int, int]] = []

    def walk(node: ast.AST, prefix: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                path = (*prefix, child.name)
                if not isinstance(child, ast.ClassDef):
                    # `lineno` de una función decorada apunta al `def`, no al
                    # decorador, que es justo el rango que queremos: un cambio
                    # en un decorador estaría fuera de la función.
                    found.append((".".join(path), child.lineno, child.end_lineno or child.lineno))
                walk(child, path)

    walk(tree, ())
    return found


def _changed_lines(before: str, after: str) -> list[int]:
    """Las líneas del fuente ORIGINAL que el parche toca, 1-indexadas."""
    old = before.splitlines(keepends=True)
    new = after.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    touched: list[int] = []
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        # Una inserción pura no consume líneas del original; se le atribuye la
        # línea donde entra para poder situarla igual dentro de la función.
        touched.extend(range(i1 + 1, max(i2, i1 + 1) + 1))
    return touched


def test_the_catalogue_survives_a_real_repository(tmp_path: Path):
    clone = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", "--depth", "1", REPO, str(clone)],
        check=True,
        capture_output=True,
    )

    applied = {kind: 0 for kind in MUTATIONS}
    attempted = 0
    files = sorted((clone / "stdnum").rglob("*.py"))[:SAMPLED_FILES]
    assert files, "el clon no trajo el paquete"

    for path in files:
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - python-stdnum no tiene de estos
            continue
        for symbol, first, last in _symbols(tree):
            for kind in MUTATIONS:
                attempted += 1
                mutated = mutate(source, symbol, kind)
                if mutated is None:
                    continue
                applied[kind] += 1

                compile(mutated, str(path), "exec")

                touched = _changed_lines(source, mutated)
                assert touched, f"{path}:{symbol}:{kind} dice que aplicó y no cambió nada"
                assert all(first <= line <= last for line in touched), (
                    f"{path}:{symbol}:{kind} tocó las líneas {touched}, "
                    f"fuera de {first}-{last}"
                )

    # Ninguna forma puede quedarse en cero: el reparto entre formas distintas es
    # lo que impide que el estrato genérico mida una sola habilidad (§3.3.1).
    assert attempted > 200, attempted
    assert all(count > 0 for count in applied.values()), applied
    print(f"\nsímbolos×formas intentados: {attempted}; aplicados: {applied}")
