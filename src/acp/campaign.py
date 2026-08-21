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
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from acp.cli import transform_repo
from acp.tasks.inject import apply_patch, module_path
from acp.tasks.models import Task
from acp.transforms.base import copy_tree


def clean_tree_name(source: Path, condition: str) -> str:
    """Cómo se llama el árbol sano de una condición, con el repo en el nombre.

    El nombre importa porque de él sale el del contenedor. Sin el repo delante,
    dos campañas sobre repositorios distintos en la misma máquina —lo normal en
    una VM con CPU de sobra— pedirían el mismo `acp-T0-clean` y una mataría el
    contenedor de la otra a mitad de celda, que se lee como un agente que rompió
    algo.
    """
    return f"{Path(source).name}-{condition}-clean"


def cell_tree_name(source: Path, condition: str, task_id: str, run: int = 0) -> str:
    """Y el del árbol con el fallo, por la misma razón."""
    sufijo = f"-r{run}" if run else ""
    return f"{Path(source).name}-{condition}-{task_id}{sufijo}"


def _clear(destination: Path, source: Path) -> None:
    """Deja el sitio libre para rehacer el árbol de una celda.

    Reanudar es el caso normal aquí y la corrida muerta deja su árbol a medias
    —puede tener media transformación aplicada—, así que se rehace en vez de
    reutilizarse: medir sobre un árbol a medio transformar es peor que perder
    los minutos de volver a construirlo.

    La guarda no es paranoia de manual: el destino sale de un `--workdir` de la
    línea de comandos, y un borrado que alcanzase al clon de referencia se
    llevaría el árbol contra el que se verifica toda la equivalencia.
    """
    destination = Path(destination)
    if not destination.exists():
        return
    source = Path(source).resolve()
    resolved = destination.resolve()
    if resolved == source or resolved in source.parents:
        raise ValueError(
            f"el destino {resolved} contiene el clon de referencia; no se borra"
        )
    shutil.rmtree(resolved)


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
    _clear(destination, source)
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


def already_measured(log: Path) -> set[tuple[str, str, int]]:
    """Las celdas que el registro ya da por medidas, para no repetirlas.

    La clave lleva el número de pasada: la varianza medida es el problema
    central —la misma tarea, condición y modelo dio fallo, acierto y acierto en
    tres pasadas— así que una celda repetida no puede leerse como la misma celda
    ya hecha. Los registros escritos antes de que existieran las repeticiones
    cuentan como la pasada 0.

    Una celda que salió **no medible** no cuenta: eso fue fontanería, y darla
    por hecha congelaría el hueco justo donde el diseño avisa del riesgo. Al
    arreglar la fontanería y reanudar, vuelve a intentarse.
    """
    log = Path(log)
    if not log.is_file():
        return set()
    medidas: set[tuple[str, str, int]] = set()
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("measurable") is False:
            continue
        medidas.add((record["condition"], record["task_id"], record.get("run", 0)))
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
    runs: int = 1,
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
            for run in range(runs):
                if (condition, task.task_id, run) in done:
                    continue
                record = measure(condition, CONDITIONS[condition], task, run=run)
                # Antes de seguir, no después del bucle: lo que no está en
                # disco no sobrevive a que la máquina se caiga en la siguiente.
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
    clean: dict[str, str] | None = None,
    run: int = 0,
) -> dict:
    """Mide una celda: monta los dos árboles, deriva el oráculo y deja actuar.

    Los dos árboles son la única forma de saber qué mide la celda. El sano dice
    qué tests pasan bajo esta degradación —que no es lo mismo que bajo el
    original— y el que lleva el fallo dice cuáles rompió el fallo. La diferencia
    es el oráculo, y si está vacía la celda no mide al agente y se declara sin
    gastar un token.
    """
    workdir = Path(workdir)
    # Las seis tareas de una condición comparten el mismo árbol sano, así que
    # quien recorre la condición lo mide una vez y lo pasa aquí. Sin esto son
    # cinco transformaciones y cinco suites de más por condición.
    if clean is None:
        clean_tree = cell_tree(
            source, None, transform_ids, workdir / clean_tree_name(source, condition)
        )
        with open_session(clean_tree, tests_from=tests_from) as session:
            clean = session.outcomes()

    faulty_tree = cell_tree(
        source, task, transform_ids,
        workdir / cell_tree_name(source, condition, task.task_id, run),
    )
    with open_session(faulty_tree, tests_from=tests_from) as session:
        faulty = session.outcomes()
        oracle = cell_oracle(clean, faulty)
        record = {
            "condition": condition,
            "task_id": task.task_id,
            "run": run,
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


# Las que dejan el árbol sin correspondencia con lo que declara su `pyproject`:
# mueven definiciones entre ficheros (B1), renombran los ficheros (B2) o los
# concatenan (B5). Instalar el repo ahí mide el paquete que pip baje de PyPI.
MOVE_CODE = {"B1", "B2", "B5"}


def installs_the_repo(transform_ids: list[str]) -> bool:
    """Si esta condición admite instalar el repo bajo prueba (§5.6)."""
    return not (set(transform_ids) & MOVE_CODE)


def suite_to_restore(transform_ids: list[str], tests: Path) -> Path | None:
    """La suite que hay que devolverle al contenedor para poder validar.

    B4 la esconde del agente, que es lo que la condición mide; pero el oráculo
    la necesita. Se le devuelve al contenedor sin que aparezca en el árbol que
    el agente explora.
    """
    return tests if "B4" in set(transform_ids) else None


def load_tasks(directory: Path) -> list[Task]:
    """Las tareas de un repositorio, en orden estable por su identificador."""
    tasks = [
        Task.from_json(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(Path(directory).glob("*.json"))
    ]
    return sorted(tasks, key=lambda task: task.task_id)


def run_all(
    source: Path,
    tasks: list[Task],
    log: Path,
    *,
    model: str,
    image: str = "python:3.12",
    workdir: Path,
    tests: Path | None = None,
    conditions: list[str] | None = None,
    timeout: int = 1800,
    max_turns: int = 40,
    grep: bool = True,
    runs: int = 1,
) -> list[dict]:
    """La campaña con las piezas de verdad: contenedor, suite y modelo.

    El árbol sano de cada condición se mide una vez y se reparte entre sus
    tareas. Lo que decide si el repo se instala y si hay que devolver la suite lo
    dice la condición, no un flag de la línea de comandos.
    """
    from acp.agent.loop import solve
    from acp.tasks.validate import SuiteSession

    source = Path(source)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    tests = Path(tests) if tests else source / "tests"
    records: list[dict] = []
    done = already_measured(Path(log))

    for condition in conditions or list(CONDITIONS):
        transform_ids = CONDITIONS[condition]
        pendientes = [
            (task, run)
            for task in tasks
            for run in range(runs)
            if (condition, task.task_id, run) not in done
        ]
        if not pendientes:
            continue

        install_repo = installs_the_repo(transform_ids)
        restore = suite_to_restore(transform_ids, tests)

        def open_session(tree, tests_from=None):
            return SuiteSession(
                repo=tree,
                image=image,
                timeout=timeout,
                install_repo=install_repo,
                tests_from=tests_from,
            )

        def ask_agent(session, prompt):
            return solve(session, prompt, model, grep=grep, max_turns=max_turns)

        clean_tree = cell_tree(
            source, None, transform_ids, workdir / clean_tree_name(source, condition)
        )
        with open_session(clean_tree, tests_from=restore) as session:
            clean = session.outcomes()

        for task, run in pendientes:
            record = measure_cell(
                source,
                condition,
                transform_ids,
                task,
                workdir=workdir,
                open_session=open_session,
                ask_agent=ask_agent,
                tests_from=restore,
                clean=clean,
                run=run,
            )
            record["model"] = model
            record["install_repo"] = install_repo
            with Path(log).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            records.append(record)
            print(
                f"[{condition}] {task.task_id}"
                f"{f' r{run}' if runs > 1 else ''}: "
                f"{'resuelto' if record['solved'] else record.get('failure_mode')}"
                f" (medible={record['measurable']})",
                flush=True,
            )
    return records



def _lines(logs: Path | list[Path]) -> list[str]:
    """Las líneas de uno o varios registros, en el orden en que se dieron."""
    paths = [logs] if isinstance(logs, (str, Path)) else list(logs)
    salida: list[str] = []
    for path in paths:
        path = Path(path)
        if path.is_file():
            salida.extend(path.read_text(encoding="utf-8").splitlines())
    return salida


def summarise(logs: Path | list[Path]) -> dict[str, dict]:
    """El 2×2 tal como se publica: tasa sobre las celdas que miden algo.

    Acepta varios registros porque cada condición corre en su propio proceso y
    escribe el suyo: dos procesos sobre el mismo jsonl se pisan, y la
    reanudación lo lee para saber qué celdas faltan.

    Las no medibles se cuentan aparte y nunca entran en el denominador. Meter
    ahí una celda que falló por fontanería la presenta como un agente que
    fracasó, y ese es el error que hundió T2 entera en la primera tanda.
    """
    resumen: dict[str, dict] = {}
    for line in _lines(logs):
        if not line.strip():
            continue
        record = json.loads(line)
        entrada = resumen.setdefault(
            record["condition"],
            {"measurable": 0, "solved": 0, "unmeasurable": 0, "rate": 0.0,
             "by_stratum": {}, "failure_modes": {}},
        )
        if not record.get("measurable"):
            entrada["unmeasurable"] += 1
            continue
        entrada["measurable"] += 1
        resuelto = bool(record.get("solved"))
        entrada["solved"] += int(resuelto)
        estrato = record.get("stratum", "generic")
        hechas, total = entrada["by_stratum"].get(estrato, (0, 0))
        entrada["by_stratum"][estrato] = (hechas + int(resuelto), total + 1)
        if not resuelto:
            modo = record.get("failure_mode", "sin clasificar")
            entrada["failure_modes"][modo] = entrada["failure_modes"].get(modo, 0) + 1
    for entrada in resumen.values():
        if entrada["measurable"]:
            entrada["rate"] = entrada["solved"] / entrada["measurable"]
    return resumen

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="acp-campaign")
    parser.add_argument("source", type=Path, help="clon del repositorio bajo prueba")
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--image", default="python:3.12")
    parser.add_argument("--tests", type=Path, default=None)
    parser.add_argument("--conditions", default=None, help="p.ej. T0,T2")
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--poor", action="store_true", help="dotación sin búsqueda")
    parser.add_argument(
        "--runs", type=int, default=1,
        help="pasadas por celda; el diseño pide 3 en las celdas de titular",
    )
    args = parser.parse_args(argv)

    conditions = args.conditions.split(",") if args.conditions else None
    records = run_all(
        args.source,
        load_tasks(args.tasks),
        args.log,
        model=args.model,
        image=args.image,
        workdir=args.workdir,
        tests=args.tests,
        conditions=conditions,
        max_turns=args.max_turns,
        grep=not args.poor,
        runs=args.runs,
    )
    medibles = [r for r in records if r["measurable"]]
    print(
        f"\n{len(records)} celdas nuevas, {len(medibles)} medibles, "
        f"{sum(1 for r in medibles if r['solved'])} resueltas"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
