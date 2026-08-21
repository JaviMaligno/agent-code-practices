"""El árbol de una celda: primero el fallo, y encima la degradación.

El orden importa y es el hallazgo que costó una campaña entera. El parche que
define una tarea es un diff unificado contra el árbol **original**, con sus
hunks anclados a números de línea. A1 y A3 desplazan todas las líneas de todos
los ficheros, así que aplicar el parche sobre el árbol ya degradado no encaja en
ningún sitio: en la primera tanda, T1 y T3 no midieron nada y T2 salió entera
como «el agente rompió otra cosa».

Aplicarlo por contenido en vez de por línea no arregla el caso general: A2
renombra los identificadores del cuerpo y A3 le cambia el formato, así que el
texto del hunk tampoco existe en el árbol degradado. Y reinyectar la mutación
desde el catálogo tampoco vale para todas: las tareas de dominio se escriben a
mano y no tienen una forma del catálogo que repetir.

Lo que sí vale para todas es invertir el orden. Las transformaciones son
semánticamente equivalentes, luego preservan el fallo inyectado; que lo
preserven de verdad no se supone, se comprueba (`tests/test_campaign.py`).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from acp.cli import transform_repo
from acp.tasks.inject import apply_patch, module_path
from acp.tasks.models import Task
from acp.transforms.base import copy_tree


def cell_tree(
    source: Path,
    task: Task | None,
    transform_ids: list[str],
    destination: Path,
    manifest: Path | None = None,
) -> Path:
    """El árbol que explora el agente en la celda `(condición, tarea)`.

    Con `task` a `None` devuelve la misma condición sin fallo, que es lo que la
    campaña necesita para saber qué tests pasaban antes de romper nada.
    """
    source = Path(source)
    if task is None:
        return transform_repo(source, list(transform_ids), destination, manifest)

    # El árbol con el fallo es intermedio y no lo ve nadie: se degrada y se tira.
    # Vive en un temporal del sistema y no junto al destino porque ahí dentro no
    # entra nada que el agente pudiera leer como pista de lo que se le hizo.
    with tempfile.TemporaryDirectory(prefix="acp-faulty-") as temporal:
        faulty = copy_tree(source, Path(temporal) / source.name)
        target = module_path(faulty, task.module)
        target.write_text(
            apply_patch(target.read_text(encoding="utf-8"), task.patch),
            encoding="utf-8",
        )
        return transform_repo(faulty, list(transform_ids), destination, manifest)


@dataclass
class CellOracle:
    """Qué tiene que arreglar el agente en esta celda, y si la celda mide algo.

    Se deriva de los dos árboles degradados —el sano y el que lleva el fallo— y
    no de los nodeids que la tarea guarda del original. B1 mueve la definición a
    otro fichero, así que el doctest de la función pasa a ejecutarse con otro
    nodeid; B4 saca la suite del árbol. Traducir nodeids a mano sería adivinar;
    esto se lo pregunta al árbol donde de verdad se mide.
    """

    fail_to_pass: list[str]
    pass_to_pass: list[str]
    measurable: bool
    why: str


def cell_oracle(clean: dict[str, str], faulty: dict[str, str]) -> CellOracle:
    """El oráculo de la celda, a partir de lo que la suite responde en ella."""
    fail_to_pass = sorted(
        name
        for name, outcome in clean.items()
        if outcome == "passed" and faulty.get(name) == "failed"
    )
    pass_to_pass = sorted(
        name
        for name, outcome in clean.items()
        if outcome == "passed" and faulty.get(name) == "passed"
    )
    if not fail_to_pass:
        return CellOracle(
            fail_to_pass=[],
            pass_to_pass=pass_to_pass,
            measurable=False,
            why="el fallo inyectado no pone en rojo ningún test de este árbol",
        )
    return CellOracle(
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
        measurable=True,
        why=f"{len(fail_to_pass)} test(s) en rojo por el fallo",
    )


def already_measured(log: Path) -> set[tuple[str, str]]:
    """Las celdas que el registro ya da por medidas, para no repetirlas.

    Una celda que salió **no medible** no cuenta: eso fue fontanería, y darla
    por hecha congelaría el hueco justo donde el diseño avisa del riesgo. Al
    arreglar la fontanería y reanudar, vuelve a intentarse.
    """
    log = Path(log)
    if not log.is_file():
        return set()
    medidas: set[tuple[str, str]] = set()
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("measurable") is False:
            continue
        medidas.add((record["condition"], record["task_id"]))
    return medidas


# El 2×2 de titular, tal y como quedó registrado antes de correr nada (§6.1):
# familia A es cómo está escrito el fichero abierto, familia B es dónde mirar
# antes de abrir ninguno. B5 no está aquí a propósito —el tamaño se mide como
# curva de dosis y no como celda del 2×2— y las ocho de knock-out y add-back
# salen de T0 y de T3, que son dos de estas cuatro.
CONDITIONS: dict[str, list[str]] = {
    "T0": [],
    "T1": ["A1", "A2", "A3", "A4"],
    "T2": ["B1", "B2", "B3", "B4"],
    "T3": ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"],
}


def run_campaign(
    source: Path,
    tasks: list[Task],
    log: Path,
    *,
    measure,
    conditions: list[str] | None = None,
) -> list[dict]:
    """Recorre las celdas que faltan y apunta cada una en cuanto termina.

    `measure` queda fuera a propósito: monta los árboles, levanta el contenedor
    y llama al modelo, y nada de eso se puede ejercitar en un test unitario. Lo
    que sí se ejercita aquí es la decisión —qué celda toca y cuándo se escribe—,
    que es donde se perdieron dos corridas enteras.
    """
    log = Path(log)
    done = already_measured(log)
    records: list[dict] = []
    for condition in conditions or list(CONDITIONS):
        for task in tasks:
            if (condition, task.task_id) in done:
                continue
            record = measure(condition, CONDITIONS[condition], task)
            # Antes de seguir, no después del bucle: lo que no está en disco no
            # sobrevive a que la máquina se caiga en la celda siguiente.
            with log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            records.append(record)
    return records


def task_prompt(task: Task, failing: list[str]) -> str:
    """El enunciado que ve el agente.

    Da lo que tiene quien recibe un informe de fallo —qué tests están en rojo— y
    no dónde está el fallo. Nombrar el fichero anularía la familia B entera: si
    el enunciado dice dónde mirar, medir si el repositorio lo dice deja de tener
    sentido. Por lo mismo no nombra la función ni el módulo, que están en la
    `Task` y sería cómodo interpolar.
    """
    listado = "\n".join(f"  {name}" for name in failing)
    return (
        "Este repositorio tiene un fallo. Al correr su suite, estos tests "
        f"están en rojo:\n\n{listado}\n\n"
        "Encuentra la causa y arréglala en el código. No modifiques los tests "
        "ni sus expectativas: el arreglo tiene que estar en el código que los "
        "tests comprueban. Cuando creas que está arreglado, corre los tests "
        "para confirmarlo."
    )


def measure_cell(
    source: Path,
    condition: str,
    transform_ids: list[str],
    task: Task,
    *,
    workdir: Path,
    open_session,
    ask_agent,
    tests_from: Path | None = None,
) -> dict:
    """Mide una celda: monta los dos árboles, deriva el oráculo y deja actuar.

    Los dos árboles son la única forma de saber qué mide la celda. El sano dice
    qué tests pasan bajo esta degradación —que no es lo mismo que bajo el
    original— y el que lleva el fallo dice cuáles rompió el fallo. La diferencia
    es el oráculo, y si está vacía la celda no mide al agente y se declara sin
    gastar un token.
    """
    workdir = Path(workdir)
    clean_tree = cell_tree(source, None, transform_ids, workdir / f"{condition}-clean")
    with open_session(clean_tree, tests_from=tests_from) as session:
        clean = session.outcomes()

    faulty_tree = cell_tree(
        source, task, transform_ids, workdir / f"{condition}-{task.task_id}"
    )
    with open_session(faulty_tree, tests_from=tests_from) as session:
        faulty = session.outcomes()
        oracle = cell_oracle(clean, faulty)
        record = {
            "condition": condition,
            "task_id": task.task_id,
            "stratum": task.stratum,
            "applied": list(transform_ids),
            "measurable": oracle.measurable,
            "why": oracle.why,
            "fail_to_pass": oracle.fail_to_pass,
            "solved": False,
        }
        if not oracle.measurable:
            return record

        trace = ask_agent(session, task_prompt(task, oracle.fail_to_pass))
        after = session.outcomes()

    arreglados = [name for name in oracle.fail_to_pass if after.get(name) == "passed"]
    rotos = [name for name in oracle.pass_to_pass if after.get(name) != "passed"]
    record["solved"] = len(arreglados) == len(oracle.fail_to_pass) and not rotos
    record["broke_others"] = rotos
    if trace is not None:
        record |= {
            "turns": getattr(trace, "turns", None),
            "first_edit_turn": getattr(trace, "first_edit_turn", None),
            "prompt_tokens": getattr(trace, "prompt_tokens", None),
            "completion_tokens": getattr(trace, "completion_tokens", None),
            "stopped_because": getattr(trace, "stopped_because", None),
            "regions_seen": len(getattr(trace, "seen", []) or []),
        }
    if record["solved"]:
        record["failure_mode"] = "resuelto"
    elif rotos:
        record["failure_mode"] = "rompió otra cosa"
    elif not arreglados:
        record["failure_mode"] = "no lo arregló"
    else:
        record["failure_mode"] = "lo arregló a medias"
    return record
