from __future__ import annotations

from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files

_EMPTY = cst.SimpleWhitespace("")


class _CrushFormatting(cst.CSTTransformer):
    """Quita el espaciado que no es sintaxis.

    Se hace sobre el árbol y no con expresiones regulares porque el espaciado
    dentro de una cadena sí es significativo: varios finalistas comparan
    mensajes literales en sus tests.
    """

    def leave_BinaryOperation(self, original, updated):
        return updated.with_changes(
            operator=updated.operator.with_changes(
                whitespace_before=_EMPTY, whitespace_after=_EMPTY
            )
        )

    def leave_Comparison(self, original, updated):
        return updated.with_changes(
            comparisons=[
                target.with_changes(
                    operator=target.operator.with_changes(
                        whitespace_before=_EMPTY, whitespace_after=_EMPTY
                    )
                )
                for target in updated.comparisons
            ]
        )

    def leave_EmptyLine(self, original, updated) -> cst.RemovalSentinel:
        return cst.RemoveFromParent()


def apply(root: Path) -> TransformResult:
    changed = 0
    for path in iter_transformable_files(root):
        source = read_source(path)
        try:
            module = cst.parse_module(source)
        except cst.ParserSyntaxError:
            continue
        transformed = module.visit(_CrushFormatting()).code
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return TransformResult(files_changed=changed)
