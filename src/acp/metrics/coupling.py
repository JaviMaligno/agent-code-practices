from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from acp.metrics.size import iter_source_files
from acp.models import CouplingMetrics


def _module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve(candidate: str, known: set[str]) -> str | None:
    """Módulo conocido más largo que sea prefijo del nombre importado."""
    while candidate:
        if candidate in known:
            return candidate
        candidate = candidate.rpartition(".")[0]
    return None


def _import_targets(tree: ast.AST, known: set[str]) -> set[str]:
    """Módulos internos a los que apunta cada sentencia de import.

    De `from pkg import core` sale `pkg.core`, no `pkg`: se toma siempre el
    destino más específico que exista, y solo se cae al módulo padre si el
    nombre importado es un símbolo y no un submódulo.
    """
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve(alias.name, known)
                if resolved:
                    targets.add(resolved)
        elif isinstance(node, ast.ImportFrom) and node.module:
            specific = {
                resolved
                for alias in node.names
                if (resolved := _resolve(f"{node.module}.{alias.name}", known))
                and resolved != node.module
            }
            if specific:
                targets |= specific
            else:
                parent = _resolve(node.module, known)
                if parent:
                    targets.add(parent)
    return targets


def measure(root: Path) -> CouplingMetrics:
    files = iter_source_files(root)
    modules = {_module_name(path, root): path for path in files}
    known = set(modules)

    fan_out: dict[str, set[str]] = defaultdict(set)
    fan_in: dict[str, int] = defaultdict(int)

    for name, path in modules.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for target in _import_targets(tree, known):
            if target != name:
                fan_out[name].add(target)
                fan_in[target] += 1

    edges = sum(len(targets) for targets in fan_out.values())
    module_count = len(modules) or 1
    return CouplingMetrics(
        internal_modules=len(modules),
        internal_edges=edges,
        mean_fan_out=edges / module_count,
        max_fan_in=max(fan_in.values()) if fan_in else 0,
    )
