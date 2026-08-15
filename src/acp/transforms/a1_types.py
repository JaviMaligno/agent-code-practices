from __future__ import annotations

from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files


class _StripTypes(cst.CSTTransformer):
    def leave_Param(self, original: cst.Param, updated: cst.Param) -> cst.Param:
        if updated.annotation is None:
            return updated
        # Sin anotación, PEP 8 escribe `factor=1.0`: dejar los espacios sería
        # meter un cambio de formato (A3) dentro de A1.
        equal = updated.equal
        if isinstance(equal, cst.AssignEqual):
            equal = equal.with_changes(
                whitespace_before=cst.SimpleWhitespace(""),
                whitespace_after=cst.SimpleWhitespace(""),
            )
        return updated.with_changes(annotation=None, equal=equal)

    def leave_FunctionDef(self, original, updated):
        return updated.with_changes(returns=None)

    def leave_AnnAssign(self, original: cst.AnnAssign, updated: cst.AnnAssign):
        if updated.value is None:
            # `x: int` sin valor no crea nombre en ejecución: quitarlo entero es
            # lo único equivalente. Dejar `x` daría NameError.
            return cst.RemoveFromParent()
        return cst.Assign(
            targets=[cst.AssignTarget(target=updated.target)],
            value=updated.value,
        )


def apply(root: Path) -> TransformResult:
    changed = 0
    for path in iter_transformable_files(root):
        source = read_source(path)
        try:
            module = cst.parse_module(source)
        except cst.ParserSyntaxError:
            continue
        transformed = module.visit(_StripTypes()).code
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return TransformResult(files_changed=changed)
