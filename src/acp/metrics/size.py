from __future__ import annotations

import ast
import re
from pathlib import Path

from acp.models import SizeMetrics

EXCLUDED_DIRS = {
    "tests", "test", "testing", "docs", "doc", "examples", "example",
    "vendor", "third_party", "build", "dist", ".git", ".venv", "venv",
    "__pycache__", "node_modules", "site-packages",
    # Utilidades del repositorio, no el código que se estudia: benchmarks,
    # scripts de CI y generadores. Cuentan como líneas y encabezan la muestra
    # de dominio por orden alfabético, que es donde más molesta.
    "benchmarks", "benchmark", "bench", "scripts", "ci_tools", "tools", "pdoc",
}

# Directorios de test que no se llaman "tests": pint guarda la suya en
# pint/testsuite/. Anclado a los dos extremos a propósito: `testfixtures` es una
# librería real, y excluirla entera sería un error peor que el que esto arregla.
EXCLUDED_DIR_PATTERN = re.compile(r"^(?:tests?_.+|.+_tests?|tests?suite)$")

# Ficheros de test sueltos, que no cuelgan de ningún directorio de tests.
EXCLUDED_FILE_PATTERN = re.compile(r"^(?:test_.+|.+_test|tests?|conftest)$")


def _is_excluded_dir(name: str) -> bool:
    # Los ocultos entran enteros: .github trae scripts de CI que no son el repo.
    if name.startswith("."):
        return True
    return name in EXCLUDED_DIRS or bool(EXCLUDED_DIR_PATTERN.match(name))


def iter_source_files(root: Path) -> list[Path]:
    """Ficheros .py del repo, excluidos tests, vendorizados y artefactos."""
    found: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(_is_excluded_dir(part) for part in relative.parts[:-1]):
            continue
        if EXCLUDED_FILE_PATTERN.match(path.stem):
            continue
        found.append(path)
    return found


def module_name(path: Path, root: Path) -> str:
    """El módulo que este fichero es, en la forma en que se importa.

    Vive aquí, junto a `iter_source_files`, porque el nombre del módulo es la
    clave con la que se cruzan tres cosas que se calculan por separado: los
    movimientos que anuncia la familia B, el mapa de identidad que los sigue
    (§5.4.2) y el grafo de acoplamiento. Mientras cada una lo deducía por su
    cuenta salió justo lo que tenía que salir: dos formas del mismo nombre —una
    con el `__init__` y otra sin él— que nunca se encontraban, y todo símbolo
    definido en el `__init__.py` de un paquete movido se caía del mapa en
    silencio. Una sola función es lo que hace que ese desacuerdo no pueda
    volver.

    El `__init__` no forma parte del nombre: `pkg/sub/__init__.py` no se importa
    como `pkg.sub.__init__`, se importa como `pkg.sub` —el fichero *es* el
    paquete—, y ese es el nombre por el que un agente lo pide y por el que la
    tarea se puede dar por localizada.
    """
    parts = path.relative_to(root).with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def read_source(path: Path) -> str:
    """Texto de un fichero fuente, sin el BOM si lo trae.

    Leído como utf-8 a secas, el BOM queda dentro del texto y `ast.parse` lo
    rechaza: el fichero desaparecería de todas las métricas de AST mientras sus
    líneas siguen contando en el denominador.
    """
    return path.read_text(encoding="utf-8-sig", errors="replace")


def parse_source(path: Path) -> ast.Module | None:
    """Árbol del fichero, o None si no hay forma de parsearlo."""
    try:
        return ast.parse(read_source(path))
    except (SyntaxError, ValueError):
        return None


def _code_lines(path: Path) -> int:
    text = read_source(path)
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def measure(root: Path) -> SizeMetrics:
    files = iter_source_files(root)
    if not files:
        return SizeMetrics(python_files=0, code_lines=0, max_depth=0, mean_depth=0.0)
    depths = [len(path.relative_to(root).parts) - 1 for path in files]
    return SizeMetrics(
        python_files=len(files),
        code_lines=sum(_code_lines(path) for path in files),
        max_depth=max(depths),
        mean_depth=sum(depths) / len(depths),
        unparseable_files=sum(1 for path in files if parse_source(path) is None),
    )
