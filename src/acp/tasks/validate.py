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
from dataclasses import dataclass, field

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
