from __future__ import annotations

import ast
from pathlib import Path

from acp.metrics.size import iter_source_files
from acp.models import DomainMetrics

BRANCHING = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert)
MIN_COMPLEXITY = 3
MAX_SAMPLES = 15


def cyclomatic_complexity(node: ast.AST) -> int:
    score = 1
    for child in ast.walk(node):
        if isinstance(child, BRANCHING):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            score += len(child.ifs)
    return score


def _local_names(root: Path, files: list[Path]) -> set[str]:
    """Nombres de módulo de primer nivel que pertenecen al propio repo."""
    return {path.relative_to(root).parts[0].removesuffix(".py") for path in files}


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
    return False


def measure(root: Path) -> DomainMetrics:
    files = iter_source_files(root)
    local = _local_names(root, files)

    complex_count = 0
    candidates: list[str] = []
    total_functions = 0

    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        module = ".".join(path.relative_to(root).with_suffix("").parts)
        imported_local = {
            alias.asname or alias.name.split(".")[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in local
            for alias in node.names
        }

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            total_functions += 1
            if cyclomatic_complexity(node) < MIN_COMPLEXITY:
                continue
            complex_count += 1
            if _calls_internal(node, local, imported_local):
                candidates.append(f"{module}.{node.name}")

    return DomainMetrics(
        complex_functions=complex_count,
        domain_candidate_functions=len(candidates),
        domain_density=len(candidates) / (total_functions or 1),
        samples=candidates[:MAX_SAMPLES],
    )
