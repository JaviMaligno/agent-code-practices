"""B3 — documentación de repo: lo que te dice qué fichero abrir.

El reparto con A4 es deliberado (§4.2 del spec): la docstring de función se lee
cuando ya has abierto el fichero correcto, el README y la docstring de módulo te
dicen cuál abrir. Si la tesis del experimento es cierta, las dos cosas tienen
que comportarse distinto, y ese contraste solo se puede leer si cada una está en
una celda distinta.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files
from acp.transforms.docstrings import docstring_literal, only_doctests

README_NAMES = ("README.md", "README.rst", "README.txt", "README")
DOCS_DIRS = ("docs", "doc")


class _StripModuleDocstring(cst.CSTTransformer):
    """Quita la docstring de módulo y nada más.

    Es lo que dice qué hay en el fichero, o sea qué fichero abrir: por eso es B3
    y no A4. Conserva los bloques de doctest por la misma razón que A4 — en
    python-stdnum son media suite, y borrarlos haría fallar la equivalencia de
    §4.3 por construcción.
    """

    def leave_Module(self, original: cst.Module, updated: cst.Module) -> cst.Module:
        if not updated.body:
            return updated
        literal = docstring_literal(updated.body[0])
        if literal is None:
            return updated
        kept = only_doctests(literal)
        remaining = list(updated.body[1:])
        if kept is None:
            return updated.with_changes(body=remaining)
        return updated.with_changes(
            body=[literal.with_changes(body=[cst.Expr(value=kept)]), *remaining]
        )


def apply(root: Path) -> TransformResult:
    changed = 0
    for name in README_NAMES:
        path = root / name
        # Vaciado, no borrado. El empaquetado de los cuatro repos del sustrato
        # lee el README —python-stdnum lo abre a mano en su `setup.py`, pint,
        # sqlglot y holidays lo declaran en el `pyproject`—, y quitarle el
        # fichero a python-stdnum revienta la construcción con FileNotFoundError
        # antes de que pueda ni declarar su versión. Un árbol que no se instala
        # se lee igual que un agente que fracasa (§4.3). Vaciarlo quita lo mismo
        # —no queda nada que leer— sin tocar lo que la construcción espera, y
        # deja la dosis idéntica en repos con empaquetados distintos.
        if path.is_file() and path.read_bytes():
            path.write_text("", encoding="utf-8")
            changed += 1
    for name in DOCS_DIRS:
        directory = root / name
        if directory.is_dir():
            shutil.rmtree(directory)
            changed += 1

    for path in iter_transformable_files(root):
        source = read_source(path)
        try:
            module = cst.parse_module(source)
        except cst.ParserSyntaxError:
            continue
        transformed = module.visit(_StripModuleDocstring()).code
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return TransformResult(files_changed=changed)
