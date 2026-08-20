"""Los dos agentes falsos que demuestran que el circuito de medida no miente.

§5.4.6 pide dos controles que recorran el pipeline entero sin gastar un token:

  - **el no-op** no edita nada y tiene que dar 0%. Si da más, hay tareas cuyos
    tests no discriminan: el árbol se entrega ya arreglado, o los tests que la
    tarea declara no llegan a correr en esa condición.
  - **el oráculo** aplica el parche de referencia y tiene que dar 100%. Si da
    menos, o la transformación rompió el repositorio, o el mapa de identidad de
    símbolos está mal —y las dos cosas se leen, en la tabla principal, igual que
    un agente que fracasa—.

## Por qué el oráculo no es «revertir el parche»

`apply_patch(..., reverse=True)` revierte el parche de referencia al carácter, y
es exactamente lo que hace falta en T0. Deja de valer en cuanto hay condición:
A2 le cambia el nombre al símbolo y a todo lo que llama, A3 aplasta el espaciado
y junta las continuaciones, A4 se lleva el comentario y la prosa con la que el
hunk se sitúa, y B1/B2/B5 mueven el símbolo a otro fichero. En cualquiera de
esos árboles el contexto no cuadra línea a línea y `apply_patch` —rígido a
propósito— lanza `ValueError`.

La alternativa fácil sería aflojarlo, que es lo que hace `patch(1)`: buscar el
hueco unas líneas más allá y aplicar igual. Sería lo peor posible aquí, porque
el árbol acabaría con un fallo distinto del que la tarea declara y la corrida
seguiría en verde. Así que el oráculo hace lo contrario: **traduce el parche a
la condición**. Localiza el símbolo por el manifiesto de procedencia —que para
eso guarda la identidad original (§5.4.2)—, busca dentro de él el trozo roto
comparando sin espacios y con los nombres pasados por el diccionario de
renombrados, y escribe ahí el trozo correcto. `apply_patch` sigue siendo la
referencia de qué significa «cuadró», no el mecanismo.

Y si el manifiesto no sabe dónde acabó el símbolo, o el trozo roto no aparece
donde el manifiesto dice, el oráculo **falla ruidosamente**: significa que esa
condición no es medible para esa tarea, y es mejor saberlo aquí que a mitad de
campaña con una celda entera en 0% sin razón aparente.

## Qué se compara para decir 0% y 100%

Los nodeids de la tarea se declararon sobre el árbol original (`stdnum/mx/curp.py
::stdnum.mx.curp`), y en el árbol de la condición ni la ruta ni el nombre del
módulo tienen por qué seguir siendo esos: B2 aplana y B5 funde. Por eso el
veredicto NO se lee contra la lista declarada sino contra la corrida del árbol
tal y como se entrega —la que produce el propio no-op—: resuelto es «todo lo que
estaba en rojo pasó a verde y nada que estuviera en verde se cayó». Es la misma
afirmación, medida con los identificadores que la condición tiene de verdad.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from acp.cli import manifest_path_for
from acp.models import SuiteMetrics
from acp.symbols import Location, build_symbol_map
from acp.tasks.inject import _hunks, apply_patch
from acp.tasks.models import Task
from acp.tasks.validate import MISSING, SuiteSession
from acp.runners import DEFAULT_IMAGE

# Lo que cuenta como «este test ya no demuestra nada». `missing` entra aquí por
# la misma razón que en `compare_runs`: un test que dejó de colectarse no es un
# test que siga verde. Lo saltado y lo xfail no entran: no son un fallo, y
# exigirles que pasen dejaría al oráculo en 0% en cualquier repo real.
FAILING = frozenset({"failed", "error", MISSING})

# Cuántas líneas de más puede ocupar en el árbol un trozo que en el parche ocupa
# n. A3 solo junta líneas (nunca las parte), así que el trozo suele ser MÁS
# corto; el margen existe para lo que A4 deja detrás —una línea de comentario se
# queda en blanco, no desaparece— y para las líneas en blanco que ninguna celda
# toca. Seis es holgado para un hunk de tres líneas de contexto y sigue siendo
# barato: el barrido es cuadrático, pero sobre el rango de UNA función.
MARGIN = 6


def no_op(repo: Path, task: Task) -> None:
    """El agente que no hace nada.

    Es trivial y no es inútil: recorre el pipeline entero —se le entrega el
    árbol, se corre la suite, se puntúa— así que su 0% es la prueba de que los
    tests de la tarea distinguen roto de arreglado en ESA condición. Un no-op
    que puntúa es una tarea que se contaría como resuelta sin tocar nada.
    """
    return None


def repaired_source(
    repo: Path, task: Task, *, manifest: Path | None = None
) -> tuple[str, str]:
    """(ruta relativa, contenido del fichero con el fallo deshecho).

    Separado de `oracle` porque el circuito de validación no escribe en el árbol
    del anfitrión: mete y saca el fichero por `docker cp` (§4.2). Quien quiera
    el arreglo sin tocar el clon entra por aquí.
    """
    repo = Path(repo)
    symbols, renames = _provenance(repo, manifest)
    key = f"{task.module}.{task.symbol}"
    location = symbols.get(key)
    if location is None:
        raise LookupError(
            f"el manifiesto de {repo} no sabe dónde acabó {key}: la condición no "
            "es medible para esta tarea (§5.4.6)"
        )
    path = repo / location.path
    if not path.is_file():
        raise LookupError(f"{key} debería estar en {location.path}, que no existe en {repo}")
    source = path.read_text(encoding="utf-8")

    try:
        # El camino de T0, y la definición de «cuadró»: el parche revertido al
        # carácter. Si el contexto no coincide exactamente, `apply_patch` grita
        # en vez de buscarle un hueco aproximado, y entonces —y solo entonces—
        # hay que traducir.
        repaired = apply_patch(source, task.patch, reverse=True)
    except ValueError:
        repaired = _revert_translated(source, task, location, renames)
    return location.path, _parsed(repaired, task, location)


def _parsed(repaired: str, task: Task, location: Location) -> str:
    """El arreglo, si es Python; si no, un error y no un fichero.

    Es la única afirmación que el oráculo puede hacer y un agente de verdad no:
    que el parche de referencia es de verdad la referencia. Un arreglo que no
    compila tira la suite ENTERA —no los dos tests de la tarea— y en la tabla
    principal eso se lee igual que un agente que rompió el repositorio, que es
    el peor error posible aquí porque nadie lo atribuye al circuito de medida.
    Los sitios por donde puede colarse son dos: el diccionario de renombrados,
    que reescribe por palabra completa y podría tocar dentro de un literal, y la
    sangría, si algún día B1 o B5 dejan el símbolo dentro de otro cuerpo.
    """
    try:
        ast.parse(repaired)
    except SyntaxError as error:
        raise ValueError(
            f"el arreglo de {task.task_id!r} no compila en {location.path}: "
            f"{error}. El parche de referencia no describe {task.module}."
            f"{task.symbol} en esta condición"
        ) from error
    return repaired


def oracle(repo: Path, task: Task, *, manifest: Path | None = None) -> Path:
    """El agente que arregla el fallo, y solo el fallo.

    Devuelve el fichero que tocó —no `None` como el no-op— porque quien lo
    ejecuta necesita saber qué copiar al contenedor.
    """
    relative, repaired = repaired_source(repo, task, manifest=manifest)
    path = Path(repo) / relative
    path.write_text(repaired, encoding="utf-8")
    return path


ORACLES = {"no_op": no_op, "oracle": oracle}


def _provenance(
    repo: Path, manifest: Path | None
) -> tuple[dict[str, Location], dict[str, str]]:
    """El mapa de identidad del árbol y el diccionario de renombrados.

    Si no hay manifiesto es que el árbol no se transformó (T0): entonces la
    identidad de cada símbolo es su nombre de hoy y se lee del propio árbol. Así
    las dos condiciones recorren el mismo camino y el caso fácil no queda sin
    ejercitar por tener código propio.
    """
    path = manifest_path_for(Path(repo)) if manifest is None else Path(manifest)
    if not path.is_file():
        return build_symbol_map(Path(repo)), {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    symbols = {key: Location(**value) for key, value in raw.get("symbols", {}).items()}
    return symbols, dict(raw.get("renames", {}))


def _revert_translated(
    source: str, task: Task, location: Location, renames: dict[str, str]
) -> str:
    """El fuente con el parche revertido, traducido a la condición.

    Cada hunk se lee como dos versiones del mismo trozo: el lado NUEVO es lo que
    la condición contiene (el fallo, ya transformado) y el lado VIEJO es lo
    correcto. Se busca el primero dentro del rango del símbolo y se escribe el
    segundo en su sitio.

    Los hunks se recorren de atrás adelante para que los números de línea de los
    que quedan por delante sigan siendo válidos después de cada sustitución.
    """
    lines = source.splitlines(keepends=True)
    rename = _pattern(renames)
    delta = 0
    for _header, body in reversed(_hunks(task.patch)):
        broken = [line[1:] for line in body if line[0] in " +"]
        correct = [line[1:] for line in body if line[0] in " -"]
        leading = _leading_context(body)
        trailing = _leading_context(list(reversed(body)))
        # El contexto del hunk puede salirse del símbolo por arriba o por abajo
        # —difflib da tres líneas a cada lado—, así que el rango se ensancha lo
        # justo para que quepa. Sigue anclado al símbolo, que es lo que hace que
        # B5 no encuentre el mismo trozo en el vecino con el que lo fundió.
        low = max(0, location.start - 1 - leading)
        high = min(len(lines), location.end + delta + trailing)
        replaced = _replace_first(lines, low, high, broken, correct, leading, trailing, rename)
        if replaced is None:
            raise ValueError(
                f"el trozo que la tarea {task.task_id!r} rompió no aparece en "
                f"{location.path} entre las líneas {low + 1} y {high}: "
                f"{task.module}.{task.symbol} no es lo que el parche describe"
            )
        lines, cambio = replaced
        delta += cambio
    return "".join(lines)


def _replace_first(
    lines: list[str],
    low: int,
    high: int,
    broken: list[str],
    correct: list[str],
    leading: int,
    trailing: int,
    rename: Callable[[list[str]], list[str]],
) -> tuple[list[str], int] | None:
    """Sustituye el trozo roto por el correcto, o None si no lo encuentra.

    Se prueba primero con TODO el contexto del hunk, que es lo más específico, y
    se va recortando línea a línea. El recorte es lo que salva el caso de A4
    —cuyas víctimas son justo las líneas de contexto: el comentario y la prosa
    de la docstring— y a la vez conserva el ancla cuando el hunk no cambia
    ninguna línea sino que solo quita (revertir un `drop_none_check` es
    insertar, y sin contexto no habría dónde).
    """
    for trim in range(max(leading, trailing) + 1):
        front, back = min(trim, leading), min(trim, trailing)
        pattern, replacement = _without_blank_edges(
            broken[front : len(broken) - back], correct[front : len(correct) - back]
        )
        target = _normalise(rename(pattern))
        if not target:
            continue
        found = _windows(lines, low, high, len(pattern), target)
        if len(found) > 1:
            raise ValueError(
                f"el trozo roto aparece {len(found)} veces entre las líneas "
                f"{low + 1} y {high}: no hay forma de saber cuál rompió la tarea"
            )
        if not found:
            continue
        start, end = found[0]
        escrito = _reindent(rename(replacement), source=pattern[0], target=lines[start])
        return lines[:start] + escrito + lines[end:], len(escrito) - (end - start)
    return None


def _without_blank_edges(
    pattern: list[str], replacement: list[str]
) -> tuple[list[str], list[str]]:
    """Los dos lados del hunk sin las líneas en blanco de los extremos.

    Una línea en blanco no dice nada al comparar sin espacios, así que dejarla
    en el patrón hace que el mismo trozo del árbol coincida dos veces —con ella
    y sin ella— y el barrido no sabría cuál de las dos sustituir. Se quitan de
    los dos lados a la vez y solo cuando en los dos está en blanco: los extremos
    del hunk son contexto, o sea la misma línea en las dos versiones, y recortar
    uno solo desalinearía el trozo que se escribe del que se busca.
    """
    while pattern and replacement and not pattern[0].strip() and not replacement[0].strip():
        pattern, replacement = pattern[1:], replacement[1:]
    while pattern and replacement and not pattern[-1].strip() and not replacement[-1].strip():
        pattern, replacement = pattern[:-1], replacement[:-1]
    return pattern, replacement


def _windows(
    lines: list[str], low: int, high: int, length: int, target: str
) -> list[tuple[int, int]]:
    """Los tramos de `lines[low:high]` que dicen lo mismo que `target`.

    Los tramos que empiezan o acaban en blanco no cuentan, por lo mismo que el
    patrón no los lleva: dirían lo mismo que el tramo de dentro y el resultado
    sería una ambigüedad inventada. Y por cada comienzo se guarda solo el tramo
    más corto que encaja, que es el que no se lleva por delante código de
    alrededor.
    """
    found: list[tuple[int, int]] = []
    for start in range(low, high):
        if not lines[start].strip():
            continue
        for end in range(start + 1, min(start + length + MARGIN, high) + 1):
            if lines[end - 1].strip() and _normalise(lines[start:end]) == target:
                found.append((start, end))
                break
    return found


def _normalise(lines: Iterable[str]) -> str:
    """El texto sin un solo espacio, ni dentro de la línea ni entre ellas.

    A3 quita el aire alrededor de los operadores (`n % 97` → `n%97`) y junta las
    continuaciones de una línea lógica, así que comparar por líneas o
    respetando el espaciado no encontraría nada. Quitarlo todo se aplica igual a
    los dos lados, de modo que no puede inventar una coincidencia que no esté:
    lo más que puede hacer es unir dos tokens que ya estaban pegados en ambos.
    """
    return "".join("".join(line.split()) for line in lines)


def _pattern(renames: dict[str, str]) -> Callable[[list[str]], list[str]]:
    """Traductor de las líneas del parche a los nombres que la condición les puso.

    A2 renombra el símbolo y todo lo que aparece en su cuerpo, así que el trozo
    escrito en el parche no existe en el árbol tal cual —y el arreglo escrito
    tal cual llamaría a nombres que ya no están—. Es el mismo diccionario que
    publica el manifiesto y el mismo criterio que usa `relocate_symbols` para
    saber por qué nombre preguntar: por palabra completa.

    Se compila UN autómata para los cientos de nombres que A2 cambia en un repo,
    y se compila una vez por parche: el barrido prueba cada recorte de contexto
    contra decenas de tramos, y recompilar ahí dentro se nota.
    """
    if not renames:
        return list
    automaton = re.compile(
        r"\b(?:%s)\b"
        % "|".join(re.escape(name) for name in sorted(renames, key=len, reverse=True))
    )
    def translate(lines: list[str]) -> list[str]:
        return [automaton.sub(lambda match: renames[match.group(0)], line) for line in lines]

    return translate


def _leading_context(body: list[str]) -> int:
    """Cuántas líneas de contexto trae el hunk por delante."""
    count = 0
    for line in body:
        if line[0] != " ":
            break
        count += 1
    return count


def _reindent(lines: list[str], source: str, target: str) -> list[str]:
    """El trozo correcto con la sangría que tiene el árbol donde va a entrar.

    Ninguna de las nueve transformaciones cambia la sangría —en Python es
    sintaxis, y A3 lo dice explícitamente—, así que esto casi siempre es la
    identidad. Casi: B1 y B5 mueven definiciones entre ficheros, y el día que
    una acabe dentro de un cuerpo con otro nivel, escribir la sangría del parche
    dejaría el fichero sin compilar.
    """
    origen, destino = _indent(source), _indent(target)
    if origen == destino:
        return list(lines)
    reindented: list[str] = []
    for line in lines:
        if not line.strip():
            reindented.append(line)
        elif line.startswith(origen):
            reindented.append(destino + line[len(origen) :])
        else:
            reindented.append(line)
    return reindented


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


@dataclass
class OracleRun:
    """Lo que dio un control: qué se arregló, qué se rompió y qué sigue roto."""

    kind: str
    repaired: list[str]
    broken: list[str]
    still_failing: list[str]
    outcomes: dict[str, str] = field(default_factory=dict)
    metrics: SuiteMetrics = field(default_factory=SuiteMetrics)

    @property
    def resolved(self) -> bool:
        """La tarea queda resuelta si no hay nada roto ni nada sin arreglar."""
        return not self.broken and not self.still_failing


def compare_to_delivered(
    kind: str,
    delivered: dict[str, str],
    after: dict[str, str],
    *,
    already_failing: Iterable[str] = (),
    outcomes: dict[str, str] | None = None,
    metrics: SuiteMetrics | None = None,
) -> OracleRun:
    """Puntúa un control contra la corrida del árbol tal y como se entregó.

    `already_failing` son los tests que ya estaban rojos ANTES de inyectar el
    fallo. En los tres finalistas del sustrato la suite de partida está en
    verde, así que la lista está vacía; existe porque un repositorio con un test
    roto de fábrica dejaría al oráculo en 0% por un defecto que no es suyo, y
    eso se leería como un circuito que miente cuando el que miente es el dato.
    """
    ajenos = set(already_failing)
    rojo_antes = [
        nodeid
        for nodeid, outcome in delivered.items()
        if outcome in FAILING and nodeid not in ajenos
    ]
    return OracleRun(
        kind=kind,
        repaired=[n for n in rojo_antes if after.get(n, MISSING) not in FAILING],
        still_failing=[n for n in rojo_antes if after.get(n, MISSING) in FAILING],
        broken=[
            nodeid
            for nodeid, outcome in delivered.items()
            if outcome not in FAILING and after.get(nodeid, MISSING) in FAILING
        ],
        outcomes=outcomes if outcomes is not None else dict(after),
        metrics=metrics or SuiteMetrics(),
    )


def run_oracle(
    kind: str,
    repo: Path,
    task: Task,
    timeout: int = 1800,
    *,
    session: SuiteSession | None = None,
    already_failing: Iterable[str] = (),
    image: str = DEFAULT_IMAGE,
    install_repo: bool = True,
    prepare: str | None = None,
    manifest: Path | None = None,
) -> OracleRun:
    """Corre uno de los dos controles sobre el árbol que se le entrega al agente.

    `repo` es el árbol de la condición CON el fallo dentro, que es lo que el
    agente recibe. La corrida de referencia es ese mismo árbol sin tocar, y esa
    corrida es literalmente lo que produce el no-op: por eso el no-op no gasta
    una segunda suite —correr dos veces el mismo árbol solo mediría flakiness, y
    si algún día quisiéramos medirla habría que decirlo, no obtenerla de rebote—.
    """
    if kind not in ORACLES:
        raise ValueError(f"oráculo desconocido: {kind}. Opciones: {', '.join(ORACLES)}")
    if session is not None:
        return _run_in(kind, session, task, already_failing, manifest)
    with SuiteSession(
        repo, image=image, timeout=timeout, install_repo=install_repo, prepare=prepare
    ) as abierta:
        return _run_in(kind, abierta, task, already_failing, manifest)


def _run_in(
    kind: str,
    session: SuiteSession,
    task: Task,
    already_failing: Iterable[str],
    manifest: Path | None,
) -> OracleRun:
    entregado = session.baseline()
    if kind == "no_op":
        despues = entregado
    else:
        relativa, arreglado = repaired_source(session.repo, task, manifest=manifest)
        roto = (session.repo / relativa).read_text(encoding="utf-8")
        session.write(relativa, arreglado)
        try:
            despues = session.outcomes()
        finally:
            # El contenedor vuelve al árbol entregado: la referencia sigue
            # siendo válida para el control siguiente sobre la misma sesión.
            session.write(relativa, roto)
    return compare_to_delivered(
        kind, entregado, despues, already_failing=already_failing, metrics=session.metrics
    )
