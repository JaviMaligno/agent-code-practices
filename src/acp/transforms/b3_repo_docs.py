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

from acp.metrics.size import (
    EXCLUDED_DIR_PATTERN,
    EXCLUDED_FILE_PATTERN,
    read_source,
)
from acp.transforms.base import TransformResult, iter_transformable_files
from acp.transforms.docstrings import docstring_literal, only_doctests

# Se reconoce por el nombre sin extensión y sin mayúsculas, no por una lista
# cerrada: un repo que llame al suyo `Readme.markdown` se habría quedado con su
# README y la celda habría medido media dosis sin que nada lo cantara. Lo que
# lleva algo detrás —`readme-for-packagers.md`— no es el README del repo.
README_STEM = "readme"

# Mismo criterio que el README, por la misma razón: una lista cerrada de nombres
# exactos falla en silencio. `("docs", "doc")` en minúsculas dejaba entero el
# directorio de un repo que lo llamara `Doc/` —como CPython— y esa media dosis
# no la canta nadie. Los cuatro repos del sustrato lo escriben `docs`, así que
# esto no mueve ninguna condición medida; se hace porque la dosis que se pierde
# sin avisar es la que estropea la comparación entre celdas.
DOCS_NAMES = frozenset({"doc", "docs"})


def is_docs_directory(path: Path) -> bool:
    """Si ese directorio es la documentación del repo y no parte del programa.

    Un directorio con `__init__.py` es un paquete que alguien importa: es el
    mismo reparto que hace B4 al no confundir un paquete `testing` con la suite.
    Llevárselo no escondería documentación, cambiaría el programa, y la
    condición se leería como un agente que fracasa en vez de como la dosis que
    es (§4.3).
    """
    if path.name.lower() not in DOCS_NAMES:
        return False
    return not (path / "__init__.py").exists()


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


SUITE_DIRS = frozenset({"tests", "test", "testing"})


def _is_suite_file(relative: Path) -> bool:
    """Si el fichero es parte de la suite del repo.

    Reusa los patrones de `acp.metrics.size` en vez de escribir otra idea de qué
    es un test: si las dos definiciones se separan, B3 mide una cosa distinta de
    la que perfilan las métricas y nadie se entera.
    """
    if EXCLUDED_FILE_PATTERN.match(relative.stem):
        return True
    return any(
        part in SUITE_DIRS or EXCLUDED_DIR_PATTERN.match(part)
        for part in relative.parts[:-1]
    )


def suite_reads_the_readme(root: Path) -> bool:
    """Si algún fichero de la suite abre el README por su nombre.

    holidays comprueba en `tests/test_docs.py` que las tablas del README listan
    todos los países y mercados soportados: con el README vacío son tres tests
    menos (7558 → 7554 en la corrida real). Ahí el README no es documentación,
    es el contrato que la suite verifica.

    Se mira solo la suite, no el repo entero, y la diferencia es justo la que
    importa: el `setup.py` de python-stdnum también lee el README, pero para el
    long_description, y eso no cambia el resultado de ningún test. Con el
    criterio ancho —cualquiera que lo abra— B3 no le quitaría el README a
    ninguno de los cuatro repos del sustrato y la celda no mediría nada.

    Detecta el nombre escrito, no todas las formas de llegar al README: el
    cuarto test que holidays perdía, `tests/test_package.py::test_metadata`, lo
    lee sin nombrarlo —a través de la `description` que el empaquetado saca del
    fichero— y ahí se salvó porque otro test del mismo repo sí lo nombra. El
    guardarraíl que sí converge es la verificación de equivalencia por repo y
    condición, igual que con los diez casos que la fase 1 dejó abiertos.
    """
    for path in iter_transformable_files(root):
        if not _is_suite_file(path.relative_to(root)):
            continue
        try:
            tree = ast.parse(read_source(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and Path(node.value).stem.lower() == README_STEM
            ):
                return True
    return False


def apply(root: Path) -> TransformResult:
    changed = 0
    # Si la suite lee el README, el README es contrato y no se toca: ver
    # `suite_reads_the_readme`.
    readmes = (
        []
        if suite_reads_the_readme(root)
        else [
            path
            for path in sorted(root.iterdir())
            if path.is_file() and path.stem.lower() == README_STEM
        ]
    )
    for path in readmes:
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
    # Se recorre el directorio en vez de construir el nombre: `root / "doc"`
    # encuentra `Doc/` en el sistema de ficheros de macOS y no dentro del
    # contenedor Linux donde corren las condiciones, y una transformación que
    # depende de dónde se lanza no es una condición.
    for directory in sorted(root.iterdir()):
        if directory.is_dir() and is_docs_directory(directory):
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
