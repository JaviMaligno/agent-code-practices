from __future__ import annotations

import re
from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files

# Comentarios que no le hablan al lector sino a una herramienta: coverage, el
# linter, el type checker, el formateador. Quitarlos no quita una explicación,
# cambia lo que hace la cadena de herramientas del repo; en python-stdnum, con
# `fail_under = 100`, borrar sus 29 `# pragma: no cover` tira la suite entera
# sin que falle un test. La lista peca de conservadora a propósito: dejar de más
# cuesta un poco de dosis, dejar de menos cuesta la equivalencia (§4.3).
DIRECTIVE_COMMENT = re.compile(
    r"#\s*(noqa|type\s*:|pragma\s*:|pylint\s*:|mypy\s*:|pyright\s*:|flake8\s*:"
    r"|ruff\s*:|isort\s*:|fmt\s*:|yapf|nosec|nocov|coverage\s*:)",
    re.IGNORECASE,
)


def _is_directive(comment: cst.Comment | None) -> bool:
    return comment is not None and DIRECTIVE_COMMENT.match(comment.value) is not None


class _StripDocs(cst.CSTTransformer):
    """Quita comentarios y docstrings de función y de clase.

    La docstring de módulo se conserva a propósito: es B3 (dice qué hay en el
    fichero) y no A4 (dice cómo funciona lo que ya has abierto).

    Los bloques de doctest de una docstring también se conservan, y por una
    razón distinta: no son documentación, son suite. python-stdnum corre la
    suya con `--doctest-modules` y 413 de sus tests viven dentro de docstrings
    de función; borrarlas dejaría 410 y la equivalencia de §4.3 fallaría por
    construcción. Lo que A4 quita de esas docstrings es lo que quiere medir —la
    prosa que explica— y lo que deja es lo ejecutable.
    """

    def leave_Comment(self, original: cst.Comment, updated: cst.Comment):
        if _is_directive(updated):
            return updated
        return cst.RemoveFromParent()

    def leave_TrailingWhitespace(self, original, updated):
        # `updated.comment`, no `original.comment`: si el comentario se conservó
        # sigue ahí, y pegarlo al código (`import os# noqa`) es un cambio de
        # formato que el linter del repo canta como error.
        if updated.comment is not None or original.comment is None:
            return updated
        # El comentario ya se fue, pero los espacios que lo separaban del código
        # se quedan al final de la línea. Trailing whitespace es formato (A3):
        # dentro de A4 el efecto medido dejaría de ser atribuible, y en un repo
        # cuya suite pasa el linter rompe la equivalencia.
        return updated.with_changes(whitespace=cst.SimpleWhitespace(""))

    def leave_EmptyLine(self, original, updated):
        # Igual que arriba: una directiva en su propia línea conserva su sangría,
        # porque `# fmt: off` o `# type: ignore` sueltos se leen por posición.
        if updated.comment is not None or original.comment is None:
            return updated
        # Una línea de comentario se queda en blanco en vez de desaparecer, para
        # no desplazar los rangos de línea del mapa de símbolos. En blanco de
        # verdad: sin la sangría del bloque colgando detrás.
        return updated.with_changes(indent=False, whitespace=cst.SimpleWhitespace(""))

    def leave_FunctionDef(self, original, updated):
        return updated.with_changes(body=_without_docstring(updated.body))

    def leave_ClassDef(self, original, updated):
        return updated.with_changes(body=_without_docstring(updated.body))


def _without_docstring(body: cst.BaseSuite) -> cst.BaseSuite:
    if not isinstance(body, cst.IndentedBlock) or not body.body:
        return body
    first = body.body[0]
    literal = _docstring_literal(first)
    if literal is None:
        return body
    kept = _only_doctests(literal)
    if kept is not None:
        # Quedaban ejemplos: la docstring sigue ahí, sin la prosa.
        statement = first.with_changes(
            body=[first.body[0].with_changes(value=kept)]  # type: ignore[union-attr]
        )
        return body.with_changes(body=[statement, *body.body[1:]])
    remaining = list(body.body[1:])
    # Un cuerpo vacío no compila: si la docstring era todo, hace falta un `pass`.
    if not remaining:
        remaining = [cst.SimpleStatementLine(body=[cst.Pass()])]
    return body.with_changes(body=remaining)


def _docstring_literal(statement: cst.BaseStatement) -> cst.SimpleStatementLine | None:
    """La sentencia si es una docstring, o None.

    Se devuelve la sentencia entera y no la cadena porque quien llama necesita
    reemplazarla dentro del bloque conservando su posición.
    """
    if (
        isinstance(statement, cst.SimpleStatementLine)
        and len(statement.body) == 1
        and isinstance(statement.body[0], cst.Expr)
        and isinstance(statement.body[0].value, cst.SimpleString)
    ):
        return statement
    return None


DOCTEST_PROMPT = ">>>"


def _is_example_start(line: str) -> bool:
    """Si la línea abre un ejemplo de doctest.

    Misma regla de prompt que `a2_names._prompt`: `>>>x` no es un prompt porque
    doctest exige el espacio, o nada detrás. Allí sirve para saber qué trozo del
    ejemplo es código renombrable; aquí, para saber dónde empieza lo que no se
    puede borrar.
    """
    stripped = line.lstrip(" ")
    if not stripped.startswith(DOCTEST_PROMPT):
        return False
    rest = stripped[len(DOCTEST_PROMPT) :]
    return not rest or rest.startswith(" ")


def _example_blocks(lines: list[str]) -> list[list[str]]:
    """Los ejemplos del texto: el `>>>`, sus continuaciones y su salida esperada.

    Un ejemplo llega hasta la primera línea en blanco o hasta el final, que es
    exactamente donde doctest da por terminada la salida esperada. Por eso la
    prosa pegada bajo un resultado se conserva: para doctest no es prosa, es
    parte de lo que el ejemplo espera, y quitarla haría fallar el test.
    """
    blocks: list[list[str]] = []
    index = 0
    while index < len(lines):
        if not _is_example_start(lines[index]):
            index += 1
            continue
        block = []
        while index < len(lines) and lines[index].strip():
            block.append(lines[index])
            index += 1
        blocks.append(block)
    return blocks


def _only_doctests(statement: cst.SimpleStatementLine) -> cst.SimpleString | None:
    """La docstring recortada a sus ejemplos, o None si no tenía ninguno.

    None significa «bórrala entera», que es el caso normal de A4. Cuando quedan
    ejemplos se reconstruye el literal con el mismo prefijo y las mismas
    comillas: la sangría original de cada línea viaja intacta porque doctest
    compara la salida esperada contra el margen del `>>>`.
    """
    original = statement.body[0].value  # type: ignore[union-attr]
    assert isinstance(original, cst.SimpleString)
    lines = original.raw_value.split("\n")
    blocks = _example_blocks(lines)
    if not blocks:
        return None
    # Un ejemplo pegado a las comillas de apertura no tiene sangría propia:
    # moverlo de línea cambiaría el margen contra el que doctest compara la
    # salida. Es rarísimo y no vale el riesgo, así que esa docstring se queda
    # como está.
    if _is_example_start(lines[0]):
        return original
    # La última línea en blanco es la sangría de las comillas de cierre.
    closing = lines[-1] if lines and not lines[-1].strip() else ""
    rebuilt = "\n" + "\n\n".join("\n".join(block) for block in blocks) + "\n" + closing
    if rebuilt == original.raw_value:
        return original
    return original.with_changes(
        value=f"{original.prefix}{original.quote}{rebuilt}{original.quote}"
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
