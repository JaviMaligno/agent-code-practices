"""La parte pura de la validación: leer el resultado POR TEST y compararlo.

§3.3 pide que una tarea rompa **un conjunto concreto de tests y no otros**. Eso
no se puede responder con el resumen de pytest, que solo da totales: dos
corridas con `1 failed` pueden ser dos fallos distintos. Hace falta el resultado
por test, y compararlo antes/después.
"""

from __future__ import annotations

import inspect
import subprocess
import sys

import pytest

from acp.tasks.models import Task
from acp.tasks.validate import (
    PER_TEST_ARGS,
    SuiteSession,
    compare_runs,
    parse_verbose_outcomes,
    validate_task,
)


def test_a_task_is_valid_when_it_breaks_exactly_what_it_should():
    report = compare_runs(
        before={"t_a": "passed", "t_b": "passed", "t_c": "passed"},
        after={"t_a": "failed", "t_b": "passed", "t_c": "passed"},
        fail_to_pass=["t_a"],
    )

    assert report.valid is True


def test_a_task_that_breaks_more_than_it_declares_is_not_valid():
    """Una tarea que tumba media suite no mide si el agente arregló el fallo:
    mide si sobrevivió al desastre."""
    report = compare_runs(
        before={"t_a": "passed", "t_b": "passed"},
        after={"t_a": "failed", "t_b": "failed"},
        fail_to_pass=["t_a"],
    )

    assert report.valid is False
    assert report.unexpected_failures == ["t_b"]


def test_a_task_that_breaks_nothing_is_not_a_task():
    report = compare_runs(
        before={"t_a": "passed"},
        after={"t_a": "passed"},
        fail_to_pass=["t_a"],
    )

    assert report.valid is False


def test_the_tests_that_already_failed_are_not_held_against_the_task():
    """Un test roto en el repo original no lo rompió la tarea, y exigir que
    pase dejaría fuera tareas buenas por un defecto ajeno."""
    report = compare_runs(
        before={"t_a": "passed", "t_flaky": "failed"},
        after={"t_a": "failed", "t_flaky": "failed"},
        fail_to_pass=["t_a"],
    )

    assert report.valid is True


def test_a_test_that_vanished_after_the_patch_did_not_pass():
    """Si el parche impide colectar un test, ese test dejó de demostrar nada.
    Contarlo como 'sigue verde' porque no aparece en rojo es exactamente el modo
    de fallo que §3.3 quiere evitar: una tarea que rompe la suite sin decirlo."""
    report = compare_runs(
        before={"t_a": "passed", "t_b": "passed"},
        after={"t_a": "failed"},
        fail_to_pass=["t_a"],
    )

    assert report.valid is False
    assert report.unexpected_failures == ["t_b"]


def test_an_error_counts_as_broken_just_like_a_failure():
    """pytest distingue el fallo de la aserción del error al preparar el test.
    Para la tarea son lo mismo: el test dejó de demostrar que el código está
    bien."""
    report = compare_runs(
        before={"t_a": "passed"},
        after={"t_a": "error"},
        fail_to_pass=["t_a"],
    )

    assert report.valid is True


def test_the_report_says_which_tests_actually_broke():
    """Lo que el generador de la fase 5 necesita cuando su declaración se queda
    corta: los tests que de verdad se rompieron, para volver a declarar la tarea
    sin pagar otras dos corridas de suite."""
    report = compare_runs(
        before={"t_a": "passed", "t_b": "passed", "t_c": "passed"},
        after={"t_a": "failed", "t_b": "failed", "t_c": "passed"},
        fail_to_pass=["t_a"],
    )

    assert report.observed_failures == ["t_a", "t_b"]


VERBOSE = """\
============================= test session starts ==============================
collecting ... collected 4 items

stdnum/mx/curp.py::stdnum.mx.curp PASSED                                 [ 25%]
stdnum/mx/rfc.py::stdnum.mx.rfc FAILED                                   [ 50%]
tests/test_iban.doctest::test_iban.doctest SKIPPED (sin red)             [ 75%]
tests/test_x.py::test_con espacios[un valor] PASSED                      [100%]

=========================== short test summary info ============================
FAILED stdnum/mx/rfc.py::stdnum.mx.rfc
========================= 1 failed, 2 passed, 1 skipped ========================
"""


def test_the_outcome_of_every_test_is_read_from_the_verbose_run():
    assert parse_verbose_outcomes(VERBOSE) == {
        "stdnum/mx/curp.py::stdnum.mx.curp": "passed",
        "stdnum/mx/rfc.py::stdnum.mx.rfc": "failed",
        "tests/test_iban.doctest::test_iban.doctest": "skipped",
        "tests/test_x.py::test_con espacios[un valor]": "passed",
    }


def test_the_summary_lines_are_not_mistaken_for_results():
    """`short test summary info` repite cada fallo con el veredicto DELANTE. Si
    esas líneas entraran, un nodeid quedaría partido y el conjunto medido no
    sería el que corrió."""
    outcomes = parse_verbose_outcomes(VERBOSE)

    assert all(not key.startswith("FAILED") for key in outcomes)
    assert len(outcomes) == 4


def test_the_progress_percentage_is_optional():
    """Sin terminal pytest no siempre cierra la línea con el porcentaje, y con
    xdist lo pone DELANTE. Colgar la lectura del porcentaje ataría el circuito
    de medida a un detalle de presentación."""
    assert parse_verbose_outcomes("a.py::t PASSED\n") == {"a.py::t": "passed"}


def test_xdist_puts_the_verdict_first_and_the_nodeid_last():
    """Alguno de los finalistas lleva `-n auto` en sus addopts, y ahí la línea
    es `[gw0] [ 50%] PASSED nodeid`. Leerla mal no daría un error: daría un
    conjunto vacío, que se leería como 'la tarea no rompe nada'."""
    salida = "[gw0] [ 50%] FAILED stdnum/mx/rfc.py::stdnum.mx.rfc \n"

    assert parse_verbose_outcomes(salida) == {"stdnum/mx/rfc.py::stdnum.mx.rfc": "failed"}


def test_a_line_that_is_not_a_result_is_not_read_as_one():
    ruido = "platform darwin -- Python 3.12.8\nrootdir: /repo\nplugins: cov-7.1.0\n"

    assert parse_verbose_outcomes(ruido) == {}


def test_a_repo_that_asks_for_quiet_cannot_silence_the_per_test_result(tmp_path):
    """La verbosidad de pytest es un CONTADOR: `-q` en los addopts del repo y
    `-v` en la línea de órdenes se cancelan y la corrida vuelve a imprimir
    puntos. Medido: con `addopts = -q`, `-v` da `test_x.py .` y ni un nodeid.

    No daría un error: daría un diccionario vacío, y la tarea se leería como
    "no rompe nada" en un repo elegido por tener sus addopts así. Por eso el
    lector pide `--verbosity=1`, que FIJA el valor en vez de sumarlo.
    """
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")
    (tmp_path / "test_x.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")

    corrida = subprocess.run(
        [sys.executable, "-m", "pytest", *PER_TEST_ARGS],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )

    assert parse_verbose_outcomes(corrida.stdout) == {"test_x.py::test_a": "passed"}


def test_validating_against_a_session_of_another_tree_is_loud(tmp_path):
    """La sesión es un contenedor con UN árbol dentro. Validar contra ella una
    tarea de otro clon mediría el árbol de la sesión y llamaría a eso el
    resultado de la otra tarea: un veredicto sobre un fichero que nadie parcheó,
    indistinguible de una tarea que no rompe nada."""
    tarea = Task(
        task_id="t", repo="a", module="pkg.core", symbol="f", stratum="generic",
        patch="", fail_to_pass=["x"],
    )
    sesion = SuiteSession(tmp_path / "a")

    with pytest.raises(ValueError, match="otro árbol"):
        validate_task(tmp_path / "b", tarea, session=sesion)


def test_the_declared_pass_to_pass_is_actually_checked():
    """El campo se guardaba, se serializaba y no se leía nunca. El resolve rate
    (§7) exige que estos tests SIGAN pasando tras la edición del agente: si la
    lista no se verifica al fabricar la tarea, puede nombrar tests que ya
    estaban rotos o que no existen, y eso no se descubre hasta la campaña."""
    report = compare_runs(
        before={"t_a": "passed", "t_b": "failed"},
        after={"t_a": "failed", "t_b": "failed"},
        fail_to_pass=["t_a"],
        pass_to_pass=["t_b"],
    )

    assert report.pass_to_pass_ok is False
    assert report.valid is False


def test_a_pass_to_pass_that_does_not_exist_invalidates_the_task():
    report = compare_runs(
        before={"t_a": "passed"},
        after={"t_a": "failed"},
        fail_to_pass=["t_a"],
        pass_to_pass=["t_inventado"],
    )

    assert report.valid is False


def test_a_task_that_breaks_far_more_than_one_thing_is_rejected_even_if_declared():
    """§3.3 pide que la tarea rompa un conjunto CONCRETO de tests. Medido en
    Docker: la misma mutación es inválida declarando 1 test y válida declarando
    los 22 que rompe. Una tarea que tumba media suite no mide si el agente
    arregló el fallo, mide si sobrevivió al desastre."""
    rotos = {f"t_{index}": "passed" for index in range(30)}
    despues = {nodeid: "failed" for nodeid in rotos}

    report = compare_runs(
        before=rotos, after=despues, fail_to_pass=list(rotos), pass_to_pass=[]
    )

    assert report.valid is False


def test_a_handful_of_broken_tests_is_still_a_task():
    """El techo no puede ser tan bajo que deje fuera una función que varios
    tests ejercitan a la vez, que es lo normal en un repo con doctests."""
    before = {f"t_{index}": "passed" for index in range(10)}
    after = {**before, "t_0": "failed", "t_1": "failed", "t_2": "failed"}

    report = compare_runs(
        before=before, after=after, fail_to_pass=["t_0", "t_1", "t_2"], pass_to_pass=["t_5"]
    )

    assert report.valid is True


def test_validating_a_task_checks_the_list_it_declared(monkeypatch):
    """De nada sirve que `compare_runs` sepa comprobar `pass_to_pass` si quien
    valida una tarea real no se lo pasa: el campo seguiría siendo decorativo."""
    from acp.tasks import validate as modulo

    recibido: dict = {}

    def espia(before, after, fail_to_pass, pass_to_pass=None):
        recibido["pass_to_pass"] = pass_to_pass
        return modulo.ValidationReport(
            valid=True, fail_to_pass_ok=True, pass_to_pass_ok=True, unexpected_failures=[]
        )

    monkeypatch.setattr(modulo, "compare_runs", espia)

    firma = inspect.signature(modulo._validate_in)
    assert "task" in firma.parameters, "cambió la firma: revisa este test"

    fuente = inspect.getsource(modulo._validate_in)
    assert "task.pass_to_pass" in fuente, (
        "el validador no le pasa a compare_runs la lista declarada en la tarea"
    )
