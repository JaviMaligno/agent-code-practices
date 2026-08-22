"""La suite de un repositorio de Node, con el mismo contrato que la de Python.

Existe por la sonda TypeScript (§3.5): en Python las anotaciones no las comprueba
nadie en ejecución, así que A1 mide su valor **como documentación**; en un
lenguaje con comprobación estática son además contrato. Sin poder correr la suite
de un repo TypeScript no hay forma de saber si el resultado sobre tipos es un
artefacto del lenguaje.

Lo que se traduce aquí es solo el borde: cómo se instala, cómo se ejecuta y cómo
se lee el veredicto. El oráculo de la celda, el checkpoint y el resumen reciben el
mismo `dict[str, str]` que reciben de pytest y no distinguen lenguajes.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from acp.runners import CONTAINER_WORKDIR, DockerRunner

# Node 22 es la que declaran los repos candidatos en `engines`; la imagen trae
# npm, y bun se instala aparte cuando el repositorio lo pide.
DEFAULT_IMAGE = "node:22"


def outcomes_from_vitest(payload: dict, root: str) -> dict[str, str]:
    """El veredicto de cada test, por identificador estable.

    Vitest da rutas absolutas del contenedor, así que se recortan contra la raíz:
    el identificador tiene que ser el mismo en el árbol sano y en el degradado, y
    esos viven en directorios distintos.

    Las palabras del veredicto se dejan como vitest las da —`passed`, `failed`,
    `skipped`— porque son las mismas que usa pytest y las que `cell_oracle`
    compara. Traducirlas aquí sería introducir un sitio más donde equivocarse.
    """
    salida: dict[str, str] = {}
    raiz = root.rstrip("/") + "/"
    for fichero in payload.get("testResults") or []:
        nombre = fichero.get("name", "")
        relativo = nombre.split(raiz, 1)[-1] if raiz in nombre else nombre
        for caso in fichero.get("assertionResults") or []:
            salida[f"{relativo}::{caso['fullName']}"] = caso["status"]

    if not salida:
        # Un diccionario vacío es indistinguible de "todo pasó" para quien
        # compare dos corridas, y ahí es donde una suite rota se lee como un
        # agente que no rompió nada.
        raise RuntimeError(
            "la corrida de vitest no dio ni un veredicto: "
            f"{json.dumps(payload)[:400]}"
        )
    return salida


@dataclass
class NodeSuiteSession:
    """Un contenedor con el repo dentro, su suite instalada y ejecutable.

    Mismo contrato que `SuiteSession`: se entra, se pregunta por `outcomes()`, se
    escribe con `write()` y se sale. Lo que cambia es el gestor de paquetes y el
    ejecutor de tests, no lo que el resto del sistema espera.
    """

    repo: Path
    image: str = DEFAULT_IMAGE
    timeout: int = 3600
    # bun cuando el repositorio lo declara en `packageManager`: hono trae
    # `bun.lock` y con npm su instalación deja fuera dependencias de desarrollo.
    package_manager: str = "npm"
    metrics: dict = field(default_factory=dict)
    _runner: DockerRunner | None = None

    def __post_init__(self) -> None:
        self.repo = Path(self.repo).expanduser().resolve()
        self._runner = DockerRunner(repo=self.repo, image=self.image)

    def _clear_previous_container(self) -> None:
        """Retira el contenedor que dejó una corrida anterior.

        Sin esto, una corrida que muere a mitad deja su contenedor con el mismo
        nombre y el siguiente arranque falla con `Conflict. The container name is
        already in use`: un fallo pasajero se vuelve permanente hasta que alguien
        lo limpia a mano.
        """
        _run(self._runner.stop_command(), 120, check=False)

    def __enter__(self) -> "NodeSuiteSession":
        self._clear_previous_container()
        _run(self._runner.start_command(), self.timeout)
        _run(self._runner.copy_command(), self.timeout)
        if self.package_manager == "bun":
            self.run("curl -fsSL https://bun.sh/install | bash")
        codigo, salida, _ = self.run(self._install_command())
        if codigo != 0:
            self.close()
            raise RuntimeError(f"la instalación falló: {salida[-600:]}")
        return self

    def __exit__(self, *_excepcion: object) -> None:
        self.close()

    def close(self) -> None:
        if self._runner is not None:
            _run(self._runner.stop_command(), 120, check=False)

    def _install_command(self) -> str:
        if self.package_manager == "bun":
            return 'export PATH="$HOME/.bun/bin:$PATH" && bun install'
        return "npm ci --no-audit --no-fund || npm install --no-audit --no-fund"

    def run(self, command: str) -> tuple[int, str, bool]:
        """Un comando dentro del contenedor: código, salida y si expiró.

        Los tres valores no son decoración: `Toolbox._shell` desempaqueta tres, y
        devolver dos rompe TODAS las herramientas del agente con un ValueError
        que él recibe como si la herramienta no hubiera encontrado nada. Cuatro
        celdas de la sonda salieron como "no lo arregló" por esto, con cero
        lecturas y cero ediciones.
        """
        try:
            proceso = subprocess.run(
                self._runner.wrap(["sh", "-lc", command]),
                capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return 124, "", True
        return proceso.returncode, proceso.stdout + proceso.stderr, False

    def write(self, relative: str, content: str) -> None:
        """Deja un fichero dentro del contenedor, como hace el agente al editar."""
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".tmp", delete=False) as tmp:
            tmp.write(content)
            origen = tmp.name
        _run(
            ["docker", "cp", origen,
             f"{self._runner.container}:{CONTAINER_WORKDIR}/{relative}"],
            self.timeout,
        )
        Path(origen).unlink(missing_ok=True)

    def outcomes(self) -> dict[str, str]:
        """Lo que la suite responde, test a test.

        Solo los tests de **runtime**: la comprobación de tipos y los type-tests
        verifican precisamente lo que A1 degrada, así que incluirlos haría la
        equivalencia imposible por construcción — no porque el programa cambie,
        sino porque la suite mide el tratamiento. Queda declarado en
        `infra/ts/README.md`.
        """
        ejecutor = "bunx" if self.package_manager == "bun" else "npx"
        prefijo = 'export PATH="$HOME/.bun/bin:$PATH" && ' if self.package_manager == "bun" else ""
        codigo, salida, _ = self.run(
            f"{prefijo}{ejecutor} vitest --run --reporter=json "
            f"--outputFile=/tmp/vitest.json --coverage.enabled=false"
        )
        _, crudo, _ = self.run("cat /tmp/vitest.json")
        try:
            payload = json.loads(crudo)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"vitest no dejó un informe legible (código {codigo}): {salida[-600:]}"
            ) from error
        return outcomes_from_vitest(payload, root=CONTAINER_WORKDIR)


def _run(command: list[str], timeout: int, check: bool = True) -> str:
    proceso = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if check and proceso.returncode != 0:
        raise RuntimeError(f"{' '.join(command[:3])}: {proceso.stderr[-400:]}")
    return proceso.stdout
