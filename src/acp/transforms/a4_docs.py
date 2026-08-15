from __future__ import annotations

from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files


class _StripDocs(cst.CSTTransformer):
    """Quita comentarios y docstrings de función y de clase.

    La docstring de módulo se conserva a propósito: es B3 (dice qué hay en el
    fichero) y no A4 (dice cómo funciona lo que ya has abierto).
    """

    def leave_Comment(self, original: cst.Comment, updated: cst.Comment) -> cst.RemovalSentinel:
        return cst.RemoveFromParent()

    def leave_FunctionDef(self, original, updated):
        return updated.with_changes(body=_without_docstring(updated.body))

    def leave_ClassDef(self, original, updated):
        return updated.with_changes(body=_without_docstring(updated.body))


def _without_docstring(body: cst.BaseSuite) -> cst.BaseSuite:
    if not isinstance(body, cst.IndentedBlock) or not body.body:
        return body
    first = body.body[0]
    if not _is_docstring(first):
        return body
    remaining = list(body.body[1:])
    # Un cuerpo vacío no compila: si la docstring era todo, hace falta un `pass`.
    if not remaining:
        remaining = [cst.SimpleStatementLine(body=[cst.Pass()])]
    return body.with_changes(body=remaining)


def _is_docstring(statement: cst.BaseStatement) -> bool:
    return (
        isinstance(statement, cst.SimpleStatementLine)
        and len(statement.body) == 1
        and isinstance(statement.body[0], cst.Expr)
        and isinstance(statement.body[0].value, cst.SimpleString)
    )


def apply(root: Path) -> TransformResult:
    changed = 0
    for path in iter_transformable_files(root):
        source = read_source(path)
        try:
            module = cst.parse_module(source)
        except cst.ParserSyntaxError:
            continue
        transformed = module.visit(_StripDocs()).code
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return TransformResult(files_changed=changed)
