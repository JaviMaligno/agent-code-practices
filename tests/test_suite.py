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
