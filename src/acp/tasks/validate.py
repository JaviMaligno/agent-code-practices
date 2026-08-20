"""Comprueba que una tarea rompe lo que declara y nada más (§3.3).

Es el requisito duro del spec: sin esto, una tarea que no rompe nada se contaría
como resuelta siempre —el agente no tendría que hacer nada— y una que rompe media
suite mediría otra cosa: no si el agente arregló el fallo, sino si sobrevivió al
desastre.

## Por qué el resultado por test, y de dónde sale

`parse_pytest_summary` da totales, y con totales la pregunta no se puede
contestar: dos corridas con `1 failed` pueden ser dos fallos distintos, y una
tarea que arregla un test mientras rompe otro daría el mismo resumen que una
tarea válida. Hace falta el veredicto de CADA test, antes y después.

De los tres canales que pytest ofrece se usa **`-v` y parseo**, por descarte
medido, no por gusto:

  - `--report-log` no es de pytest: vive en el plugin `pytest-reportlog`.
    Instalarlo metería una dependencia nueva en el entorno que se está midiendo,
    que es justo lo que §5.6 manda no hacer.
  - `--junit-xml` sí es de pytest y es un fichero, no un terminal, así que sería
    el candidato robusto. Pero su identificador es `classname` + `name`, y
    `classname` se queda con el BASENAME del módulo: medido sobre python-stdnum,
    `stdnum/mx/curp.py` sale como `curp`, y el repo tiene doce módulos llamados
    `vat.py`. Dos tests distintos con el mismo identificador se pisan en el
    diccionario, y el conjunto medido dejaría de ser el que corrió.
  - `-v` imprime el nodeid literal —`stdnum/mx/curp.py::stdnum.mx.curp`—, que es
    el identificador que además sirve para volver a seleccionar el test y el que
    viaja al JSON de la tarea.

El precio de `-v` es que es texto de presentación, así que el lector se escribe
defensivo: sin apoyarse en el porcentaje de progreso (que no siempre está),
reconociendo la forma que xdist usa (veredicto delante) y sin confundir el
`short test summary info` del final, que repite cada fallo al revés.
"""

from __future__ import annotations

import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from acp.models import SuiteMetrics
from acp.runners import CONTAINER_WORKDIR, DEFAULT_IMAGE, DockerRunner
from acp.suite import _pytest_command, _run, install_and_collect, resolve_locations
from acp.tasks.inject import apply_patch, module_path
from acp.tasks.models import Task

# El vocabulario de veredictos de pytest. `error` se guarda distinto de `failed`
# —no es lo mismo que falle una aserción que que el test no llegara a correr—
# pero para la tarea los dos significan lo mismo: ese test dejó de demostrar que
# el código está bien.
OUTCOMES = {
    "PASSED": "passed",
    "FAILED": "failed",
    "ERROR": "error",
    "SKIPPED": "skipped",
    "XFAIL": "xfailed",
    "XPASS": "xpassed",
    "RERUN": "rerun",
}

# Un test que no aparece en la corrida de después no es un test que siga verde:
# el parche pudo impedir que se colectara. Se le da un valor propio para que no
# se confunda con `passed` por omisión.
MISSING = "missing"

# `stdnum/mx/curp.py::stdnum.mx.curp PASSED  [ 25%]`. El porcentaje se recorta
# antes, y el nodeid es codicioso a propósito: así el corte cae en el ÚLTIMO
# sitio posible, que es donde está el veredicto, y un nodeid con espacios
# —parametrizaciones como `test_x[un valor]`— no se parte por el primero.
_PROGRESS = re.compile(r"\s*\[\s*\d+%\]\s*$")
_RESULT = re.compile(r"^(?P<nodeid>\S.*)\s+(?P<outcome>[A-Z]+)(?:\s+\(.*\))?$")

# Con `-n auto` pytest-xdist invierte la línea: `[gw0] [ 50%] PASSED nodeid`.
# Se exige el prefijo `[gwN]` y no solo "veredicto delante" porque el
# `short test summary info` del final tiene esa misma forma —`FAILED nodeid`—
# y leerlo contaría cada fallo dos veces.
_XDIST_RESULT = re.compile(
    r"^\[gw\d+\]\s*(?:\[\s*\d+%\]\s*)?(?P<outcome>[A-Z]+)\s+(?P<nodeid>\S.*?)\s*$"
)


def parse_verbose_outcomes(output: str) -> dict[str, str]:
    """El veredicto de cada test de una corrida con `-v`, por nodeid.

    Si un nodeid aparece varias veces —`pytest-rerunfailures` reintenta— gana el
    último, que es el veredicto con el que la corrida se cerró.
    """
    outcomes: dict[str, str] = {}
    for raw in output.splitlines():
        line = _PROGRESS.sub("", raw.rstrip())
        match = _XDIST_RESULT.match(line) or _RESULT.match(line)
        if match is None:
            continue
        outcome = OUTCOMES.get(match.group("outcome"))
        if outcome is None:
            continue
        outcomes[match.group("nodeid")] = outcome
    return outcomes


@dataclass
class ValidationReport:
    """Lo que se sabe de una tarea después de correr la suite dos veces."""

    valid: bool
    fail_to_pass_ok: bool
    pass_to_pass_ok: bool
    unexpected_failures: list[str]
    # Todo lo que pasó de verde a no-verde, esté declarado o no. No hace falta
    # para el veredicto, pero sí para el generador de la fase 5: cuando su
    # declaración se queda corta puede volver a declarar la tarea con lo que se
    # observó, y la tarea resultante sigue estando respaldada por esta misma
    # corrida en vez de costar otras dos.
    observed_failures: list[str] = field(default_factory=list)


def compare_runs(
    before: dict[str, str], after: dict[str, str], fail_to_pass: list[str]
) -> ValidationReport:
    """Compara las dos corridas y dice si la tarea discrimina.

    Tres reglas, y las tres tienen un modo de fallo detrás:

      1. Los tests que ya fallaban antes se ignoran. No los rompió la tarea, y
         exigirles que pasen dejaría fuera tareas buenas por un defecto ajeno.
      2. Los declarados en `fail_to_pass` tienen que pasar de verde a rojo. Si
         uno no estaba verde antes, no distingue arreglado de roto.
      3. Cualquier OTRO que pase de verde a rojo invalida la tarea, incluido el
         que desaparece: un test que ya no se colecta dejó de demostrar nada, y
         contarlo como "sigue verde" porque no sale en rojo es exactamente la
         forma de romper la suite sin decirlo.
    """
    observed = [
        nodeid
        for nodeid, outcome in before.items()
        if outcome == "passed" and after.get(nodeid, MISSING) != "passed"
    ]
    declared = set(fail_to_pass)
    unexpected = [nodeid for nodeid in observed if nodeid not in declared]
    fail_to_pass_ok = bool(fail_to_pass) and all(
        nodeid in set(observed) for nodeid in fail_to_pass
    )
    return ValidationReport(
        valid=fail_to_pass_ok and not unexpected,
        fail_to_pass_ok=fail_to_pass_ok,
        pass_to_pass_ok=not unexpected,
        unexpected_failures=unexpected,
        observed_failures=observed,
    )


# Los argumentos con los que se pide el resultado por test.
#
# `--verbosity=1` y no `-v`: la verbosidad de pytest es un contador, así que un
# repo con `addopts = -q` cancelaría el `-v` de la línea de órdenes y la corrida
# volvería a imprimir puntos. No daría un error —daría un diccionario vacío, que
# se lee como "esta tarea no rompe nada"—. `--verbosity` FIJA el valor, y como
# los addopts se procesan antes que la línea de órdenes, gana este.
#
# `--tb=no` porque el traceback no se usa para nada y sí cuesta: una mutación en
# un módulo del que cuelga medio repo imprime cientos de trazas, y esa salida se
# lee entera en memoria. La máquina donde corre esto tiene 6 GB para Docker.
PER_TEST_ARGS = ["--verbosity=1", "--tb=no"]


@dataclass
class SuiteSession:
    """Un contenedor vivo donde correr la suite del mismo árbol varias veces.

    `run_suite_in_docker` levanta un contenedor, instala y lo destruye en cada
    llamada. Validar una tarea son DOS corridas de la misma suite —antes y
    después del parche— y validar las 24 son 25: pagar la instalación cada vez
    multiplicaría por dos el coste del pre-flight y, peor, mediría el antes y el
    después en dos entornos distintos, que es justo lo que hace falta descartar
    para poder atribuir la diferencia al parche.

    El árbol del anfitrión no se toca nunca: el parche entra y sale por
    `docker cp` (§4.2). Así el clon queda igual después de validar que antes, y
    una tarea no puede heredar el fallo de la anterior.
    """

    repo: Path
    image: str = DEFAULT_IMAGE
    timeout: int = 1800
    install_repo: bool = True
    prepare: str | None = None

    def __post_init__(self) -> None:
        self.repo, _ = resolve_locations(self.repo, None)
        self._runner = DockerRunner(repo=self.repo, image=self.image)
        self._baseline: dict[str, str] | None = None
        self.metrics = SuiteMetrics(attempted=True)

    def __enter__(self) -> "SuiteSession":
        started = time.monotonic()
        # Un contenedor huérfano de una corrida anterior bloquearía el nombre.
        _run(self._runner.stop_command(), self.repo, self.timeout)
        code, output, timed_out = _run(self._runner.start_command(), self.repo, self.timeout)
        if code != 0 or timed_out:
            raise RuntimeError(f"docker run: {output[-800:]}")
        try:
            code, output, timed_out = _run(self._runner.copy_command(), self.repo, self.timeout)
            if code != 0 or timed_out:
                raise RuntimeError(f"docker cp: {output[-800:]}")
            _run(self._runner.trust_command(), self.repo, self.timeout)
            self.metrics = install_and_collect(
                self.repo, self._runner, self.timeout, self.metrics, started,
                self.prepare, install_repo=self.install_repo,
            )
            # Sin colecta no hay medida, y una tarea "sin tests rotos" sobre un
            # entorno que no llegó a levantarse se leería como una tarea que no
            # discrimina. Es fontanería, y §5.6 pide que suene como fontanería.
            if not self.metrics.collect_ok:
                raise RuntimeError(f"la suite no se colectó: {self.metrics.install_error}")
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_excepcion: object) -> None:
        self.close()

    def close(self) -> None:
        _run(self._runner.stop_command(), self.repo, self.timeout)

    def outcomes(self) -> dict[str, str]:
        """El veredicto de cada test de una corrida completa de la suite."""
        comando = self._runner.wrap(
            _pytest_command(self._runner, PER_TEST_ARGS, self.install_repo)
        )
        _, output, timed_out = _run(comando, self.repo, self.timeout)
        if timed_out:
            raise TimeoutError(f"la suite de {self.repo.name} pasó de {self.timeout}s")
        leidos = parse_verbose_outcomes(output)
        if not leidos:
            # Ni un solo veredicto legible es un fallo del circuito de medida, no
            # un resultado: se cuenta como "nada roto" y la tarea se descarta o
            # se acepta por la razón equivocada.
            raise RuntimeError(f"la corrida no dio ni un veredicto: {output[-800:]}")
        return leidos

    def baseline(self) -> dict[str, str]:
        """La corrida del árbol original, medida una vez y reutilizada.

        Es la misma para todas las tareas del mismo repo: el "antes" es una
        propiedad del árbol, no de la tarea. Reutilizarla es lo que hace que
        validar N tareas cueste N+1 corridas y no 2N.
        """
        if self._baseline is None:
            self._baseline = self.outcomes()
        return self._baseline

    def write(self, relative: str, content: str) -> None:
        """Deja un fichero dentro del contenedor, sin tocar el árbol de fuera."""
        with tempfile.TemporaryDirectory() as temporal:
            destino = Path(temporal) / Path(relative).name
            destino.write_text(content, encoding="utf-8")
            code, output, _ = _run(
                ["docker", "cp", str(destino),
                 f"{self._runner.container}:{CONTAINER_WORKDIR}/{relative}"],
                self.repo, self.timeout,
            )
            if code != 0:
                raise RuntimeError(f"docker cp {relative}: {output[-400:]}")


def validate_task(
    repo: Path,
    task: Task,
    timeout: int = 1800,
    *,
    session: SuiteSession | None = None,
    image: str = DEFAULT_IMAGE,
    install_repo: bool = True,
    prepare: str | None = None,
) -> ValidationReport:
    """Corre la suite con y sin el parche y dice si la tarea discrimina.

    `repo` se pasa en su estado ORIGINAL: el parche lo aplica esta función, y
    solo dentro del contenedor. `session` permite validar varias tareas del
    mismo repo pagando una sola instalación y una sola corrida de referencia.
    """
    if session is not None:
        return _validate_in(session, session.repo, task)
    with SuiteSession(
        repo, image=image, timeout=timeout, install_repo=install_repo, prepare=prepare
    ) as abierta:
        return _validate_in(abierta, abierta.repo, task)


def _validate_in(session: SuiteSession, repo: Path, task: Task) -> ValidationReport:
    ruta = module_path(repo, task.module)
    relativa = ruta.relative_to(repo).as_posix()
    original = ruta.read_text(encoding="utf-8")
    # Aplicar el parche de la tarea, y no volver a mutar, es lo que comprueba que
    # el parche guardado en el JSON es de verdad el parche de referencia: si no
    # encajara en el fuente, esto suena aquí y no en mitad de la campaña.
    mutado = apply_patch(original, task.patch)

    antes = session.baseline()
    session.write(relativa, mutado)
    try:
        despues = session.outcomes()
    finally:
        # El contenedor vuelve al árbol original aunque la corrida falle: la
        # referencia y las tareas siguientes miden sobre él.
        session.write(relativa, original)
    return compare_runs(antes, despues, task.fail_to_pass)
