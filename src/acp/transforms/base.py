from __future__ import annotations

import shutil
from dataclasses import dataclass, field
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


def copy_tree(source: Path, destination: Path) -> Path:
    """Copia desechable sobre la que se transforma.

    El original nunca se toca: es el árbol de referencia contra el que se
    verifica la equivalencia, y la campaña reutiliza el mismo clon entre
    condiciones.
    """
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git"))
    return destination


# Artefactos y dependencias ajenas. No incluye los directorios de test: esos sí
# se transforman (§4.3.1), al revés que en las métricas de la fase 0.
NOT_TRANSFORMABLE = {
    "build", "dist", ".git", ".venv", "venv", "__pycache__", "node_modules", "site-packages",
    "vendor", "third_party",
}


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
