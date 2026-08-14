"""Dónde se ejecutan los comandos de preparación y de suite.

Los dos ejecutores se conservan a propósito (§2 del spec). El de entorno
virtual aísla dependencias y sirve donde no hay contenedores; el de Docker
aísla el sistema y es el de la campaña. Lo que ninguno de los dos decide es
**qué** se instala ni si la suite llegó a colectarse: eso vive en `suite.py` y
es idéntico en ambos. Aquí solo cambia quién ejecuta.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

CONTAINER_WORKDIR = "/repo"

# La imagen `slim` no trae git, y pint, jsonschema, dateutil y sqlglot derivan
# su versión del repositorio en tiempo de instalación: sin git, `pip install -e .`
# aborta y el candidato se perdería por fontanería, no por sus propiedades.
DEFAULT_IMAGE = "python:3.12"

# Docker admite [a-zA-Z0-9][a-zA-Z0-9_.-]*; el resto se sustituye.
INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_.-]")


def _venv_python(env_dir: Path) -> str:
    if sys.platform == "win32":
        return str(env_dir / "Scripts" / "python.exe")
    return str(env_dir / "bin" / "python")


@dataclass
class VenvRunner:
    """Ejecuta en la propia máquina, con el intérprete de un entorno virtual."""

    repo: Path
    env_dir: Path

    @property
    def python(self) -> str:
        return _venv_python(self.env_dir)

    def wrap(self, command: list[str]) -> list[str]:
        return command


@dataclass
class DockerRunner:
    """Ejecuta dentro de un contenedor de larga vida con el repo montado.

    De larga vida y no uno por comando: la capa de decisión instala, colecta,
    deduce plugins que faltan y reintenta, y todo eso tiene que ver lo que
    instaló el paso anterior.
    """

    repo: Path
    image: str = DEFAULT_IMAGE
    container: str = field(default="")

    def __post_init__(self) -> None:
        if not self.container:
            self.container = f"acp-{INVALID_NAME_CHARS.sub('_', self.repo.name)}"

    @property
    def python(self) -> str:
        return "python"

    def wrap(self, command: list[str]) -> list[str]:
        return ["docker", "exec", "--workdir", CONTAINER_WORKDIR, self.container, *command]

    def start_command(self) -> list[str]:
        return [
            "docker", "run", "--detach", "--name", self.container,
            "--workdir", CONTAINER_WORKDIR,
            self.image, "sleep", "infinity",
        ]

    def copy_command(self) -> list[str]:
        """Copia el repo dentro en vez de montarlo.

        Medido sobre python-stdnum, mismo contenedor y mismo entorno: 113 s de
        suite sobre volumen montado frente a 43 s con el repo dentro. Con 54
        corridas la diferencia no es un detalle. De paso, el clon del host queda
        intacto: ni `.egg-info` ni artefactos de la suite.

        El `/.` final copia el contenido; sin él, `docker cp` crearía
        `/repo/<nombre>` y nada encontraría el repo donde lo espera.
        """
        return ["docker", "cp", f"{self.repo}/.", f"{self.container}:{CONTAINER_WORKDIR}"]

    def trust_command(self) -> list[str]:
        """Sin esto git ve el montaje como `dubious ownership` y aborta."""
        return self.wrap(
            ["git", "config", "--global", "--add", "safe.directory", CONTAINER_WORKDIR]
        )

    def stop_command(self) -> list[str]:
        return ["docker", "rm", "--force", self.container]
