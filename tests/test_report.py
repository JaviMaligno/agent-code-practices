from acp.models import ReadabilityMetrics, RepoProfile, SizeMetrics, SuiteMetrics
from acp.report import admission_verdict, comparison_table, render_profile


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
        suite=SuiteMetrics(ran=True, passed=300, failed=0, errors=0, seconds=44.0),
    )


def test_profile_renders_name_and_key_numbers():
    text = render_profile(make_profile())
    assert "# demo" in text
    assert "8000" in text
    assert "44.0" in text


def test_comparison_table_has_a_row_per_repo():
    text = comparison_table([make_profile("a"), make_profile("b")])
    lines = [line for line in text.splitlines() if line.startswith("| ")]
    assert len(lines) == 3  # cabecera y dos filas; el separador empieza por "|---"


def test_admission_verdict_rejects_red_suite():
    profile = make_profile()
    profile.suite = SuiteMetrics(ran=True, passed=10, failed=2, errors=0, seconds=5.0)
    assert "RECHAZADO" in render_profile(profile)


def test_admission_verdict_rejects_a_suite_that_ran_no_test():
    """Todo skipped es verde para el parser y no mide nada: la variable
    dependiente del experimento saldría 'sin regresión' por construcción."""
    profile = make_profile()
    profile.suite = SuiteMetrics(ran=True, passed=0, failed=0, errors=0, skipped=5, seconds=0.12)

    verdict, reasons = admission_verdict(profile)

    assert verdict == "RECHAZADO"
    assert any("ningún test" in reason for reason in reasons)


def test_admission_verdict_accepts_a_suite_with_skips_but_real_passes():
    profile = make_profile()
    profile.suite = SuiteMetrics(ran=True, passed=300, failed=0, errors=0, skipped=12, seconds=44.0)

    assert admission_verdict(profile)[0] == "ADMITIDO"
