from acp.equivalence import compare
from acp.models import SuiteMetrics


def suite(**overrides) -> SuiteMetrics:
    defaults = dict(
        ran=True, passed=413, failed=0, errors=0, skipped=9,
        attempted=True, install_ok=True, collect_ok=True, tree_under_test=True,
    )
    return SuiteMetrics(**{**defaults, **overrides})


def test_the_same_result_is_equivalent():
    assert compare(suite(), suite()).equivalent is True


def test_one_test_less_is_not_equivalent():
    """Que el total baje suele significar que la transformación se llevó por
    delante un módulo entero, no que el repo cambiara de opinión."""
    report = compare(suite(), suite(passed=412))

    assert report.equivalent is False
    assert any("passed" in difference for difference in report.differences)


def test_a_suite_that_did_not_run_afterwards_is_not_equivalent():
    report = compare(suite(), suite(ran=False, passed=0))

    assert report.equivalent is False


def test_duration_does_not_count_as_a_difference():
    """El tiempo varía entre corridas de la misma máquina: exigirlo igual haría
    fallar la verificación por ruido."""
    assert compare(suite(seconds=20.0), suite(seconds=31.5)).equivalent is True
