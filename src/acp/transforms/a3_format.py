from __future__ import annotations

from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files

_EMPTY = cst.SimpleWhitespace("")

# Estos cuatro se escriben con palabras, así que su espacio es sintaxis igual
# que la sangría: `a in b` pegado sería el nombre `ainb`. LibCST ni siquiera
# deja construirlos así, y sin esta excepción A3 revienta en cualquier repo real.
KEYWORD_COMPARISONS = (cst.In, cst.NotIn, cst.Is, cst.IsNot)


def _crushed(whitespace: cst.BaseParenthesizableWhitespace) -> cst.BaseParenthesizableWhitespace:
    """Aplasta el espaciado, salvo el que no es solo espaciado.

    Un `a +  # la suma primero` guarda el comentario dentro del espacio que hay
    tras el operador, y lo mismo pasa con los saltos de línea dentro de
    paréntesis. Sustituirlo por vacío se llevaría el comentario por delante, que
    es A4 metida dentro de A3: con las dos mezcladas ninguna de las dos celdas
    del diseño es atribuible.
    """
    return _EMPTY if isinstance(whitespace, cst.SimpleWhitespace) else whitespace


class _CrushFormatting(cst.CSTTransformer):
    """Quita el espaciado que no es sintaxis.

    Se hace sobre el árbol y no con expresiones regulares porque el espaciado
    dentro de una cadena sí es significativo: varios finalistas comparan
    mensajes literales en sus tests.
    """

    def leave_BinaryOperation(self, original, updated):
        return updated.with_changes(operator=_pinch(updated.operator))

    def leave_Comparison(self, original, updated):
        return updated.with_changes(
            comparisons=[
                target
                if isinstance(target.operator, KEYWORD_COMPARISONS)
                else target.with_changes(operator=_pinch(target.operator))
                for target in updated.comparisons
            ]
        )

    def leave_EmptyLine(self, original, updated):
        if updated.comment is not None:
            # Una línea con comentario no es una línea en blanco: borrarla sería
            # hacer A4, que es la transformación de al lado.
            return updated
        return cst.RemoveFromParent()


def _pinch(operator):
    """Deja un operador sin aire a los lados."""
    return operator.with_changes(
        whitespace_before=_crushed(operator.whitespace_before),
        whitespace_after=_crushed(operator.whitespace_after),
    )


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
