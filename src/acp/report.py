from __future__ import annotations

from acp.models import RepoProfile


def run_cost_seconds(profile: RepoProfile) -> float:
    """Lo que cuesta una corrida entera: preparar el entorno y pasar la suite.

    El spec §3.2 mide las dos porque las dos se multiplican por 54. Reportar
    solo la suite deja fuera la mitad del criterio — en python-stdnum, 69 s de
    preparación frente a 20 s de suite.
    """
    return profile.suite.install_seconds + profile.suite.seconds


def admission_verdict(profile: RepoProfile) -> tuple[str, list[str]]:
    """Aplica los criterios de admisión del spec §3.2.1.

    Tres estados, no dos. NO EVALUABLE es para lo que no se pudo medir —el
    entorno no se dejó preparar, la suite no colectó, se agotó el tiempo— y
    RECHAZADO para lo que se midió y no cumple. Mezclarlos haría que un fallo
    de la cadena de instalación se lea como un defecto del candidato.
    """
    blockers: list[str] = []
    suite = profile.suite

    if not suite.attempted:
        blockers.append("la suite no se ejecutó en este perfilado")
    elif not suite.install_ok:
        detail = f": {suite.install_error}" if suite.install_error else ""
        blockers.append(f"no se pudo preparar el entorno{detail}")
    elif not suite.collect_ok:
        blockers.append("el entorno se instaló pero la suite no llegó a colectarse")
    elif not suite.tree_under_test:
        blockers.append(
            "la suite no se ejecutó contra el árbol del repo: una dependencia lo sustituyó "
            "por su versión publicada"
        )
    elif suite.timed_out:
        blockers.append("se agotó el tiempo antes de terminar la suite")
    elif not suite.ran:
        blockers.append("la suite no llegó a ejecutarse")

    if blockers:
        return "NO EVALUABLE", blockers

    reasons: list[str] = []
    if suite.failed or suite.errors:
        reasons.append(f"suite en rojo: {suite.failed} fallos, {suite.errors} errores")
    elif not suite.passed:
        # Verde sin haber ejecutado ningún test: la variable dependiente del
        # experimento saldría "sin regresión" por construcción.
        reasons.append(f"la suite no ejecutó ningún test: 0 pasan, {suite.skipped} saltados")
    if profile.runtime_typing.uses_runtime_typing:
        reasons.append("usa tipado en ejecución, A1 no sería equivalente")
    if profile.size.code_lines < 2000:
        reasons.append("demasiado pequeño: el agente lo lee entero")
    if profile.size.code_lines > 80000:
        reasons.append("demasiado grande para el presupuesto")
    return ("RECHAZADO" if reasons else "ADMITIDO"), reasons


# El plan manda descartar candidatos leyendo esta tabla, así que tiene que
# llevar todo lo que decide un descarte: sin la columna de veredicto y la de
# tipado en ejecución habría que abrir ficha por ficha.
COLUMNS = [
    ("repo", lambda p: p.name),
    ("veredicto", lambda p: admission_verdict(p)[0]),
    ("ficheros", lambda p: str(p.size.python_files)),
    ("líneas", lambda p: str(p.size.code_lines)),
    ("prof. máx", lambda p: str(p.size.max_depth)),
    ("anotadas", lambda p: f"{p.readability.annotated_function_ratio:.0%}"),
    ("docs", lambda p: "sí" if p.readability.has_docs_dir else "no"),
    ("tipado runtime", lambda p: "sí" if p.runtime_typing.uses_runtime_typing else "no"),
    ("fan-out", lambda p: f"{p.coupling.mean_fan_out:.2f}"),
    ("dominio", lambda p: f"{p.domain.domain_density:.0%}"),
    ("suite", lambda p: f"{p.suite.passed}p/{p.suite.failed}f {p.suite.seconds:.0f}s"),
    ("entorno", lambda p: f"{p.suite.install_seconds:.0f}s"),
    ("coste", lambda p: f"{run_cost_seconds(p):.0f}s"),
]


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
        f"- No parseables: {profile.size.unparseable_files} "
        "(cuentan líneas pero no aparecen en el resto de métricas)",
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
        "## Entorno",
        f"- Instalación: {'sí' if profile.suite.install_ok else 'no'}",
        f"- Estrategia que funcionó: {profile.suite.install_strategy or '(ninguna)'}",
        f"- Coste de preparación: {profile.suite.install_seconds:.0f} s",
        f"- Colecta la suite: {'sí' if profile.suite.collect_ok else 'no'}",
    ]
    if profile.suite.install_error:
        lines += [f"- Error: `{profile.suite.install_error}`"]
    lines += [
        "",
        "## Suite",
        f"- Ejecutada: {'sí' if profile.suite.ran else 'no'}",
        f"- Pasan: {profile.suite.passed}, fallan: {profile.suite.failed}, "
        f"errores: {profile.suite.errors}, saltados: {profile.suite.skipped}",
        f"- Duración: {profile.suite.seconds} s",
        f"- Tiempo agotado: {'sí' if profile.suite.timed_out else 'no'}",
        f"- Coste por corrida: {run_cost_seconds(profile):.0f} s "
        f"({profile.suite.install_seconds:.0f} s de entorno + {profile.suite.seconds:.0f} s de suite)",
        "",
        "## Tipado en ejecución",
        f"- Detectado: {'sí' if profile.runtime_typing.uses_runtime_typing else 'no'}",
        f"- Alcance: {profile.runtime_typing.affected_files} de "
        f"{profile.runtime_typing.total_files} ficheros",
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
