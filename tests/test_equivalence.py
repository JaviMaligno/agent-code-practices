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


def test_two_suites_that_never_ran_are_not_a_green_check():
    """Si Docker se cae o la instalación falla, las dos corridas dan cero y son
    idénticas. Leerlo como equivalente es el peor fallo posible aquí: la
    verificación existe para atrapar un repo roto y estaría dando vía libre a un
    bloque entero corrido sobre uno."""
    dead = suite(ran=False, passed=0, skipped=0, install_ok=False)

    report = compare(dead, dead)

    assert report.equivalent is False
    assert any("no corrió" in difference for difference in report.differences)


def test_a_reference_that_executed_no_test_proves_nothing():
    """Colectar cero también deja la comparación sin nada que comparar: la suite
    arrancó, pero no observó ni un resultado que la transformación pudiera
    preservar."""
    empty = suite(passed=0, skipped=0)

    report = compare(empty, empty)

    assert report.equivalent is False
