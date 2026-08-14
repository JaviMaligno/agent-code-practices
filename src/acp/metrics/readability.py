from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

from acp.metrics.size import iter_source_files
from acp.models import ReadabilityMetrics

README_NAMES = ("README.md", "README.rst", "README.txt", "README")
DOCS_DIRS = ("docs", "doc")
# Nunca se anotan, y exigirlo dejaría todo método como no anotado.
SELF_NAMES = {"self", "cls"}


def _is_annotated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Anotada de verdad: el retorno y todos los parámetros.

    Con `any` bastaba un argumento anotado para dar la función por tipada, y el
    ratio medía presencia de anotaciones en el repo, no cobertura — que es lo
    que decide cuánto puede quitar A1.
    """
    if node.returns is None:
        return False
    args = node.args
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg:
        every.append(args.vararg)
    if args.kwarg:
        every.append(args.kwarg)
    return all(
        arg.annotation is not None for arg in every if arg.arg not in SELF_NAMES
    )


def _comment_lines(text: str) -> int:
    """Líneas que llevan comentario, esté al principio o al final.

    Se tokeniza en vez de mirar el principio de la línea porque los dos errores
    que eso comete van en direcciones opuestas: pierde los comentarios de final
    de línea y se inventa comentarios donde solo hay una almohadilla dentro de
    una cadena.
    """
    rows: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                rows.add(token.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return sum(1 for line in text.splitlines() if line.strip().startswith("#"))
    return len(rows)


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
        comment_lines += _comment_lines(text)

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
