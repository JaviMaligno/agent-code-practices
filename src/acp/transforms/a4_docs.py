from __future__ import annotations

import re
from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files
from acp.transforms.docstrings import docstring_literal, only_doctests

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



# El shebang y la cookie de codificación (PEP 263) son comentarios para el
# sistema y para el propio parser: sin el primero un script deja de ser
# ejecutable, y sin la segunda el intérprete decodifica el fichero con otra
# codificación. Ninguna de las dos explica nada a quien lee. Se reconocen por
# forma y no por posición (solo cuentan en las dos primeras líneas): confundir
# una prosa rara con una cookie cuesta un comentario de dosis; equivocarse al
# revés cuesta el fichero.
SHEBANG = re.compile(r"#!")
ENCODING_COOKIE = re.compile(r"#.*?coding[:=][ \t]*[-_.a-zA-Z0-9]+")


def _is_directive(comment: cst.Comment | None) -> bool:
    if comment is None:
        return False
    return any(
        pattern.match(comment.value) is not None
        for pattern in (DIRECTIVE_COMMENT, SHEBANG, ENCODING_COOKIE)
    )


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
    literal = docstring_literal(first)
    if literal is None:
        return body
    kept = only_doctests(literal)
    if kept is not None:
        # Quedaban ejemplos: la docstring sigue ahí, sin la prosa.
        statement = first.with_changes(
            body=[first.body[0].with_changes(value=kept)]  # type: ignore[union-attr]
        )
        return body.with_changes(body=[statement, *body.body[1:]])
    # Si la docstring era todo el cuerpo, el bloque se queda vacío y `def f():`
    # sin cuerpo no compila. No hace falta insertar un `Pass()`: libcst escribe
    # `pass` al renderizar un IndentedBlock vacío (comprobado con comentario de
    # cabecera, de cierre, en clase y anidado). Lo que queda fijado es el
    # comportamiento —el fichero se ejecuta— y no cómo se escribe.
    return body.with_changes(body=list(body.body[1:]))


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
