from __future__ import annotations

from typing import Callable

import libcst as cst

# El catálogo de fallos que §3.3.1 llama genéricos: se reconocen por patrón sin
# entender el código. Se generan programáticamente porque lo que define al
# estrato es la FORMA del fallo, no su contenido: escribirlos a mano metería
# criterio de dominio en el estrato que precisamente no debe tenerlo.
#
# Dos reglas comunes a todas las mutaciones de este módulo:
#
#   1. Cada mutación toca UN sitio, el primero que encaja. Una tarea es un fallo,
#      no una lluvia de fallos: si el parche invirtiera todas las comparaciones
#      de la función, la validación de §3.3 vería media suite en rojo y no
#      sabríamos si el agente arregló el fallo o sobrevivió al desastre.
#   2. Si el patrón no aparece, se devuelve `None`, no el fuente intacto.
#      Devolver el fuente haría creer al generador que hay tarea donde no la hay,
#      y el error saldría dos corridas de Docker más tarde.


def _code(node: cst.CSTNode) -> str:
    """El texto de un nodo suelto, para comparar dos subexpresiones."""
    return cst.Module(body=[]).code_for_node(node)


class _Scoped(cst.CSTTransformer):
    """Lleva la cuenta de dentro de qué definición vamos.

    El alcance importa tanto como la mutación: el parche tiene que tocar solo la
    función que la tarea nombra, porque el conjunto `fail_to_pass` se deduce de
    esa función. Una mutación que se cuela en la función de al lado rompe tests
    que la tarea no declara y la invalida (§3.3).

    El símbolo se nombra por su ruta desde el módulo (`funcion` o `Clase.metodo`)
    y se compara como PREFIJO de la pila: así una función anidada dentro de la
    función objetivo cuenta como dentro —es parte de su cuerpo— y un método
    homónimo de otra clase no.
    """

    def __init__(self, symbol: str) -> None:
        super().__init__()
        self.target = tuple(symbol.split("."))
        self.found = False
        self.applied = False
        self._stack: list[str] = []

    @property
    def _inside(self) -> bool:
        return (
            len(self._stack) >= len(self.target)
            and tuple(self._stack[: len(self.target)]) == self.target
        )

    def _push(self, name: str) -> None:
        self._stack.append(name)
        if tuple(self._stack) == self.target:
            self.found = True

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._push(node.name.value)
        return True

    def leave_FunctionDef(self, original_node, updated_node):  # noqa: ANN001
        self._stack.pop()
        return updated_node

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self._push(node.name.value)
        return True

    def leave_ClassDef(self, original_node, updated_node):  # noqa: ANN001
        self._stack.pop()
        return updated_node


# Los contrarios que cuenta `invert_condition`. `is` / `is not` NO están a
# propósito: invertir `x is None` es quitar la comprobación de nulo con otro
# nombre, y esa forma ya la cubre `drop_none_check`. Que dos entradas del
# catálogo fabricaran el mismo fallo estrecharía el reparto de formas sin que se
# notara al contarlas.
_OPPOSITE: dict[type, type] = {
    cst.GreaterThan: cst.LessThanEqual,
    cst.LessThan: cst.GreaterThanEqual,
    cst.GreaterThanEqual: cst.LessThan,
    cst.LessThanEqual: cst.GreaterThan,
    cst.Equal: cst.NotEqual,
    cst.NotEqual: cst.Equal,
}


class _InvertCondition(_Scoped):
    """Cambia un operador de comparación por su contrario."""

    def leave_Comparison(self, original_node, updated_node):  # noqa: ANN001
        if self.applied or not self._inside:
            return updated_node
        for index, part in enumerate(updated_node.comparisons):
            opposite = _OPPOSITE.get(type(part.operator))
            if opposite is None:
                continue
            # Se reconstruye el operador conservando su espacio: si la mutación
            # reformateara la línea, el parche dejaría de ser mínimo y el diff
            # que ve el agente contendría una pista que la tarea no quería dar.
            operator = opposite(
                whitespace_before=part.operator.whitespace_before,
                whitespace_after=part.operator.whitespace_after,
            )
            parts = list(updated_node.comparisons)
            parts[index] = part.with_changes(operator=operator)
            self.applied = True
            return updated_node.with_changes(comparisons=parts)
        return updated_node


class _OffByOne(_Scoped):
    """Suma uno a un literal entero.

    Se prefiere el literal que está en una comparación —es donde el off-by-one
    mueve un límite y por tanto donde cambia el comportamiento en el borde—, y
    esa preferencia la aplica `_off_by_one` corriendo esta clase dos veces.
    """

    def __init__(self, symbol: str, *, only_in_comparison: bool) -> None:
        super().__init__(symbol)
        self.only_in_comparison = only_in_comparison
        self._comparison_depth = 0

    def visit_Comparison(self, node: cst.Comparison) -> bool:
        self._comparison_depth += 1
        return True

    def leave_Comparison(self, original_node, updated_node):  # noqa: ANN001
        self._comparison_depth -= 1
        return updated_node

    def leave_Integer(self, original_node, updated_node):  # noqa: ANN001
        if self.applied or not self._inside:
            return updated_node
        if self.only_in_comparison and self._comparison_depth == 0:
            return updated_node
        try:
            value = int(updated_node.value, 0)
        except ValueError:  # pragma: no cover - literales que Python no acepta
            return updated_node
        self.applied = True
        # Siempre +1, nunca al azar: la tarea tiene que poder regenerarse igual
        # dos meses después para que el parche de referencia del oráculo (§5.4.6)
        # siga siendo el mismo parche.
        return updated_node.with_changes(value=str(value + 1))


def _names_none(test: cst.BaseExpression) -> bool:
    """La condición es una comprobación de nulo escrita como tal."""
    if not isinstance(test, cst.Comparison) or len(test.comparisons) != 1:
        return False
    part = test.comparisons[0]
    if not isinstance(part.operator, (cst.Is, cst.IsNot)):
        return False
    return isinstance(part.comparator, cst.Name) and part.comparator.value == "None"


def _is_guard(statement: cst.CSTNode, *, only_none: bool) -> bool:
    """Una guarda de salida temprana: `if <cond>: return ...` o `: raise ...`.

    Se exige que el cuerpo sea una salida —`return` o `raise`— y que no haya
    `else`: quitar una guarda así deja pasar al resto de la función lo que la
    comprobación filtraba, que es el fallo que se quiere inyectar. Quitar un `if`
    con `else`, o uno cuyo cuerpo siga ejecutando después, cambiaría el flujo
    entero y sería otro bug, mucho más ruidoso.

    `only_none` es la forma que nombra §3.3.1 literalmente, y por eso va primero.
    Pero escrita al pie de la letra esa forma no existe en el sustrato:
    python-stdnum entero tiene una aparición de `is None`, dentro de un doctest,
    mientras que guardas de salida temprana tiene 314. La comprobación que falta
    es la misma clase de fallo se escriba `if x is None` o `if not x.isdigit()`,
    y sin la segunda forma esta entrada del catálogo no se aplicaría ni una vez.
    """
    if not isinstance(statement, cst.If) or statement.orelse is not None:
        return False
    if only_none and not _names_none(statement.test):
        return False
    body = statement.body
    lines = body.body if isinstance(body, cst.IndentedBlock) else [body]
    for line in lines:
        if not isinstance(line, (cst.SimpleStatementLine, cst.SimpleStatementSuite)):
            return False
        if not all(isinstance(small, (cst.Return, cst.Raise)) for small in line.body):
            return False
    return True


class _DropNoneCheck(_Scoped):
    """Quita una comprobación que protegía al resto de la función.

    Se hace desde el bloque que la contiene y no desde el `if`, por dos razones:
    ahí se ve si la guarda era la única sentencia del cuerpo —quitarla dejaría un
    bloque vacío, que no compila— y ahí el borrado es un cambio local que no
    toca la indentación de lo que queda.
    """

    def __init__(self, symbol: str, *, only_none: bool) -> None:
        super().__init__(symbol)
        self.only_none = only_none

    def leave_IndentedBlock(self, original_node, updated_node):  # noqa: ANN001
        if self.applied or not self._inside:
            return updated_node
        if len(updated_node.body) < 2:
            return updated_node
        for index, statement in enumerate(updated_node.body):
            if _is_guard(statement, only_none=self.only_none):
                self.applied = True
                body = [*updated_node.body[:index], *updated_node.body[index + 1 :]]
                return updated_node.with_changes(body=body)
        return updated_node


class _SwapArgs(_Scoped):
    """Intercambia dos argumentos posicionales de una llamada."""

    def leave_Call(self, original_node, updated_node):  # noqa: ANN001
        if self.applied or not self._inside:
            return updated_node
        # Solo posicionales y sin `*`: cambiar el orden de dos argumentos con
        # nombre no cambia nada, y mover un desempaquetado cambia la aridad en
        # vez del significado.
        positions = [
            index
            for index, arg in enumerate(updated_node.args)
            if arg.keyword is None and arg.star == ""
        ]
        if len(positions) < 2:
            return updated_node
        first, second = positions[0], positions[1]
        left, right = updated_node.args[first], updated_node.args[second]
        # Dos argumentos idénticos —`max(x, x)`— intercambiados dan el mismo
        # programa: sería una tarea que no rompe nada, y se descubriría después
        # de dos corridas de suite en Docker.
        if _code(left.value) == _code(right.value):
            return updated_node
        args = list(updated_node.args)
        # Se intercambian los VALORES y no los `Arg`, para que la coma y el
        # espacio de cada posición se queden donde estaban.
        args[first] = left.with_changes(value=right.value)
        args[second] = right.with_changes(value=left.value)
        self.applied = True
        return updated_node.with_changes(args=args)


def _apply(mutation: _Scoped, module: cst.Module) -> cst.Module | None:
    mutated = module.visit(mutation)
    # Un símbolo que no está es un fallo del generador, no una forma que no
    # aplica. Devolver `None` en los dos casos los haría indistinguibles: el
    # generador leería un nombre mal escrito como "esta función no tiene
    # comparaciones", buscaría otra y nadie se enteraría de que la tarea que
    # quería fabricar nunca se intentó.
    if not mutation.found:
        raise LookupError(f"{'.'.join(mutation.target)!r} no está definido en el fuente")
    return mutated if mutation.applied else None


def _invert_condition(module: cst.Module, symbol: str) -> cst.Module | None:
    return _apply(_InvertCondition(symbol), module)


def _off_by_one(module: cst.Module, symbol: str) -> cst.Module | None:
    # Primero el límite de una comparación, y solo si no hay, cualquier otra
    # constante entera de la función. La segunda pasada no es un capricho: las
    # funciones aritméticas de los finalistas —`checksum` de python-stdnum es
    # `int(number) % 97`, sin una sola comparación— son donde el off-by-one es
    # más natural, y un catálogo que no aplicara ahí dejaría el estrato genérico
    # concentrado en las funciones con `if`, que son otra población.
    boundary = _apply(_OffByOne(symbol, only_in_comparison=True), module)
    if boundary is not None:
        return boundary
    return _apply(_OffByOne(symbol, only_in_comparison=False), module)


def _drop_none_check(module: cst.Module, symbol: str) -> cst.Module | None:
    # Primero la comprobación que nombra `None` y solo si no hay, cualquier otra
    # guarda de salida temprana: la primera es la forma canónica y la más limpia
    # de juzgar, la segunda es la que el sustrato tiene de verdad.
    named = _apply(_DropNoneCheck(symbol, only_none=True), module)
    if named is not None:
        return named
    return _apply(_DropNoneCheck(symbol, only_none=False), module)


def _swap_args(module: cst.Module, symbol: str) -> cst.Module | None:
    return _apply(_SwapArgs(symbol), module)


# El catálogo. Las claves son el vocabulario que las tareas guardan en su JSON,
# así que renombrar una entrada invalida las tareas ya generadas.
MUTATIONS: dict[str, Callable[[cst.Module, str], cst.Module | None]] = {
    "invert_condition": _invert_condition,
    "off_by_one": _off_by_one,
    "drop_none_check": _drop_none_check,
    "swap_args": _swap_args,
}


def mutate(source: str, symbol: str, kind: str) -> str | None:
    """El fuente con el fallo inyectado, o `None` si esa forma no aplica aquí."""
    if kind not in MUTATIONS:
        raise KeyError(f"{kind!r} no está en el catálogo {sorted(MUTATIONS)}")
    module = cst.parse_module(source)
    mutated = MUTATIONS[kind](module, symbol)
    if mutated is None:
        return None
    return mutated.code
