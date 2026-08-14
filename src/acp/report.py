from __future__ import annotations

from acp.models import RepoProfile

COLUMNS = [
    ("repo", lambda p: p.name),
    ("ficheros", lambda p: str(p.size.python_files)),
    ("líneas", lambda p: str(p.size.code_lines)),
    ("prof. máx", lambda p: str(p.size.max_depth)),
    ("anotadas", lambda p: f"{p.readability.annotated_function_ratio:.0%}"),
    ("docs", lambda p: "sí" if p.readability.has_docs_dir else "no"),
    ("fan-out", lambda p: f"{p.coupling.mean_fan_out:.2f}"),
    ("dominio", lambda p: f"{p.domain.domain_density:.0%}"),
    ("suite", lambda p: f"{p.suite.passed}p/{p.suite.failed}f {p.suite.seconds:.0f}s"),
]


def admission_verdict(profile: RepoProfile) -> tuple[str, list[str]]:
    """Aplica los criterios de admisión del spec §3.2.1."""
    reasons: list[str] = []
    if not profile.suite.ran:
        reasons.append("la suite no llegó a ejecutarse")
    elif profile.suite.failed or profile.suite.errors:
        reasons.append(f"suite en rojo: {profile.suite.failed} fallos, {profile.suite.errors} errores")
    if profile.runtime_typing.uses_runtime_typing:
        reasons.append("usa tipado en ejecución, A1 no sería equivalente")
    if profile.size.code_lines < 2000:
        reasons.append("demasiado pequeño: el agente lo lee entero")
    if profile.size.code_lines > 80000:
        reasons.append("demasiado grande para el presupuesto")
    return ("RECHAZADO" if reasons else "ADMITIDO"), reasons


def render_profile(profile: RepoProfile) -> str:
    verdict, reasons = admission_verdict(profile)
    lines = [
        f"# {profile.name}",
        "",
        f"**Veredicto de admisión:** {verdict}",
        "",
    ]
    if reasons:
        lines += [f"- {reason}" for reason in reasons] + [""]

    lines += [
        "## Tamaño",
        f"- Ficheros Python: {profile.size.python_files}",
        f"- Líneas de código: {profile.size.code_lines}",
        f"- Profundidad de jerarquía: máx {profile.size.max_depth}, media {profile.size.mean_depth:.2f}",
        "",
        "## Margen de degradación",
        f"- Ratio de comentarios: {profile.readability.comment_ratio:.1%}",
        f"- Ratio de docstrings: {profile.readability.docstring_ratio:.1%}",
        f"- Funciones anotadas: {profile.readability.annotated_function_ratio:.1%}",
        f"- README: {'sí' if profile.readability.has_readme else 'no'}",
        f"- Directorio docs/: {'sí' if profile.readability.has_docs_dir else 'no'}",
        "",
        "## Acoplamiento",
        f"- Módulos internos: {profile.coupling.internal_modules}",
        f"- Aristas internas: {profile.coupling.internal_edges}",
        f"- Fan-out medio: {profile.coupling.mean_fan_out:.2f}",
        f"- Fan-in máximo: {profile.coupling.max_fan_in}",
        "",
        "## Densidad de lógica de dominio (proxy)",
        f"- Funciones complejas: {profile.domain.complex_functions}",
        f"- Candidatas a dominio: {profile.domain.domain_candidate_functions}",
        f"- Densidad: {profile.domain.domain_density:.1%}",
        "",
        "### Muestra para inspección manual",
    ]
    lines += [f"- `{sample}`" for sample in profile.domain.samples] or ["- (ninguna)"]
    lines += [
        "",
        "## Suite",
        f"- Ejecutada: {'sí' if profile.suite.ran else 'no'}",
        f"- Pasan: {profile.suite.passed}, fallan: {profile.suite.failed}, errores: {profile.suite.errors}",
        f"- Duración: {profile.suite.seconds} s",
        "",
        "## Tipado en ejecución",
        f"- Detectado: {'sí' if profile.runtime_typing.uses_runtime_typing else 'no'}",
    ]
    lines += [f"  - {item}" for item in profile.runtime_typing.evidence]
    return "\n".join(lines) + "\n"


def comparison_table(profiles: list[RepoProfile]) -> str:
    header = "| " + " | ".join(name for name, _ in COLUMNS) + " |"
    separator = "|" + "|".join("---" for _ in COLUMNS) + "|"
    rows = [
        "| " + " | ".join(getter(profile) for _, getter in COLUMNS) + " |"
        for profile in profiles
    ]
    return "\n".join([header, separator, *rows]) + "\n"
