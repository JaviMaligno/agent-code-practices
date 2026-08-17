"""B3 — documentación de repo: lo que te dice qué fichero abrir.

El reparto con A4 es deliberado (§4.2 del spec): la docstring de función se lee
cuando ya has abierto el fichero correcto, el README y la docstring de módulo te
dicen cuál abrir. Si la tesis del experimento es cierta, las dos cosas tienen
que comportarse distinto, y ese contraste solo se puede leer si cada una está en
una celda distinta.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files
from acp.transforms.docstrings import docstring_literal, only_doctests

# Se reconoce por el nombre sin extensión y sin mayúsculas, no por una lista
# cerrada: un repo que llame al suyo `Readme.markdown` se habría quedado con su
# README y la celda habría medido media dosis sin que nada lo cantara. Lo que
# lleva algo detrás —`readme-for-packagers.md`— no es el README del repo.
README_STEM = "readme"
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


# Funciones cuyo único propósito es leer una docstring y devolverla como dato.
# No se busca `__doc__` a secas en forma de atributo: `cls.__doc__` —que es lo
# que hacen los scripts de l10n de holidays— habla de una clase, y eso es A4.
DOCSTRING_READERS = frozenset({"getdoc", "splitdoc", "render_doc"})


def reads_module_docstrings(root: Path) -> bool:
    """Si el repo lee docstrings por API, en vez de solo escribirlas.

    `stdnum.util.get_module_name()` hace `pydoc.splitdoc(pydoc.getdoc(module))`
    sobre cada módulo de número y publica el resultado en la aplicación web; su
    suite lo comprueba módulo a módulo. Ahí la docstring de módulo no es
    documentación, es un dato del programa, y borrarla cambia lo que el programa
    devuelve: la equivalencia de §4.3 se cae, y una condición que no se instala
    o no pasa su suite se lee igual que un agente que fracasa.

    Es la misma línea que A4 traza con los doctests —lo que el programa ejecuta
    o lee no es documentación— y se aplica al repo entero, no fichero a fichero,
    porque el módulo cuya docstring se lee se resuelve en ejecución
    (`get_number_modules()` recorre el paquete) y no hay forma estática de saber
    a cuál le toca. Se paga en dosis, nunca en equivalencia; el precio real de
    cada repo se declara en los resultados.
    """
    for path in iter_transformable_files(root):
        try:
            tree = ast.parse(read_source(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in DOCSTRING_READERS:
                return True
            if isinstance(node, ast.Name) and node.id in DOCSTRING_READERS:
                return True
    return False


def _reads_its_own_docstring(source: str) -> bool:
    """Si el módulo usa su propio `__doc__` como dato.

    `tests/leakcheck.py` de sqlglot abre su argparse con
    `description=__doc__.splitlines()[0]`: sin docstring eso es un AttributeError
    sobre None. Aquí sí se ve fichero a fichero —`__doc__` a secas solo puede ser
    el del módulo que lo escribe—, así que la excepción cuesta esa docstring y
    ninguna más.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.Name) and node.id == "__doc__" for node in ast.walk(tree)
    )


def apply(root: Path) -> TransformResult:
    changed = 0
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.stem.lower() != README_STEM:
            continue
        # Vaciado, no borrado. El empaquetado de los cuatro repos del sustrato
        # lee el README —python-stdnum lo abre a mano en su `setup.py`, pint,
        # sqlglot y holidays lo declaran en el `pyproject`—, y quitarle el
        # fichero a python-stdnum revienta la construcción con FileNotFoundError
        # antes de que pueda ni declarar su versión. Un árbol que no se instala
        # se lee igual que un agente que fracasa (§4.3). Vaciarlo quita lo mismo
        # —no queda nada que leer— sin tocar lo que la construcción espera, y
        # deja la dosis idéntica en repos con empaquetados distintos.
        if path.read_bytes():
            path.write_text("", encoding="utf-8")
            changed += 1
    for name in DOCS_DIRS:
        directory = root / name
        if directory.is_dir():
            shutil.rmtree(directory)
            changed += 1

    if reads_module_docstrings(root):
        # La dosis de este repo es README + `docs/`, y se declara. Ver la razón
        # en `reads_module_docstrings`.
        return TransformResult(files_changed=changed)

    for path in iter_transformable_files(root):
        source = read_source(path)
        if _reads_its_own_docstring(source):
            continue
        try:
            module = cst.parse_module(source)
        except cst.ParserSyntaxError:
            continue
        transformed = module.visit(_StripModuleDocstring()).code
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return TransformResult(files_changed=changed)
