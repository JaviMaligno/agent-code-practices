"""Qué trozo de un texto es código de doctest, y qué ficheros los ejecutan.

Vive fuera de A2 y de B2 por la misma razón que `docstrings.py` vive fuera de A4
y de B3: las dos celdas necesitan la regla idéntica. Un doctest no es
documentación, es suite —python-stdnum corre la suya con `--doctest-modules` y
413 de sus tests están dentro de ejemplos—, así que las dos transformaciones
tienen que reescribir exactamente el mismo conjunto de líneas. A2 cambia los
nombres que aparecen en el ejemplo y B2 la ruta del módulo que importa; si cada
una decidiera por su cuenta dónde empieza y acaba un ejemplo, la diferencia
medida entre las dos incluiría la diferencia entre sus dos lectores.
"""

from __future__ import annotations

import configparser
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

from acp.transforms.base import iter_transformable_files

DOCTEST_PROMPT = ">>>"
DOCTEST_CONTINUATION = "..."


def prompt_parts(line: str, marker: str) -> tuple[str, str, str] | None:
    """(sangría, prefijo hasta el prompt, código) de una línea de doctest."""
    stripped = line.lstrip(" ")
    if not stripped.startswith(marker):
        return None
    indent = line[: len(line) - len(stripped)]
    rest = stripped[len(marker) :]
    # `>>>x` no es un prompt: doctest exige el espacio, o nada detrás.
    if rest and not rest.startswith(" "):
        return None
    return indent, indent + marker + rest[:1], rest[1:]


def doctest_examples(lines: list[str]) -> list[list[tuple[int, str, str]]]:
    """Ejemplos del texto: la línea `>>>` y las continuaciones que la siguen.

    Una continuación solo cuenta si va pegada al ejemplo y con su misma
    sangría. Es lo que distingue el `...` que continúa una línea de código del
    `...` que es la salida esperada dentro de un traceback.
    """
    examples: list[list[tuple[int, str, str]]] = []
    index = 0
    while index < len(lines):
        opened = prompt_parts(lines[index], DOCTEST_PROMPT)
        if opened is None:
            index += 1
            continue
        indent, prefix, code = opened
        block = [(index, prefix, code)]
        index += 1
        while index < len(lines):
            following = prompt_parts(lines[index], DOCTEST_CONTINUATION)
            if following is None or following[0] != indent:
                break
            block.append((index, following[1], following[2]))
            index += 1
        examples.append(block)
    return examples


def rewrite_examples(text: str, rewrite: Callable[[str], str | None]) -> str:
    """Aplica `rewrite` al código de cada ejemplo, y solo ahí.

    La prosa y la salida esperada de alrededor no se tocan: reescribirlas sería
    documentación (A4/B3) colándose dentro de otra celda. `rewrite` devuelve
    None cuando no ha sabido leer el trozo, y entonces el ejemplo se queda como
    estaba: a medias es peor que intacto.
    """
    lines = text.split("\n")
    examples = doctest_examples(lines)
    if not examples:
        return text

    changed = False
    for block in examples:
        rewritten = rewrite("\n".join(item[2] for item in block))
        if rewritten is None:
            continue
        new_lines = rewritten.split("\n")
        # Reescribir una ruta no añade ni quita líneas. Si las cuentas no
        # cuadran, algo se entendió mal y el ejemplo se deja como estaba.
        if len(new_lines) != len(block):
            continue
        for (index, prefix, _), new_code in zip(block, new_lines):
            lines[index] = prefix + new_code
        changed = True
    return "\n".join(lines) if changed else text


DOCTEST_GLOB_PATTERN = re.compile(r"--doctest-glob[=\s]+['\"]?([^'\"\s]+)")


def pytest_addopts(root: Path) -> str:
    """Los `addopts` que el repo declara, mire donde mire pytest."""
    pieces: list[str] = []
    for name, section in (
        ("setup.cfg", "tool:pytest"), ("pytest.ini", "pytest"), ("tox.ini", "pytest"),
    ):
        path = root / name
        if not path.exists():
            continue
        parser = configparser.ConfigParser()
        try:
            parser.read_string(path.read_text(encoding="utf-8-sig", errors="replace"))
        except (configparser.Error, OSError):
            continue
        pieces.append(parser.get(section, "addopts", fallback=""))

    path = root / "pyproject.toml"
    if path.exists():
        try:
            config = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (tomllib.TOMLDecodeError, OSError):
            config = {}
        raw = config.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("addopts", "")
        pieces.append(" ".join(raw) if isinstance(raw, list) else str(raw))
    return " ".join(pieces)


def doctest_files(root: Path) -> list[Path]:
    """Ficheros que no son .py y que la suite del repo ejecuta como doctests.

    Se leen los `addopts` en vez de barrer todo lo que contenga un `>>>` porque
    la diferencia importa: un README con ejemplos no lo ejecuta nadie, así que
    reescribirlo no arregla ninguna equivalencia y sí contamina B3, que es la
    condición sobre la documentación del repo.
    """
    found: list[Path] = []
    for pattern in DOCTEST_GLOB_PATTERN.findall(pytest_addopts(root)):
        if pattern.endswith(".py"):
            continue
        found.extend(iter_transformable_files(root, pattern))
    return sorted(set(found))
