from __future__ import annotations

import ast
from pathlib import Path

from acp.metrics.size import iter_source_files
from acp.models import ReadabilityMetrics

README_NAMES = ("README.md", "README.rst", "README.txt", "README")
DOCS_DIRS = ("docs", "doc")


def _is_annotated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if node.returns is not None:
        return True
    args = node.args
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg:
        every.append(args.vararg)
    if args.kwarg:
        every.append(args.kwarg)
    return any(arg.annotation is not None for arg in every)


def measure(root: Path) -> ReadabilityMetrics:
    total_lines = 0
    comment_lines = 0
    docstring_lines = 0
    functions = 0
    annotated = 0

    for path in iter_source_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        total_lines += len(lines)
        comment_lines += sum(1 for line in lines if line.strip().startswith("#"))

        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstring_lines += len(doc.splitlines())
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions += 1
                if _is_annotated(node):
                    annotated += 1

    denominator = total_lines or 1
    return ReadabilityMetrics(
        comment_ratio=comment_lines / denominator,
        docstring_ratio=docstring_lines / denominator,
        annotated_function_ratio=(annotated / functions) if functions else 0.0,
        has_readme=any((root / name).exists() for name in README_NAMES),
        has_docs_dir=any((root / name).is_dir() for name in DOCS_DIRS),
    )
