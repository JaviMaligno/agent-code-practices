"""A3 — Formato (§4.1): «Líneas de hasta 400 caracteres, sin líneas en blanco,
sin espaciado alrededor de operadores, expresiones colapsadas».

Las cuatro partes están implementadas aquí: `_JoinContinuations` junta las
líneas de cada línea lógica (las «expresiones colapsadas», que es también de
donde salen las líneas largas), `MAX_LINE` pone el techo de 400, y
`_CrushFormatting` quita las líneas en blanco y el aire alrededor de los
operadores.

Lo que A3 NO hace, dicho aquí para que nadie lo lea como dosis aplicada:

* No encadena sentencias distintas con `;` ni mete el cuerpo de un compuesto en
  la línea de su cabecera. Eso no es formato: cambia qué sentencia vive en qué
  línea lógica, no cabe en los compuestos anidados (o sea que la dosis saldría
  desigual entre ficheros) y ningún repo sin formateador tiene esa pinta. El
  techo de 400 caracteres se lee como lo que es, un tope al colapso de
  expresiones, no un objetivo que haya que rellenar.
* No toca nada que lleve un comentario dentro: una expresión con un comentario
  entre sus paréntesis se queda repartida. Juntarla obligaría a tirar el
  comentario, que es A4, y con las dos celdas mezcladas ninguna de las dos es
  atribuible.
* No cambia la sangría, que en Python es sintaxis.
"""

from __future__ import annotations

from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files

_EMPTY = cst.SimpleWhitespace("")
_SPACE = cst.SimpleWhitespace(" ")

# Estos cuatro se escriben con palabras, así que su espacio es sintaxis igual
# que la sangría: `a in b` pegado sería el nombre `ainb`. LibCST ni siquiera
# deja construirlos así, y sin esta excepción A3 revienta en cualquier repo real.
KEYWORD_COMPARISONS = (cst.In, cst.NotIn, cst.Is, cst.IsNot)

# §4.1: «líneas de hasta 400 caracteres». Es un techo, no un objetivo: se juntan
# las continuaciones que quepan por debajo y se dejan repartidas las que no. Sin
# él, una tabla de datos de mil entradas —que las hay en los tres finalistas—
# saldría como una sola línea de decenas de miles de caracteres, y eso ya no es
# «un repo sin formateador» sino un fichero que ni el editor ni el agente saben
# leer: la celda mediría otra cosa distinta de la que el diseño le atribuye.
MAX_LINE = 400


def _crushed(whitespace: cst.BaseParenthesizableWhitespace) -> cst.BaseParenthesizableWhitespace:
    """Aplasta el espaciado, salvo el que no es solo espaciado.

    Un `a +  # la suma primero` guarda el comentario dentro del espacio que hay
    tras el operador, y lo mismo pasa con los saltos de línea dentro de
    paréntesis. Sustituirlo por vacío se llevaría el comentario por delante, que
    es A4 metida dentro de A3: con las dos mezcladas ninguna de las dos celdas
    del diseño es atribuible.
    """
    return _EMPTY if isinstance(whitespace, cst.SimpleWhitespace) else whitespace


def _carries_comment(whitespace: cst.ParenthesizedWhitespace) -> bool:
    """Si el salto de línea lleva un comentario colgando, juntar es borrarlo."""
    return whitespace.first_line.comment is not None or any(
        line.comment is not None for line in whitespace.empty_lines
    )


class _JoinContinuations(cst.CSTTransformer):
    """Convierte cada continuación de línea en un espacio.

    Hay dos formas de partir una línea lógica en Python y LibCST las guarda en
    sitios distintos: dentro de corchetes el salto es un `ParenthesizedWhitespace`,
    y con barra invertida es un `SimpleWhitespace` corriente que lleva un `\\`
    dentro. Tratar solo la primera dejaba intactas todas las continuaciones de
    los repos que no usan formateador, que son justo los que más las usan.

    El reemplazo es un espacio y nunca el vacío: en `a if b else c` repartido en
    cuatro renglones, o en un `del \\` o un `assert x, \\`, el salto de línea es
    el único separador que hay entre dos palabras, y pegarlas daría `aifbelsec`.
    El aire sobrante lo quita después `_CrushFormatting`, que sí sabe dónde
    puede.
    """

    def leave_ParenthesizedWhitespace(self, original, updated):
        if _carries_comment(updated):
            return updated
        return _SPACE

    def leave_SimpleWhitespace(self, original, updated):
        return _SPACE if "\\" in updated.value else updated


# Dónde puede vivir una continuación de línea, ranura a ranura. La clave es que
# el cuerpo de un compuesto NUNCA está en la lista: juntar la cabecera de un
# `if` con su cuerpo no es formato, es reescribir el programa. El salto que
# sigue al `(` de una firma tampoco vive dentro de `params`, sino en la ranura
# de al lado, y sin nombrarla la firma se quedaba partida por su primera línea.
_CONTINUABLE: dict[type[cst.CSTNode], tuple[str, ...]] = {
    cst.SimpleStatementLine: ("body",),
    cst.SimpleStatementSuite: ("body",),
    cst.FunctionDef: ("whitespace_before_params", "params", "returns"),
    cst.ClassDef: ("lpar", "bases", "keywords", "rpar"),
    cst.Decorator: ("decorator",),
    cst.If: ("test",),
    cst.While: ("test",),
    cst.For: ("target", "iter"),
    cst.With: ("items",),
    cst.ExceptHandler: ("type",),
}


def _joined(value):
    """La misma ranura con sus continuaciones juntadas."""
    if isinstance(value, cst.CSTNode):
        return value.visit(_JoinContinuations())
    if isinstance(value, (list, tuple)):
        return [element.visit(_JoinContinuations()) for element in value]
    # `None` y los `MaybeSentinel` (el paréntesis que la clase no llegó a
    # escribir) no tienen espacio que juntar.
    return value


def _rendered(module: cst.Module, node: cst.CSTNode) -> str:
    """El texto de un nodo suelto, o el de sus hijos si no sabe escribirse solo.

    Hay nodos cuyo símbolo depende de dónde cuelgan: una `Annotation` es `:` en
    un parámetro y `->` en un retorno, y LibCST se niega a escribirla fuera de
    su sitio. Como aquí solo se está midiendo un ancho, sus hijos valen: son los
    mismos caracteres menos el símbolo. Sin este rodeo, A3 no era que midiera
    mal, es que reventaba en cualquier fichero con la firma anotada —o sea, en
    su propia celda, que corre antes de que A1 quite los tipos—.
    """
    try:
        return module.code_for_node(node)
    except SyntaxError:  # CSTCodegenError, que hereda de SyntaxError
        return "\n".join(_rendered(module, child) for child in node.children)


def _widest(module: cst.Module, value) -> int:
    """La línea más larga que ocupa una ranura una vez escrita."""
    if isinstance(value, cst.CSTNode):
        nodes = [value]
    elif isinstance(value, (list, tuple)):
        nodes = list(value)
    else:
        return 0
    return max(
        (len(line) for node in nodes for line in _rendered(module, node).splitlines()),
        default=0,
    )


class _JoinLines(cst.CSTTransformer):
    """Junta las continuaciones de cada línea lógica que quepa en `MAX_LINE`.

    La decisión es por línea lógica y no por ranura: o se juntan todas las
    ranuras de la sentencia o ninguna, porque las ranuras de una cabecera se
    escriben seguidas en el mismo renglón y juntar media deja una cabecera con
    la mitad del aire y ninguna de las dos formas.

    El ancho se mide sumando lo que ocupan las ranuras juntadas más la sangría
    en la que van a caer, porque el techo es sobre el renglón que acaba en el
    fichero y la sangría de un método anidado son ya ocho de esos caracteres.
    Lo único que sigue sin contarse es la palabra clave de una cabecera (`if `,
    `with `), que son cuatro o cinco caracteres: el error que queda es por
    debajo y sobre un techo que es redondo por diseño.

    La comparación es contra el máximo entre `MAX_LINE` y lo que ya medía la
    sentencia, para no castigar a la que ya se salía del techo por sí sola —un
    literal de texto de mil caracteres— con algo que A3 no puede arreglar.
    """

    def __init__(self, module: cst.Module) -> None:
        super().__init__()
        self._module = module
        self._depth = 0

    def visit_IndentedBlock(self, node) -> bool:
        self._depth += 1
        return True

    def leave_IndentedBlock(self, original, updated):
        self._depth -= 1
        return updated

    def on_leave(self, original, updated):
        updated = super().on_leave(original, updated)
        if not isinstance(updated, cst.CSTNode):
            return updated
        slots = _CONTINUABLE.get(type(updated))
        if slots is None:
            return updated
        values = {slot: getattr(updated, slot) for slot in slots}
        candidates = {slot: _joined(value) for slot, value in values.items()}
        indent = self._depth * len(self._module.default_indent)
        room = max(MAX_LINE, max(_widest(self._module, value) for value in values.values()))
        if indent + sum(_widest(self._module, value) for value in candidates.values()) > room:
            return updated
        return updated.with_changes(**candidates)


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

    def leave_AssignTarget(self, original, updated):
        return updated.with_changes(
            whitespace_before_equal=_crushed(updated.whitespace_before_equal),
            whitespace_after_equal=_crushed(updated.whitespace_after_equal),
        )

    def leave_AugAssign(self, original, updated):
        return updated.with_changes(operator=_pinch(updated.operator))

    def leave_EmptyLine(self, original, updated):
        if updated.comment is not None:
            # Una línea con comentario no es una línea en blanco: borrarla sería
            # hacer A4, que es la transformación de al lado.
            return updated
        return cst.RemoveFromParent()

    # A partir de aquí, el aire pegado a un corchete o a una coma. Es el que
    # deja `_JoinContinuations` al juntar (`sum( values.values() )`), y sin
    # quitarlo la expresión colapsada sigue anunciando por dónde estaba partida
    # —que es la señal que A3 quiere borrar—. Junto a un corchete o una coma el
    # vacío siempre es legal: no hay dos palabras que se puedan pegar.

    def leave_LeftParen(self, original, updated):
        return updated.with_changes(whitespace_after=_crushed(updated.whitespace_after))

    def leave_RightParen(self, original, updated):
        return updated.with_changes(whitespace_before=_crushed(updated.whitespace_before))

    def leave_LeftSquareBracket(self, original, updated):
        return updated.with_changes(whitespace_after=_crushed(updated.whitespace_after))

    def leave_RightSquareBracket(self, original, updated):
        return updated.with_changes(whitespace_before=_crushed(updated.whitespace_before))

    def leave_LeftCurlyBrace(self, original, updated):
        return updated.with_changes(whitespace_after=_crushed(updated.whitespace_after))

    def leave_RightCurlyBrace(self, original, updated):
        return updated.with_changes(whitespace_before=_crushed(updated.whitespace_before))

    def leave_Comma(self, original, updated):
        return updated.with_changes(
            whitespace_before=_crushed(updated.whitespace_before),
            whitespace_after=_crushed(updated.whitespace_after),
        )

    def leave_Call(self, original, updated):
        return updated.with_changes(
            whitespace_after_func=_crushed(updated.whitespace_after_func),
            whitespace_before_args=_crushed(updated.whitespace_before_args),
        )

    def leave_Arg(self, original, updated):
        return updated.with_changes(whitespace_after_arg=_crushed(updated.whitespace_after_arg))

    def leave_Param(self, original, updated):
        return updated.with_changes(
            whitespace_after_param=_crushed(updated.whitespace_after_param),
        )

    def leave_Subscript(self, original, updated):
        return updated.with_changes(
            whitespace_after_value=_crushed(updated.whitespace_after_value),
        )

    def leave_FunctionDef(self, original, updated):
        return updated.with_changes(
            whitespace_before_params=_crushed(updated.whitespace_before_params),
        )


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
        # Primero juntar y después aplastar, y no al revés: el aire alrededor de
        # un operador partido en dos renglones no existe como espacio hasta que
        # la continuación se convierte en uno, y medir el techo de 400 antes de
        # aplastar solo puede pecar de conservador.
        transformed = module.visit(_JoinLines(module)).visit(_CrushFormatting()).code
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return TransformResult(files_changed=changed)
