import pytest

from acp.tasks.models import Task

RAW = {
    "task_id": "python-stdnum-001",
    "repo": "python-stdnum",
    "module": "stdnum.mx.curp",
    "symbol": "get_gender",
    "stratum": "domain",
    "patch": "--- a\n+++ b\n",
    "fail_to_pass": ["stdnum/mx/curp.py::stdnum.mx.curp.get_gender"],
    "pass_to_pass": ["tests/test_mx_curp.doctest"],
    "min_files_to_judge": 2,
}


def test_a_task_survives_a_round_trip():
    assert Task.from_json(RAW).to_json() == RAW


def test_the_stratum_is_one_of_the_two_the_design_declares():
    """§3.3.1 parte las tareas en genéricas y de dominio, y el corte es lo que
    más probablemente cambie la lectura de la tabla principal. Un tercer valor
    por error tipográfico rompería ese corte sin avisar."""
    with pytest.raises(ValueError):
        Task.from_json({**RAW, "stratum": "generico"})


def test_a_task_without_tests_to_break_is_not_a_task():
    """Sin tests que distingan arreglado de roto no hay medida (§3.2.1)."""
    with pytest.raises(ValueError):
        Task.from_json({**RAW, "fail_to_pass": []})


def test_a_domain_task_must_say_how_many_files_it_takes_to_judge():
    """Es el puente entre el estrato y la métrica de localización (§3.3.1): un
    fallo de dominio que se juzga en una sola línea no sirve, aunque nadie lo
    detecte leyendo la función."""
    with pytest.raises(ValueError):
        Task.from_json({**RAW, "min_files_to_judge": 1})
