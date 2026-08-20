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


# Todo lo que un programa resuelve contando desde el HOME del usuario. Se fijan
# también las variables XDG y no solo HOME porque en Linux ganan a HOME: una
# `XDG_CACHE_HOME` heredada del anfitrión devolvería la caché compartida aunque
# HOME fuera falso.
_HOME_DERIVED = {
    "XDG_CACHE_HOME": ".cache",
    "XDG_DATA_HOME": ".local/share",
    "XDG_STATE_HOME": ".local/state",
    "XDG_CONFIG_HOME": ".config",
}


@dataclass
class VenvRunner:
    """Ejecuta en la propia máquina, con el intérprete de un entorno virtual.

    Y con un HOME propio, que es la otra mitad del aislamiento. §5.4.4 pide que
    cada ejecución arranque sin estado compartido con la anterior, y aquí el
    aislamiento es solo de dependencias: lo que una condición deje en la caché
    del usuario lo lee la siguiente. Medido sobre pint, que guarda su caché de
    unidades ahí con `cache_folder=":auto:"` —la condición base deja los
    pickles, B1 y B5 cambian el `__module__` de las clases y el tramo siguiente
    muere con `AttributeError: Can't get attribute 'OffsetConverter'`, un fallo
    que no tiene que ver con lo que la celda mide—. Fue el único rojo de la
    suite entera de pint bajo B1 (`1 failed, 2288 passed`); con la caché limpia
    vuelve a 2.289, idéntico a la base.

    El contenedor no lo necesita —se crea y se destruye en cada corrida—, así
    que esto vive solo en el ejecutor que el spec conserva como alternativa
    (§2, §5.6) y no en los dos.

    El precio, declarado: la caché de descargas de pip también vive bajo el HOME
    del usuario, así que un entorno recién creado vuelve a bajar lo que instala.
    Es el mismo precio que el contenedor ya paga en cada corrida, y en la
    campaña —un entorno por repositorio, `keep_env`— solo se paga una vez.
    """

    repo: Path
    env_dir: Path

    @property
    def python(self) -> str:
        return _venv_python(self.env_dir)

    @property
    def project_dir(self) -> str:
        """Dónde vive el repo desde el punto de vista del intérprete que lo usa."""
        return str(self.repo)

    @property
    def home(self) -> Path:
        """El HOME de esta corrida: hermano del entorno y atado al repo.

        Hermano y con nombre derivado, y no un temporal anónimo, por lo mismo
        que el entorno (`resolve_locations`): dos clones hermanos no pueden
        compartirlo, quien depure una celda puede mirar lo que quedó dentro, y
        no se acumula un directorio por corrida en el temporal del sistema. Lo
        que garantiza que esté vacío es que quien prepara el entorno lo borra
        antes de empezar; el nombre solo tiene que ser suyo.
        """
        return self.env_dir.with_name(f".acp-home-{self.repo.name}")

    def environment(self) -> dict[str, str]:
        return {"HOME": str(self.home)} | {
            name: str(self.home / tail) for name, tail in _HOME_DERIVED.items()
        }

    def wrap(self, command: list[str]) -> list[str]:
        """El mismo comando, con el entorno de la corrida por delante.

        Se pone en el comando y no en el `env=` de `subprocess` para que el
        aislamiento viaje con el ejecutor —que es lo que distingue a los dos— y
        no dependa de que cada uno de los once sitios que lanzan algo se acuerde
        de pasarlo. `env` está donde ya está `sh`, que este ejecutor usa desde
        que el repo se alcanza por ruta en vez de instalado.
        """
        assignments = [f"{name}={value}" for name, value in self.environment().items()]
        return ["env", *assignments, *command]


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

    @property
    def project_dir(self) -> str:
        return CONTAINER_WORKDIR

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
