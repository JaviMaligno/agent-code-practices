"""El árbol que el agente explora en una celda de la campaña.

Lo que se prueba aquí es comportamiento y no texto: la pregunta es si el fallo
inyectado sigue vivo después de degradar el repositorio, y eso se responde
ejecutando el código, no buscando una cadena en un fichero. Con A3 el formato
del fichero ya no se parece al original y cualquier aserción sobre su texto
mide la transformación en vez de la tarea.
"""

import json
import pytest
import subprocess
import sys
from pathlib import Path

from acp.campaign import (
    ALL_CONDITIONS,
    BREAKDOWN,
    CURVE,
    CONDITIONS,
    already_measured,
    cell_oracle,
    cell_tree,
    cell_tree_name,
    clean_tree_name,
    installs_the_repo,
    measure_cell,
    run_campaign,
    suite_to_restore,
    summarise,
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

    assert hecho == {("T0", "uno", 0), ("T0", "dos", 0)}


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

    assert already_measured(log) == {("T1", "dos", 0)}


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

    def measure(condition, transform_ids, task, run=0):
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

    def measure(condition, transform_ids, task, run=0):
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




def test_the_summary_counts_resolve_rate_over_measurable_cells_only(tmp_path: Path):
    """La tasa se calcula sobre las celdas que miden algo. Meter en el
    denominador una celda que salió no medible por fontanería la cuenta como un
    agente que fracasó, que es el error que hundió T2 en la primera tanda."""
    log = tmp_path / "campana.jsonl"
    log.write_text(
        "\n".join([
            '{"condition":"T0","task_id":"a","stratum":"generic","measurable":true,"solved":true}',
            '{"condition":"T0","task_id":"b","stratum":"domain","measurable":true,"solved":false}',
            '{"condition":"T0","task_id":"c","stratum":"generic","measurable":false,"solved":false}',
        ]) + "\n",
        encoding="utf-8",
    )

    resumen = summarise(log)

    assert resumen["T0"]["measurable"] == 2
    assert resumen["T0"]["solved"] == 1
    assert resumen["T0"]["unmeasurable"] == 1
    assert resumen["T0"]["rate"] == 0.5


def test_the_summary_keeps_the_strata_apart(tmp_path: Path):
    """Genéricas y de dominio responden a cosas distintas (§3.3.1) y mezclarlas
    en una sola tasa esconde justo la mitad del diseño."""
    log = tmp_path / "campana.jsonl"
    log.write_text(
        "\n".join([
            '{"condition":"T1","task_id":"a","stratum":"generic","measurable":true,"solved":true}',
            '{"condition":"T1","task_id":"b","stratum":"generic","measurable":true,"solved":true}',
            '{"condition":"T1","task_id":"c","stratum":"domain","measurable":true,"solved":false}',
        ]) + "\n",
        encoding="utf-8",
    )

    resumen = summarise(log)

    assert resumen["T1"]["by_stratum"]["generic"] == (2, 2)
    assert resumen["T1"]["by_stratum"]["domain"] == (0, 1)


def test_a_cell_can_be_built_again_over_the_tree_a_dead_run_left_behind(tmp_path: Path):
    """Reanudar es el caso normal en esta campaña, no la excepción: la máquina ya
    se cayó dos veces. El árbol que dejó la corrida muerta está a medias —puede
    tener media transformación aplicada— así que se rehace, no se reutiliza:
    medir sobre un árbol a medio transformar es peor que perder los minutos.
    """
    source = build(tmp_path / "repo")
    destination = tmp_path / "cell"
    cell_tree(source, None, ["A4"], destination)
    (destination / "resto-de-la-corrida-muerta.txt").write_text("x", encoding="utf-8")

    tree = cell_tree(source, None, ["A4"], destination)

    assert rate_of(tree) == "4"
    assert not (tree / "resto-de-la-corrida-muerta.txt").exists()


def test_the_summary_reads_the_campaign_split_across_one_log_per_condition(tmp_path: Path):
    """Cada condición corre en su propio proceso y escribe su propio log: dos
    procesos sobre el mismo jsonl se pisan, y la reanudación lo lee para saber
    qué falta. El resumen que va al artículo tiene que juntarlos."""
    (tmp_path / "campana-T0.jsonl").write_text(
        '{"condition":"T0","task_id":"a","stratum":"generic","measurable":true,"solved":true}\n',
        encoding="utf-8",
    )
    (tmp_path / "campana-T1.jsonl").write_text(
        '{"condition":"T1","task_id":"a","stratum":"generic","measurable":true,"solved":false,'
        '"failure_mode":"editó mal"}\n',
        encoding="utf-8",
    )

    resumen = summarise(sorted(tmp_path.glob("campana-T*.jsonl")))

    assert resumen["T0"]["rate"] == 1.0
    assert resumen["T1"]["rate"] == 0.0
    assert resumen["T1"]["failure_modes"] == {"editó mal": 1}


def test_a_cell_can_be_measured_more_than_once_and_the_runs_do_not_collide(tmp_path: Path):
    """La varianza medida es el problema central: la misma tarea, condición y
    modelo dio fallo (27 turnos), acierto (17) y acierto (40) en tres pasadas.
    Con una sola pasada por celda no hay conclusión que sostener, así que la
    campaña tiene que poder repetir una celda sin que la repetición se lea como
    la misma celda ya hecha."""
    log = tmp_path / "campana.jsonl"
    log.write_text(
        '{"condition":"T0","task_id":"uno","run":0,"measurable":true,"solved":true}\n'
        '{"condition":"T0","task_id":"uno","run":1,"measurable":true,"solved":false}\n',
        encoding="utf-8",
    )

    hecho = already_measured(log)

    assert ("T0", "uno", 0) in hecho
    assert ("T0", "uno", 1) in hecho
    assert ("T0", "uno", 2) not in hecho


def test_a_log_without_run_numbers_still_counts_as_the_first_run(tmp_path: Path):
    """Las celdas ya medidas se escribieron antes de que existieran las
    repeticiones. Reanudar sobre ellas no puede volver a medirlas: son la
    pasada 0."""
    log = tmp_path / "campana.jsonl"
    log.write_text(
        '{"condition":"T0","task_id":"uno","measurable":true,"solved":true}\n',
        encoding="utf-8",
    )

    assert already_measured(log) == {("T0", "uno", 0)}


def test_the_campaign_repeats_every_cell_the_requested_number_of_times(tmp_path: Path):
    pedidas: list[tuple] = []

    def measure(condition, transform_ids, task, run=0):
        pedidas.append((condition, task.task_id, run))
        return {"condition": condition, "task_id": task.task_id, "run": run,
                "measurable": True, "solved": True}

    run_campaign(
        tmp_path / "repo",
        [fake_task("uno")],
        tmp_path / "campana.jsonl",
        measure=measure,
        conditions=["T0"],
        runs=3,
    )

    assert pedidas == [("T0", "uno", 0), ("T0", "uno", 1), ("T0", "uno", 2)]


def test_two_repos_running_at_once_do_not_fight_over_the_same_container(tmp_path: Path):
    """El nombre del contenedor se deriva del directorio del árbol, y el árbol se
    llamaba `T0-clean` en todos los repos. Con dos campañas en la misma máquina
    —lo normal en una VM con CPU de sobra— las dos pedirían `acp-T0-clean` y una
    mataría el contenedor de la otra a mitad de celda, que se lee como un agente
    que rompió algo.
    """
    uno = build(tmp_path / "repo-uno")
    otro = build(tmp_path / "repo-otro")

    arbol_uno = cell_tree(uno, None, [], tmp_path / "w1" / clean_tree_name(uno, "T0"))
    arbol_otro = cell_tree(otro, None, [], tmp_path / "w2" / clean_tree_name(otro, "T0"))

    assert arbol_uno.name != arbol_otro.name
    assert "repo-uno" in arbol_uno.name and "repo-otro" in arbol_otro.name


def test_a_knock_out_removes_one_practice_from_intact_code():
    """§6.2: el knock-out contesta «qué pierdo si dejo de hacer esto», así que
    quitar la práctica A1 es aplicar **solo** la degradación A1 sobre el código
    por lo demás intacto. La palabra engaña: quitar una práctica es añadir una
    degradación."""
    assert BREAKDOWN["KO-A1"] == ["A1"]
    assert BREAKDOWN["KO-B3"] == ["B3"]


def test_an_add_back_returns_one_practice_to_fully_degraded_code():
    """Y el add-back contesta «qué recupero si solo hago esto»: partir de T3 y
    devolver una sola práctica es T3 menos esa degradación."""
    assert BREAKDOWN["AB-A1"] == ["A2", "A3", "A4", "B1", "B2", "B3", "B4"]
    assert BREAKDOWN["AB-B4"] == ["A1", "A2", "A3", "A4", "B1", "B2", "B3"]


def test_the_breakdown_covers_the_eight_practices_in_both_directions():
    """Dieciséis celdas, y las dos direcciones sobre las mismas ocho. B5 no
    entra: el tamaño se mide como curva de dosis (§6.3), no como práctica que se
    quita o se devuelve."""
    assert len(BREAKDOWN) == 16
    practicas = {"A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"}
    assert {n.split("-", 1)[1] for n in BREAKDOWN} == practicas
    assert "B5" not in {t for ids in BREAKDOWN.values() for t in ids}


def test_each_add_back_is_exactly_t3_minus_one():
    """La comprobación que impide que un despiste deje una celda midiendo otra
    cosa: cada add-back tiene que ser T3 sin exactamente una."""
    for practica in ("A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"):
        assert set(BREAKDOWN[f"AB-{practica}"]) == set(CONDITIONS["T3"]) - {practica}


def test_the_breakdown_conditions_are_launchable_like_any_other():
    """Se corren con el mismo camino que T0-T3 o serían un script aparte que
    mide distinto sin que se note."""
    assert ALL_CONDITIONS["T0"] == CONDITIONS["T0"]
    assert ALL_CONDITIONS["KO-A2"] == BREAKDOWN["KO-A2"]
    # 4 del 2×2 + 16 del desglose + 3 de la curva de tamaño.
    assert len(ALL_CONDITIONS) == 23


def test_two_tiers_of_the_same_repo_do_not_fight_over_the_same_container(tmp_path: Path):
    """El diseño pide dos tiers en las celdas de titular (§6.1), y correrlos a la
    vez es lo que hace que quepan. Pero el árbol se llamaba igual en los dos
    —`<repo>-T0-clean`— así que pedirían el mismo contenedor y uno mataría el del
    otro. Misma clase de fallo que entre repositorios, un nivel más abajo."""
    bajo = clean_tree_name(tmp_path / "pint", "T0", label="mini")
    alto = clean_tree_name(tmp_path / "pint", "T0", label="full")

    assert bajo != alto
    assert "mini" in bajo and "full" in alto


def test_without_a_label_the_names_stay_as_the_cells_already_measured_have_them(tmp_path: Path):
    """Las celdas ya medidas se hicieron sin etiqueta. Cambiar el nombre por
    defecto obligaría a reconstruir árboles que ya existen, y en una reanudación
    eso es tiempo tirado."""
    assert clean_tree_name(tmp_path / "pint", "T0") == "pint-T0-clean"
    assert cell_tree_name(tmp_path / "pint", "T0", "t-001") == "pint-T0-t-001"


def test_the_suite_to_restore_is_the_one_b4_actually_moved(tmp_path: Path):
    """Medido sobre pint. La campaña buscaba la suite en `<repo>/tests`, que es
    donde la tiene python-stdnum; la de pint vive en `pint/testsuite`. Así que
    bajo B4 se la escondía al agente —correcto— y no se la devolvía al
    contenedor, y las celdas salían con «no tests collected»: la condición
    entera ilegible por una ruta supuesta.

    B4 ya sabe dónde la dejó: hermana del árbol, con sufijo `.acp-tests`.
    Preguntárselo funciona en cualquier repo; adivinar la ruta, en uno.
    """
    arbol = tmp_path / "work" / "pint-T2-clean"
    arbol.mkdir(parents=True)
    guardada = tmp_path / "work" / "pint-T2-clean.acp-tests"
    (guardada / "pint" / "testsuite").mkdir(parents=True)

    assert suite_to_restore(["B1", "B2", "B3", "B4"], arbol) == guardada
    assert suite_to_restore(["A1", "A2"], arbol) is None


def test_no_suite_to_restore_when_b4_moved_nothing(tmp_path: Path):
    """Si B4 no encontró suite que mover, no hay nada que devolver, y apuntar a
    un directorio inexistente es lo que producía el fallo silencioso."""
    arbol = tmp_path / "work" / "repo-T2-clean"
    arbol.mkdir(parents=True)

    assert suite_to_restore(["B4"], arbol) is None


def test_the_size_curve_is_launchable_as_conditions_like_any_other():
    """§6.3 pide tres puntos nuevos sobre B5 (~500, ~2.000 y ~10.000 líneas por
    fichero) con el original como cuarto. Es la única parte del diseño que busca
    un umbral en vez de una diferencia, y por eso necesita más de dos puntos.

    Van por el mismo camino que el 2×2 y el desglose: una curva que se corriera
    con un script aparte mediría distinto sin que se note.
    """
    assert ALL_CONDITIONS["C-500"] == ["B5-500"]
    assert ALL_CONDITIONS["C-2000"] == ["B5-2000"]
    assert ALL_CONDITIONS["C-10000"] == ["B5-10000"]
    # El cuarto punto es T0: el árbol sin tocar, que ya se mide en el 2×2.
    assert ALL_CONDITIONS["T0"] == []


def test_the_curve_does_not_leak_into_the_headline_grid():
    """El tamaño se mide como curva, no como celda del 2×2 ni como práctica que
    se quita o se devuelve: mezclarlo cambiaría lo que las tablas significan."""
    assert not any(c.startswith("C-") for c in CONDITIONS)
    assert not any(c.startswith("C-") for c in BREAKDOWN)
    assert not any("B5" in ids for ids in CONDITIONS.values())


def test_a_cell_whose_suite_will_not_run_is_recorded_and_the_campaign_goes_on(tmp_path: Path):
    """Medido sobre pint bajo T3: un fichero de test deja de colectar y la
    sesión de suite lanza. Eso mató el proceso entero con ocho celdas hechas y
    cuatro por hacer — y en el desglose, que son dieciséis condiciones por tier,
    se habría llevado un bloque completo.

    Que la suite no arranque es fontanería, igual que un fallo que no rompe
    nada: la celda no mide al agente, se apunta como no medible con su motivo, y
    la campaña sigue con la siguiente.
    """
    log = tmp_path / "campana.jsonl"
    medidas: list[str] = []

    def measure(condition, transform_ids, task, run=0):
        medidas.append(task.task_id)
        if task.task_id == "dos":
            raise RuntimeError("la corrida no dio ni un veredicto: 1 error during collection")
        return {"condition": condition, "task_id": task.task_id, "run": run,
                "measurable": True, "solved": True}

    run_campaign(
        tmp_path / "repo",
        [fake_task("uno"), fake_task("dos"), fake_task("tres")],
        log,
        measure=measure,
        conditions=["T0"],
    )

    assert medidas == ["uno", "dos", "tres"], "la campaña tiene que llegar a la tercera"
    escritas = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    caida = [r for r in escritas if r["task_id"] == "dos"][0]
    assert caida["measurable"] is False
    assert "veredicto" in caida["why"]


def test_the_broken_cell_is_not_counted_as_a_failed_agent(tmp_path: Path):
    """Y no entra en el denominador: la tasa se calcula sobre celdas medibles,
    así que una suite rota no puede parecer un agente peor."""
    log = tmp_path / "campana.jsonl"

    def measure(condition, transform_ids, task, run=0):
        raise RuntimeError("la corrida no dio ni un veredicto")

    run_campaign(tmp_path / "repo", [fake_task("uno")], log,
                 measure=measure, conditions=["T0"])

    resumen = summarise(log)
    assert resumen["T0"]["measurable"] == 0
    assert resumen["T0"]["unmeasurable"] == 1


def test_running_out_of_memory_stops_the_campaign_instead_of_being_logged(tmp_path: Path):
    """La tolerancia a celdas rotas no puede tragarse el fallo que sí obliga a
    parar. Una suite que no arranca es fontanería de esa celda; quedarse sin
    memoria es la máquina, y seguir intentando celdas gasta horas sin medir
    nada. Pasó dos veces con el portátil, y el checkpoint existe justo para que
    parar sea barato."""
    log = tmp_path / "campana.jsonl"

    def measure(condition, transform_ids, task, run=0):
        if task.task_id == "dos":
            raise MemoryError("la máquina se quedó sin memoria")
        return {"condition": condition, "task_id": task.task_id, "run": run,
                "measurable": True, "solved": True}

    with pytest.raises(MemoryError):
        run_campaign(
            tmp_path / "repo",
            [fake_task("uno"), fake_task("dos"), fake_task("tres")],
            log, measure=measure, conditions=["T0"],
        )

    escritas = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert [r["task_id"] for r in escritas] == ["uno"], "lo medido antes se conserva"
