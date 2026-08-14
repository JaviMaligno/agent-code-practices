from __future__ import annotations

import ast
from pathlib import Path

from acp.metrics.size import iter_source_files, parse_source
from acp.models import RuntimeTypingMetrics

# Librerías que convierten las anotaciones en comportamiento en ejecución.
RUNTIME_TYPING_MODULES = {
    "pydantic", "attrs", "attr", "marshmallow", "cattrs", "typeguard",
    "beartype", "trafaret", "schematics", "msgspec", "typedload",
}
RUNTIME_TYPING_CALLS = {"get_type_hints", "validate_arguments", "validate_call"}


def measure(root: Path) -> RuntimeTypingMetrics:
    evidence: list[str] = []

    for path in iter_source_files(root):
        tree = parse_source(path)
        if tree is None:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in RUNTIME_TYPING_MODULES:
                        evidence.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root_module = (node.module or "").split(".")[0]
                if root_module in RUNTIME_TYPING_MODULES:
                    evidence.append(f"{path.name}: from {node.module} import ...")
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name in RUNTIME_TYPING_CALLS:
                    evidence.append(f"{path.name}: {name}()")

    return RuntimeTypingMetrics(uses_runtime_typing=bool(evidence), evidence=evidence[:20])
