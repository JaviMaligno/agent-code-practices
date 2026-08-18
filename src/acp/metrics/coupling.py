from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from acp.metrics.size import iter_source_files, parse_source
from acp.metrics.size import module_name as _module_name
from acp.models import CouplingMetrics


def _resolve(candidate: str, known: set[str]) -> str | None:
    """Módulo conocido más largo que sea prefijo del nombre importado."""
    while candidate:
        if candidate in known:
            return candidate
        candidate = candidate.rpartition(".")[0]
    return None


def _package_of(module: str, is_init: bool) -> str:
    """Paquete al que pertenece un módulo, que es el ancla de sus imports relativos."""
    return module if is_init else module.rpartition(".")[0]


def _join(package: str, name: str) -> str:
    """Cualifica un nombre con su paquete, que puede ser la raíz del clon."""
    return f"{package}.{name}" if package else name


def _absolute_module(node: ast.ImportFrom, package: str) -> str:
    """Nombre absoluto del módulo importado, resolviendo los `.` iniciales.

    `from .core import x` dentro de pkg.api apunta a pkg.core; sin esto el
    import se pierde entero y el repo se lee como desacoplado. La cadena vacía
    es un ancla legítima, no un fallo: cuando el propio clon es el paquete, sus
    módulos no llevan prefijo ninguno.
    """
    if not node.level:
        return node.module or ""
    base = package
    for _ in range(node.level - 1):
        base = base.rpartition(".")[0]
    return _join(base, node.module) if node.module else base


def _import_targets(tree: ast.AST, known: set[str], package: str = "") -> set[str]:
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
        elif isinstance(node, ast.ImportFrom):
            module = _absolute_module(node, package)
            if not module and not node.level:
                continue
            specific = {
                resolved
                for alias in node.names
                if (resolved := _resolve(_join(module, alias.name), known))
                and resolved != module
            }
            if specific:
                targets |= specific
            else:
                parent = _resolve(module, known)
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
        tree = parse_source(path)
        if tree is None:
            continue
        package = _package_of(name, path.name == "__init__.py")
        for target in _import_targets(tree, known, package):
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
