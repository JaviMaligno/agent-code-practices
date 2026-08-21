"""El árbol que el agente explora en una celda de la campaña.

Lo que se prueba aquí es comportamiento y no texto: la pregunta es si el fallo
inyectado sigue vivo después de degradar el repositorio, y eso se responde
ejecutando el código, no buscando una cadena en un fichero. Con A3 el formato
del fichero ya no se parece al original y cualquier aserción sobre su texto
mide la transformación en vez de la tarea.
"""

import json
import subprocess
import sys
from pathlib import Path

from acp.campaign import (
    CONDITIONS,
    already_measured,
    cell_oracle,
    cell_tree,
    installs_the_repo,
    measure_cell,
    run_campaign,
    suite_to_restore,
    task_prompt,
)
from acp.tasks.models import Task
from acp.tasks.inject import inject


def build(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        '"""Tarifas."""\n'
        "\n"
        "\n"
        "def rate(value: int) -> int:\n"
        '    """El doble, si el valor es positivo.\n'
        "\n"
        "    >>> rate(2)\n"
        "    4\n"
        '    """\n'
        "    if value > 0:\n"
        "        return value * 2\n"
        "    return 0\n",
        encoding="utf-8",
    )
    return root


def rate_of(tree: Path) -> str:
    """Lo que el árbol responde de verdad al llamar a la función tocada."""
    ejecucion = subprocess.run(
        [sys.executable, "-c", "import pkg.core; print(pkg.core.rate(2))"],
        cwd=tree,
        capture_output=True,
        text=True,
    )
    assert ejecucion.returncode == 0, ejecucion.stderr
    return ejecucion.stdout.strip()


def test_the_fault_survives_a_transformation_that_shifts_every_line(tmp_path: Path):
    """El primer runner de la campaña aplicaba el parche DESPUÉS de transformar,
    y A1 y A3 desplazan todas las líneas: el hunk anclado a `@@ -118` no encajaba
    en ningún sitio, así que T1 y T3 no midieron nada y las 12 celdas se leyeron
    como fallos del agente.

    El fallo se inyecta donde el parche encaja —el original— y la degradación se
    aplica encima. Que el fallo sobreviva no se supone: se comprueba aquí.
    """
    source = build(tmp_path / "repo")
    task = inject(source, "pkg.core", "rate", "invert_condition")

    tree = cell_tree(source, task, ["A3"], tmp_path / "cell")

    assert rate_of(tree) == "0"


def test_the_same_degradation_without_the_fault_keeps_the_program_intact(tmp_path: Path):
    """El control del test anterior. Sin él, un árbol de la celda roto por la
    transformación —no por la tarea— daría el mismo 0 y pasaría por medida."""
    source = build(tmp_path / "repo")

    tree = cell_tree(source, None, ["A3"], tmp_path / "clean")

    assert rate_of(tree) == "4"


def test_a_cell_whose_fault_breaks_nothing_is_declared_unmeasurable(tmp_path: Path):
    """La celda cuyo fallo no pone en rojo ningún test no mide al agente: mide la
    fontanería. Contarla como un fallo del agente es el error de lectura número
    uno del diseño, y es el que produjo seis ceros falsos en la primera tanda."""
    clean = {"t::uno": "passed", "t::dos": "passed"}
    faulty = {"t::uno": "passed", "t::dos": "passed"}

    oracle = cell_oracle(clean, faulty)

    assert oracle.measurable is False
    assert oracle.fail_to_pass == []
    assert "no pone en rojo" in oracle.why


def test_the_oracle_of_a_cell_is_derived_from_the_tree_it_is_measured_in(tmp_path: Path):
    """Y no de los nodeids que la tarea trae del árbol original. B1 mueve la
    definición a otro fichero, así que el doctest de la función se ejecuta con
    otro nodeid; B4 saca la suite del árbol. Derivarlo aquí es lo que hace que el
    oráculo hable del árbol donde de verdad se mide."""
    clean = {"t::uno": "passed", "t::dos": "passed", "t::tres": "skipped"}
    faulty = {"t::uno": "failed", "t::dos": "passed", "t::tres": "skipped"}

    oracle = cell_oracle(clean, faulty)

    assert oracle.measurable is True
    assert oracle.fail_to_pass == ["t::uno"]
    assert oracle.pass_to_pass == ["t::dos"]


def test_a_campaign_resumes_instead_of_repeating_what_it_already_measured(tmp_path: Path):
    """Dos corridas se perdieron por falta de memoria de la máquina, y con ellas
    horas de agente ya pagadas. Una celda cuesta minutos y dinero: la campaña
    tiene que poder morir a la mitad y seguir donde estaba."""
    log = tmp_path / "campana.jsonl"
    log.write_text(
        '{"condition": "T0", "task_id": "uno", "solved": true}\n'
        '{"condition": "T0", "task_id": "dos", "solved": false}\n',
        encoding="utf-8",
    )

    hecho = already_measured(log)

    assert hecho == {("T0", "uno"), ("T0", "dos")}


def test_a_missing_log_means_nothing_has_been_measured_yet(tmp_path: Path):
    assert already_measured(tmp_path / "no-existe.jsonl") == set()


def test_an_unmeasurable_cell_does_not_count_as_measured(tmp_path: Path):
    """Si la celda salió no medible por fontanería, arreglar la fontanería y
    reanudar tiene que volver a intentarla. Darla por hecha congelaría el hueco
    en el conjunto de datos justo donde el diseño avisa del riesgo."""
    log = tmp_path / "campana.jsonl"
    log.write_text(
        '{"condition": "T1", "task_id": "uno", "measurable": false}\n'
        '{"condition": "T1", "task_id": "dos", "measurable": true, "solved": true}\n',
        encoding="utf-8",
    )

    assert already_measured(log) == {("T1", "dos")}


def fake_task(task_id: str) -> Task:
    return Task(
        task_id=task_id,
        repo="demo",
        module="pkg.core",
        symbol="rate",
        stratum="generic",
        patch="--- a/pkg/core.py\n+++ b/pkg/core.py\n",
        fail_to_pass=["t::uno"],
    )


def test_the_campaign_checkpoints_each_cell_as_soon_as_it_finishes(tmp_path: Path):
    """La campaña murió dos veces por memoria de la máquina. Escribir el registro
    al final habría perdido las dos horas enteras; escribirlo por celda pierde
    la celda en curso y nada más."""
    log = tmp_path / "campana.jsonl"
    medidas: list[str] = []

    def measure(condition, transform_ids, task):
        medidas.append(f"{condition}/{task.task_id}")
        if len(medidas) == 3:
            raise MemoryError("la máquina se quedó sin memoria")
        return {"condition": condition, "task_id": task.task_id, "solved": True}

    try:
        run_campaign(
            tmp_path / "repo",
            [fake_task("uno"), fake_task("dos"), fake_task("tres")],
            log,
            measure=measure,
            conditions=["T0"],
        )
    except MemoryError:
        pass

    escritas = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["task_id"] for r in escritas] == ["uno", "dos"]


def test_the_campaign_skips_the_cells_the_log_already_has(tmp_path: Path):
    log = tmp_path / "campana.jsonl"
    log.write_text('{"condition": "T0", "task_id": "uno", "solved": true}\n', encoding="utf-8")
    pedidas: list[str] = []

    def measure(condition, transform_ids, task):
        pedidas.append(task.task_id)
        return {"condition": condition, "task_id": task.task_id, "solved": False}

    run_campaign(
        tmp_path / "repo",
        [fake_task("uno"), fake_task("dos")],
        log,
        measure=measure,
        conditions=["T0"],
    )

    assert pedidas == ["dos"]


def test_the_headline_grid_is_the_two_by_two_the_design_registered(tmp_path: Path):
    """Las cuatro condiciones y sus transformaciones se fijan aquí y no en el
    script de turno: son la hipótesis registrada (§6.1) y elegirlas al correr
    sería elegirlas post-hoc."""
    assert CONDITIONS["T0"] == []
    assert CONDITIONS["T1"] == ["A1", "A2", "A3", "A4"]
    assert CONDITIONS["T2"] == ["B1", "B2", "B3", "B4"]
    assert CONDITIONS["T3"] == ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"]


class FakeSession:
    """Una sesión de suite que responde lo que el test le diga, sin contenedor."""

    def __init__(self, outcomes: dict[str, str]):
        self._outcomes = outcomes

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def outcomes(self) -> dict[str, str]:
        return self._outcomes


def test_an_unmeasurable_cell_never_reaches_the_agent(tmp_path: Path):
    """Llamar al modelo en una celda cuyo fallo no rompe nada gasta inferencia
    para nada y, peor, deja un resultado escrito que se lee como un agente que
    fracasó. La celda se declara antes de gastar un token."""
    source = build(tmp_path / "repo")
    task = inject(source, "pkg.core", "rate", "invert_condition")
    verde = {"pkg/core.py::pkg.core": "passed"}
    llamadas: list[str] = []

    record = measure_cell(
        source,
        "T0",
        [],
        task,
        workdir=tmp_path / "work",
        open_session=lambda tree, tests_from=None: FakeSession(verde),
        ask_agent=lambda session, prompt: llamadas.append(prompt),
    )

    assert llamadas == []
    assert record["measurable"] is False
    assert record["solved"] is False


def test_the_prompt_names_the_failing_tests_and_not_the_file_to_open(tmp_path: Path):
    """El enunciado da lo que tiene quien recibe un bug report —los tests en
    rojo— y no el fichero donde está el fallo. Nombrarlo anularía la familia B
    entera: si el enunciado dice dónde mirar, medir si el repositorio lo dice
    deja de tener sentido."""
    task = fake_task("uno")
    task.fail_to_pass = ["tests/test_algo.py::test_rate"]

    prompt = task_prompt(task, ["tests/test_algo.py::test_rate"])

    assert "tests/test_algo.py::test_rate" in prompt
    assert "pkg/core.py" not in prompt
    assert "rate" not in prompt.replace("test_rate", "")


def test_the_clean_tree_of_a_condition_is_measured_once_and_reused(tmp_path: Path):
    """Las seis tareas de una condición comparten el mismo árbol sano: es la
    misma degradación sin fallo. Medirlo por celda son cinco transformaciones y
    cinco suites de más por condición, cerca de una hora en la campaña entera —y
    en una máquina que ya se cayó dos veces, una hora de exposición gratis."""
    source = build(tmp_path / "repo")
    task = inject(source, "pkg.core", "rate", "invert_condition")
    abiertas: list[str] = []

    def open_session(tree, tests_from=None):
        abiertas.append(Path(tree).name)
        return FakeSession({"pkg/core.py::pkg.core": "passed"})

    measure_cell(
        source,
        "T0",
        [],
        task,
        workdir=tmp_path / "work",
        open_session=open_session,
        ask_agent=lambda session, prompt: None,
        clean={"pkg/core.py::pkg.core": "passed"},
    )

    assert not any(name.endswith("-clean") for name in abiertas)


def test_a_condition_that_moves_code_must_not_install_the_repo():
    """B1, B2 y B5 dejan el árbol sin correspondencia con lo que declara su
    `pyproject` (§5.6): instalarlo ahí mide el paquete de PyPI en vez del árbol
    transformado, y la celda entera se lee como un resultado. La decisión se
    deriva de la condición y no de un flag que se pueda olvidar."""
    assert installs_the_repo(CONDITIONS["T0"]) is True
    assert installs_the_repo(CONDITIONS["T1"]) is True
    assert installs_the_repo(CONDITIONS["T2"]) is False
    assert installs_the_repo(CONDITIONS["T3"]) is False
    assert installs_the_repo(["B5"]) is False


def test_a_condition_that_hides_the_suite_needs_it_given_back_to_validate(tmp_path: Path):
    """B4 saca la suite del árbol, que es lo que la condición mide. Pero validar
    sin ella cuenta las seis tareas como «rompió otra cosa», que es exactamente
    lo que pasó en la primera tanda."""
    tests = tmp_path / "tests-originales"

    assert suite_to_restore(CONDITIONS["T0"], tests) is None
    assert suite_to_restore(CONDITIONS["T1"], tests) is None
    assert suite_to_restore(CONDITIONS["T2"], tests) == tests
    assert suite_to_restore(CONDITIONS["T3"], tests) == tests
