from __future__ import annotations

from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files


def _registers_by_annotation(node: cst.FunctionDef) -> bool:
    """`@x.register` sin llamar: la anotación es el selector del despacho.

    `functools.singledispatch` y `singledispatchmethod` eligen la implementación
    leyendo `get_type_hints(func)` cuando el decorador se usa desnudo, así que
    ahí la anotación no documenta el parámetro: lo despacha. `@x.register(int)`
    nombra la clase aparte y por eso no entra: en esa forma la anotación vuelve a
    ser documentación y quitarla no cambia el comportamiento.

    Cualquier otro registro con un método `register` usado como decorador desnudo
    también cae aquí. Es un falso positivo deliberado: cuesta una anotación por
    registro y evita tener que resolver de qué objeto cuelga `register` por todo
    el repo, que es justo lo que no se puede hacer estáticamente.
    """
    for decorator in node.decorators:
        expression = decorator.decorator
        if isinstance(expression, cst.Attribute) and expression.attr.value == "register":
            return True
    return False


def _dispatch_selector(node: cst.FunctionDef) -> cst.Param | None:
    """El parámetro cuya anotación lee `register()`, o None si no hay ninguno.

    `register()` hace `next(iter(get_type_hints(func).items()))`, es decir se
    queda con la PRIMERA anotación en orden de declaración, no con el primer
    parámetro. En un método registrado por `singledispatchmethod` el primero es
    `self`, que no se anota, y el selector es el siguiente.
    """
    params = node.params
    ordered = [*params.posonly_params, *params.params]
    if isinstance(params.star_arg, cst.Param):
        ordered.append(params.star_arg)
    ordered.extend(params.kwonly_params)
    if isinstance(params.star_kwarg, cst.Param):
        ordered.append(params.star_kwarg)
    for param in ordered:
        if param.annotation is not None:
            return param
    return None


class _StripTypes(cst.CSTTransformer):
    def __init__(self) -> None:
        # Hace falta saber si un AnnAssign cuelga directamente del cuerpo de una
        # clase, y el visitante no da el padre. La pila lleva el ámbito abierto.
        self._scopes: list[str] = []
        # Una entrada por función abierta: el parámetro que despacha (o None) y
        # si hay que conservar el retorno. Es una pila porque una función anidada
        # dentro de una registrada no hereda la protección.
        self._selectors: list[cst.Param | None] = []
        self._keep_returns: list[bool] = []

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self._scopes.append("class")

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._scopes.append("function")
        dispatched = _registers_by_annotation(node)
        selector = _dispatch_selector(node) if dispatched else None
        self._selectors.append(selector)
        # Sin ningún parámetro anotado, lo primero que `get_type_hints` devuelve
        # es el retorno, y entonces es el retorno lo que despacha.
        self._keep_returns.append(dispatched and selector is None)

    def leave_ClassDef(self, original, updated):
        self._scopes.pop()
        return updated

    def leave_Param(self, original: cst.Param, updated: cst.Param) -> cst.Param:
        if updated.annotation is None:
            return updated
        # Identidad contra el nodo original, no comparación por nombre: la
        # protección es de ESE parámetro de ESA función, y el mismo nombre se
        # repite por todo el fichero.
        if self._selectors and self._selectors[-1] is original:
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
        self._selectors.pop()
        if self._keep_returns.pop():
            return updated
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
