from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path


@dataclass
class TransformResult:
    """Lo que una transformación cambió.

    `renames` viaja con el resultado porque el enunciado de la tarea se
    transforma con el mismo diccionario (§4.3.2 del spec): un enunciado que
    habla de `get_queryset` sobre un código donde eso se llama `f7` mide otra
    cosa.
    """

    files_changed: int = 0
    renames: dict[str, str] = field(default_factory=dict)
    # Módulo original → módulo destino. Viaja con el resultado por la misma
    # razón que `renames`: el mapa de identidad tiene que poder seguir dónde
    # acabó cada símbolo, y solo la transformación sabe qué movió.
    moves: dict[str, str] = field(default_factory=dict)
    # Símbolo → módulo destino, para las transformaciones que mueven
    # definiciones sueltas y no ficheros enteros: dos símbolos del mismo módulo
    # pueden acabar en sitios distintos, y `moves` no sabe expresar eso —diría
    # que los dos se fueron al mismo sitio, o los dejaría caer del manifiesto—.
    # La clave es el nombre cualificado ORIGINAL del símbolo, la misma que usa
    # el mapa de identidad, porque es la única que sobrevive a A2.
    symbol_moves: dict[str, str] = field(default_factory=dict)


# Lo que no entra en la copia. No es higiene: cada uno de estos artefactos
# REINTRODUCE en el árbol justo lo que una condición acaba de quitar, y lo hace
# en un sitio donde ninguna transformación vuelve a mirar.
#   - `.pytest_cache/v/cache/nodeids` lista los IDs de la suite que B4 se lleva
#     fuera, y `*.egg-info/SOURCES.txt` sus rutas: dos punteros a los tests
#     escondidos dentro del árbol que el agente explora.
#   - `__pycache__` conserva el árbol de módulos con los nombres previos a B2,
#     y además mantiene vivos directorios que B2 querría vaciar.
#   - `build/lib/**`, `.tox`, `.nox`, `.venv` y `.eggs` guardan una copia
#     literal del fuente instalado: el original entero, sin transformar.
#   - `.acp-*` y `*.acp-tests` son del propio pipeline; hoy viven fuera del
#     árbol, pero copiarlos si alguna vez cayeran dentro sería enseñar el
#     experimento —la misma fuga que ya tapó `_reject_manifest_inside_the_tree`—.
#   - `coverage.xml`, `coverage.json`, `coverage.lcov` y los `*.py,cover` son
#     los otros formatos del informe que `_is_coverage_report` solo tapaba en
#     HTML: cada uno lista los ficheros del repo por su ruta completa, así que
#     dentro del árbol aplanado republican la jerarquía que B2 acaba de
#     destruir y en el árbol sin suite nombran los tests que B4 se llevó. El
#     XML es además el formato que escriben los repos en CI, o sea que llega en
#     el clon sin que la campaña corra nada.
# Se filtra en la copia, que es por donde entran todos: `iter_transformable_files`
# los salta, pero saltarlos al transformar solo garantiza que llegan intactos.
NOT_COPYABLE = frozenset({
    ".git",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".pytype", ".hypothesis",
    ".tox", ".nox", ".eggs", ".venv", "venv", "node_modules",
    "build", "dist", ".coverage",
    "coverage.xml", "coverage.json", "coverage.lcov",
})

# Lo mismo, cuando lo que delata es la forma del nombre y no el nombre exacto.
# `.coverage.*` son los ficheros de coverage en paralelo; `.coveragerc` NO cae
# aquí a propósito: es configuración del repositorio y el agente la lee.
# `*,cover` es lo que deja `coverage annotate`: el fuente entero anotado línea
# a línea, junto a cada fichero y con su nombre.
NOT_COPYABLE_PATTERNS = (
    "*.egg-info", "*.pyc", "*.pyo", ".coverage.*", "*,cover", ".acp-*", "*.acp-tests",
)

# Artefactos y dependencias ajenas. Es lo que no se copia más el código
# vendorizado, que sí viaja —el repo lo importa— pero no se transforma. No
# incluye los directorios de test: esos sí se transforman (§4.3.1), al revés
# que en las métricas de la fase 0.
NOT_TRANSFORMABLE = NOT_COPYABLE | {"site-packages", "vendor", "third_party"}


def _is_coverage_report(path: Path) -> bool:
    """Un informe HTML de coverage, se llame como se llame.

    Empotra el fuente entero en HTML —nombres y docstrings originales, uno por
    módulo—, así que es una fuga de las gordas; pero cada repo bautiza la
    carpeta a su gusto (`htmlcov`, `coverage`, `cov_html`) y excluir el nombre
    `coverage` a secas se llevaría por delante un paquete legítimo. Lo que sí
    es inequívoco es el par que escribe coverage y nadie más.
    """
    return (path / "status.json").is_file() and (path / "index.html").is_file()


def _artifacts_of_the_clone(directory: str, names: list[str]) -> set[str]:
    parent = Path(directory)
    return {
        name
        for name in names
        if name in NOT_COPYABLE
        or any(fnmatch(name, pattern) for pattern in NOT_COPYABLE_PATTERNS)
        or _is_coverage_report(parent / name)
    }


def copy_tree(source: Path, destination: Path) -> Path:
    """Copia desechable sobre la que se transforma, sin los restos del clon.

    El original nunca se toca: es el árbol de referencia contra el que se
    verifica la equivalencia, y la campaña reutiliza el mismo clon entre
    condiciones. Reutilizarlo es justo lo que hace peligrosa la copia: el clon
    llega con lo que dejaron las corridas anteriores —`run_suite_in_venv`
    ejecuta pip y pytest con cwd en el repo—, y esos restos describen el árbol
    de antes de transformar (ver `NOT_COPYABLE`).
    """
    shutil.copytree(source, destination, ignore=_artifacts_of_the_clone)
    return destination


def iter_transformable_files(root: Path, pattern: str = "*.py") -> list[Path]:
    """Ficheros .py que una transformación puede tocar, tests del repo incluidos.

    Es deliberadamente distinta de `acp.metrics.size.iter_source_files`, que
    excluye los tests: perfilar y transformar quieren conjuntos opuestos.

    El patrón se puede cambiar porque no todo lo que la suite ejecuta es un .py:
    hay repos cuya suite son ficheros de doctest, y esos también hay que
    transformarlos o el renombrado los deja llamando a nombres que ya no están.
    """
    found: list[Path] = []
    for path in sorted(root.rglob(pattern)):
        parts = path.relative_to(root).parts[:-1]
        if any(part in NOT_TRANSFORMABLE or part.startswith(".") for part in parts):
            continue
        found.append(path)
    return found


# Donde pytest lee su configuración. Son los únicos ficheros que pueden nombrar
# una ruta y con ella cambiar lo que la suite colecta, y dos transformaciones
# distintas los necesitan por el mismo motivo: B2 porque mueve los ficheros que
# la configuración nombra, B4 porque se lleva el directorio entero. Vive aquí, y
# no en una de las dos, para que no puedan discrepar sobre dónde mirar.
PYTEST_CONFIG_FILES = ("setup.cfg", "pytest.ini", "tox.ini", "pyproject.toml")


def unparseable_files(root: Path) -> list[Path]:
    """Los ficheros .py que este intérprete no puede leer.

    Existe por un fallo de reproducibilidad medido sobre pint.
    `pint/delegates/txt_defparser/context.py` usa genéricos de PEP 695
    (`def f[T: A | B](...)`), sintaxis de Python 3.12. Con el 3.11 de la VM ese
    fichero no parsea, así que A2 lo saltaba **en silencio**: renombraba
    `ForwardRelation` donde se define y dejaba colgando la referencia
    `definitions.ForwardRelation` del fichero que no pudo leer. El paquete moría
    al importarse y la celda se leía como un agente que rompe cosas.

    El mismo árbol transformado desde 3.12 sale bien, o sea que el resultado
    dependía de la versión del intérprete. Quince sitios hacen
    `except ParserSyntaxError: continue`, que es correcto para lo que no es
    Python y desastroso para lo que sí lo es en otra versión.
    """
    import ast

    rotos: list[Path] = []
    for path in iter_transformable_files(root):
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            rotos.append(path)
        except OSError:
            continue
    return rotos
