from __future__ import annotations

from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files


class _StripTypes(cst.CSTTransformer):
    def __init__(self) -> None:
        # Hace falta saber si un AnnAssign cuelga directamente del cuerpo de una
        # clase, y el visitante no da el padre. La pila lleva el ámbito abierto.
        self._scopes: list[str] = []

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self._scopes.append("class")

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._scopes.append("function")

    def leave_ClassDef(self, original, updated):
        self._scopes.pop()
        return updated

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
        self._scopes.pop()
        return updated.with_changes(returns=None)

    def leave_AnnAssign(self, original: cst.AnnAssign, updated: cst.AnnAssign):
        if self._scopes[-1:] == ["class"]:
            # En el cuerpo de una clase la anotación no describe el atributo: lo
            # declara. En un dataclass, un NamedTuple, un TypedDict o un modelo
            # de pydantic, quitarla borra el campo y el repo deja de construir
            # sus objetos —un repo roto se lee igual que un agente que fracasa
            # (§4.3)—. Saber qué clases son de esas exige resolver decoradores y
            # bases por todo el repo; dejarlas todas cuesta poca dosis: en los
            # tres finalistas los parámetros y retornos son >90% de las
            # anotaciones y esto es como mucho el 6% (sqlglot).
            return updated
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
