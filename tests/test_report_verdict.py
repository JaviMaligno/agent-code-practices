"""El veredicto tiene que distinguir tres cosas, no dos.

Un repo cuyo entorno no se pudo preparar no es un repo rechazado: es un repo
que no se pudo medir. Confundirlos hace que un fallo de fontanería —una cadena
de instalación que no cubre el proyecto, un timeout— se lea como un defecto del
candidato, y con la misma frase que un rechazo legítimo.
"""

from acp.models import (
    ReadabilityMetrics,
    RepoProfile,
    RuntimeTypingMetrics,
    SizeMetrics,
    SuiteMetrics,
)
from acp.report import admission_verdict, comparison_table, render_profile


def make_profile(name: str = "demo", **suite_kwargs) -> RepoProfile:
    suite = SuiteMetrics(
        ran=True, passed=300, failed=0, errors=0, seconds=44.0,
        install_ok=True, collect_ok=True, install_strategy="extra:test",
        install_seconds=61.0,
    )
    for key, value in suite_kwargs.items():
        setattr(suite, key, value)
    return RepoProfile(
        name=name,
        size=SizeMetrics(python_files=40, code_lines=8000, max_depth=3, mean_depth=1.8),
        readability=ReadabilityMetrics(
            comment_ratio=0.08, docstring_ratio=0.12, annotated_function_ratio=0.4,
            has_readme=True, has_docs_dir=True,
        ),
        suite=suite,
    )


def test_healthy_repo_is_admitted():
    verdict, reasons = admission_verdict(make_profile())
    assert verdict == "ADMITIDO"
    assert reasons == []


def test_environment_that_never_installed_is_not_a_rejection():
    profile = make_profile(ran=False, passed=0, install_ok=False, collect_ok=False,
                           install_error="install -e .: error: Microsoft Visual C++ 14.0 required")
    verdict, reasons = admission_verdict(profile)
    assert verdict == "NO EVALUABLE"
    assert any("entorno" in reason for reason in reasons)


def test_collection_that_never_worked_is_not_a_rejection():
    profile = make_profile(ran=False, passed=0, install_ok=True, collect_ok=False)
    verdict, _ = admission_verdict(profile)
    assert verdict == "NO EVALUABLE"


def test_timeout_is_not_a_rejection():
    profile = make_profile(ran=False, passed=0, install_ok=True, collect_ok=True, timed_out=True)
    verdict, reasons = admission_verdict(profile)
    assert verdict == "NO EVALUABLE"
    assert any("tiempo" in reason for reason in reasons)


def test_red_suite_in_a_healthy_environment_is_a_rejection():
    profile = make_profile(passed=10, failed=2)
    verdict, reasons = admission_verdict(profile)
    assert verdict == "RECHAZADO"
    assert any("rojo" in reason for reason in reasons)


def test_runtime_typing_is_a_rejection():
    profile = make_profile()
    profile.runtime_typing = RuntimeTypingMetrics(uses_runtime_typing=True, evidence=["a.py: import pydantic"])
    verdict, reasons = admission_verdict(profile)
    assert verdict == "RECHAZADO"
    assert any("tipado" in reason for reason in reasons)


def test_table_carries_the_columns_the_plan_says_to_read_it_by():
    """El plan manda descartar leyendo la tabla, no ficha por ficha."""
    header = comparison_table([make_profile("a")]).splitlines()[0]
    assert "veredicto" in header
    assert "tipado" in header


def test_profile_reports_the_install_strategy_that_worked():
    text = render_profile(make_profile())
    assert "extra:test" in text
