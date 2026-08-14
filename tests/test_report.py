from acp.models import (
    ReadabilityMetrics,
    RepoProfile,
    RuntimeTypingMetrics,
    SizeMetrics,
    SuiteMetrics,
)
from acp.report import admission_verdict, comparison_table, render_profile


def healthy_suite(**overrides) -> SuiteMetrics:
    """Una suite que se ejecutó de verdad: entorno preparado y suite colectada.

    Sin esos dos campos el veredicto es NO EVALUABLE, que es lo correcto: una
    suite no puede haber corrido si su entorno nunca llegó a instalarse.
    """
    defaults = dict(
        ran=True, passed=300, failed=0, errors=0, seconds=44.0,
        attempted=True, install_ok=True, collect_ok=True, install_strategy="extra:test",
        tree_under_test=True,
    )
    return SuiteMetrics(**{**defaults, **overrides})


def make_profile(name: str = "demo") -> RepoProfile:
    return RepoProfile(
        name=name,
        size=SizeMetrics(python_files=40, code_lines=8000, max_depth=3, mean_depth=1.8),
        readability=ReadabilityMetrics(
            comment_ratio=0.08,
            docstring_ratio=0.12,
            annotated_function_ratio=0.4,
            has_readme=True,
            has_docs_dir=True,
        ),
        suite=healthy_suite(),
    )


def test_profile_renders_name_and_key_numbers():
    text = render_profile(make_profile())
    assert "# demo" in text
    assert "8000" in text
    assert "44.0" in text


def test_profile_reports_unparseable_files():
    profile = make_profile()
    profile.size.unparseable_files = 7

    text = render_profile(profile)

    assert "No parseables: 7" in text


def test_profile_reports_the_whole_cost_of_a_run():
    """El spec §3.2 mide el coste como preparación más suite, porque las dos se
    multiplican por 54: publicar solo la suite esconde la mitad."""
    profile = make_profile()
    profile.suite.install_seconds = 69.0

    text = render_profile(profile)

    assert "Coste por corrida: 113 s" in text


def test_comparison_table_carries_the_total_cost():
    profile = make_profile()
    profile.suite.install_seconds = 69.0

    text = comparison_table([profile])

    assert "coste" in text.splitlines()[0]
    assert "113s" in text


def test_a_repo_replaced_by_its_published_version_is_not_evaluable():
    """Una dependencia de test puede desinstalar el repo y dejar en su lugar la
    versión de PyPI: la suite saldría verde midiendo otro código, y en la
    campaña las transformaciones no tendrían efecto sobre lo que se prueba."""
    profile = make_profile()
    profile.suite = healthy_suite(tree_under_test=False)

    verdict, reasons = admission_verdict(profile)

    assert verdict == "NO EVALUABLE"
    assert any("árbol" in reason for reason in reasons)


def test_a_failed_preparation_step_is_not_a_rejection():
    """holidays genera sus traducciones en el build. Si ese paso falla, lo que
    no se pudo es medir el repo — no es un defecto suyo."""
    profile = make_profile()
    profile.suite = healthy_suite(
        prepare_command="python scripts/l10n/generate_mo_files.py", prepare_ok=False
    )

    verdict, reasons = admission_verdict(profile)

    assert verdict == "NO EVALUABLE"
    assert any("preparación" in reason for reason in reasons)


def test_the_preparation_step_appears_in_the_profile():
    """Cambia lo que hay que hacer para reproducir la corrida, así que no puede
    quedarse fuera de la ficha."""
    profile = make_profile()
    profile.suite = healthy_suite(
        prepare_command="python scripts/l10n/generate_mo_files.py", prepare_ok=True
    )

    assert "generate_mo_files.py" in render_profile(profile)


def test_a_repo_that_tests_its_own_tree_passes_admission():
    profile = make_profile()
    profile.suite = healthy_suite(tree_under_test=True)

    assert admission_verdict(profile)[0] == "ADMITIDO"


def test_profile_reports_the_reach_of_runtime_typing():
    profile = make_profile()
    profile.runtime_typing = RuntimeTypingMetrics(
        uses_runtime_typing=True, evidence=["pkg/a.py: @singledispatch"],
        affected_files=1, total_files=360,
    )

    text = render_profile(profile)

    assert "1 de 360 ficheros" in text


def test_comparison_table_has_a_row_per_repo():
    text = comparison_table([make_profile("a"), make_profile("b")])
    lines = [line for line in text.splitlines() if line.startswith("| ")]
    assert len(lines) == 3  # cabecera y dos filas; el separador empieza por "|---"


def test_admission_verdict_rejects_red_suite():
    profile = make_profile()
    profile.suite = healthy_suite(passed=10, failed=2, seconds=5.0)
    assert "RECHAZADO" in render_profile(profile)


def test_admission_verdict_rejects_a_suite_that_ran_no_test():
    """Todo skipped es verde para el parser y no mide nada: la variable
    dependiente del experimento saldría 'sin regresión' por construcción."""
    profile = make_profile()
    profile.suite = healthy_suite(passed=0, skipped=5, seconds=0.12)

    verdict, reasons = admission_verdict(profile)

    assert verdict == "RECHAZADO"
    assert any("ningún test" in reason for reason in reasons)


def test_admission_verdict_accepts_a_suite_with_skips_but_real_passes():
    profile = make_profile()
    profile.suite = healthy_suite(skipped=12)

    assert admission_verdict(profile)[0] == "ADMITIDO"
