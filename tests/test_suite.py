import pytest

from acp.suite import parse_pytest_summary

GREEN = """\
============================= test session starts ==============================
collected 312 items

tests/test_a.py ....                                                     [ 10%]
tests/test_b.py ....                                                     [100%]

======================== 312 passed, 4 skipped in 21.44s =======================
"""

RED = """\
======================== 3 failed, 120 passed, 1 error in 8.10s ================
"""


def test_parses_green_summary():
    result = parse_pytest_summary(GREEN)
    assert result.ran is True
    assert result.passed == 312
    assert result.failed == 0
    assert result.errors == 0
    assert result.seconds == pytest.approx(21.44)


def test_parses_failures_and_errors():
    result = parse_pytest_summary(RED)
    assert result.passed == 120
    assert result.failed == 3
    assert result.errors == 1
    assert result.seconds == pytest.approx(8.10)


def test_unparseable_output_is_not_a_run():
    result = parse_pytest_summary("ImportError while loading conftest")
    assert result.ran is False
    assert result.passed == 0


def test_collection_errors_are_not_counted_twice():
    """pytest anuncia el error dos veces: en la interrupción y en el resumen."""
    output = (
        "!!!!!!!!!!!!!!! Interrupted: 3 errors during collection !!!!!!!!!!!!!!!\n"
        "=============================== 3 errors in 0.51s ==============================\n"
    )
    assert parse_pytest_summary(output).errors == 3


def test_numbers_inside_failure_output_do_not_reach_the_counts():
    """El mismo parser mide las ~54 corridas de agentes, donde el texto de un
    assert que diga '4 passed' inflaría la tasa de éxito."""
    output = (
        "E       AssertionError: expected 4 passed, got 0\n"
        "E       assert 7 failed\n"
        "======================== 1 failed, 2 passed in 1.00s ===========================\n"
    )
    result = parse_pytest_summary(output)
    assert result.passed == 2
    assert result.failed == 1


def test_duration_comes_from_the_summary_not_from_the_first_match():
    output = (
        "E       assert took 0.01s in 2.00s\n"
        "======================== 1 failed, 3 passed in 88.10s =========================\n"
    )
    assert parse_pytest_summary(output).seconds == pytest.approx(88.10)


def test_parses_the_quiet_summary_line_pytest_actually_prints():
    """INSTALL_AND_TEST usa `pytest -q`: ahí el resumen no lleva ningún '='."""
    output = (
        "....F                                                              [100%]\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_a.py::test_x - assert 1 == 2\n"
        "1 failed, 4 passed in 0.12s\n"
    )
    result = parse_pytest_summary(output)
    assert result.ran is True
    assert result.passed == 4
    assert result.failed == 1
    assert result.seconds == pytest.approx(0.12)


def test_long_runs_report_the_seconds_not_the_clock_format():
    output = "================== 200 passed in 129.53s (0:02:09) =====================\n"
    assert parse_pytest_summary(output).seconds == pytest.approx(129.53)


def test_skips_are_recorded():
    result = parse_pytest_summary("======= 5 passed, 900 skipped in 3.00s =======")
    assert result.passed == 5
    assert result.skipped == 900
