from __future__ import annotations

import ast
from pathlib import Path

from acp.metrics.size import parse_source
from acp.models import RuntimeTypingMetrics
from acp.transforms.base import iter_transformable_files

# Librerías que convierten las anotaciones en comportamiento en ejecución.
RUNTIME_TYPING_MODULES = {
    "pydantic", "attrs", "attr", "marshmallow", "cattrs", "typeguard",
    "beartype", "trafaret", "schematics", "msgspec", "typedload",
}
RUNTIME_TYPING_CALLS = {"get_type_hints", "validate_arguments", "validate_call"}
# Stdlib, así que ningún import de tercero los delata: `register` elige la
# implementación leyendo la anotación del primer argumento, con lo que quitar
# las anotaciones cambiaría el comportamiento del programa.
RUNTIME_TYPING_DECORATORS = {"singledispatch", "singledispatchmethod"}


def _decorator_name(node: ast.expr) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    return getattr(target, "attr", None) or getattr(target, "id", None)


def measure(root: Path) -> RuntimeTypingMetrics:
    evidence: list[str] = []
    affected: set[str] = set()
    # La única métrica de la fase 0 que no perfila el repo: decide si el
    # candidato se excluye porque A1 no sería equivalente en él (§3.2.4). Por eso
    # criba el conjunto que las transformaciones REESCRIBEN —tests y conftest
    # incluidos (§4.3.1)— y no el que se perfila, que los deja fuera: un
    # `@singledispatch` en la suite rompe el árbol transformado igual que uno en
    # el paquete, y ahí el fallo ya solo lo ve el pre-flight §3.6.3.
    files = iter_transformable_files(root)

    for path in files:
        tree = parse_source(path)
        if tree is None:
            continue

        # Ruta relativa, no nombre suelto: la evidencia existe para poder ir a
        # comprobarla, y en un repo grande hay varios `models.py`.
        location = path.relative_to(root).as_posix()
        before = len(evidence)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in RUNTIME_TYPING_MODULES:
                        evidence.append(f"{location}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root_module = (node.module or "").split(".")[0]
                if root_module in RUNTIME_TYPING_MODULES:
                    evidence.append(f"{location}: from {node.module} import ...")
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name in RUNTIME_TYPING_CALLS:
                    evidence.append(f"{location}: {name}()")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    name = _decorator_name(decorator)
                    if name in RUNTIME_TYPING_DECORATORS:
                        evidence.append(f"{location}: @{name}")

        if len(evidence) > before:
            affected.add(location)

    return RuntimeTypingMetrics(
        uses_runtime_typing=bool(evidence),
        evidence=evidence[:20],
        affected_files=len(affected),
        total_files=len(files),
    )
