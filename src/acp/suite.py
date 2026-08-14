from __future__ import annotations

import re
import subprocess
from pathlib import Path

from acp.models import SuiteMetrics

COUNT_PATTERN = re.compile(r"(\d+)\s+(passed|failed|errors|error|skipped)\b")

# Línea de resumen de pytest: la última que termina en `in <n>s`, con o sin el
# marco de `=` (con `-q` no lo lleva) y con o sin el reloj de las corridas
# largas. Anclar aquí es lo que impide que los números de una traza de fallo
# entren en el recuento y que la duración se lea de la primera coincidencia.
SUMMARY_LINE_PATTERN = re.compile(r"\bin\s+(\d+(?:\.\d+)?)s(?:\s*\([^)]*\))?\s*=*\s*$")

DEFAULT_IMAGE = "python:3.12-slim"
INSTALL_AND_TEST = (
    "python -m pip install --quiet --upgrade pip && "
    "python -m pip install --quiet -e '.[test,dev]' || python -m pip install --quiet -e . ; "
    "python -m pip install --quiet pytest && python -m pytest -q"
)


def _summary_line(output: str) -> tuple[str, float] | None:
    """Última línea de resumen de pytest y su duración, o None si no la hay."""
    for line in reversed(output.splitlines()):
        match = SUMMARY_LINE_PATTERN.search(line.rstrip())
        if match:
            return line, float(match.group(1))
    return None


def parse_pytest_summary(output: str) -> SuiteMetrics:
    summary = _summary_line(output)
    if summary is None:
        return SuiteMetrics()

    line, seconds = summary
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for number, label in COUNT_PATTERN.findall(line):
        key = "errors" if label.startswith("error") else label
        counts[key] += int(number)

    return SuiteMetrics(
        ran=True,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["errors"],
        skipped=counts["skipped"],
        seconds=seconds,
    )


def run_suite_in_docker(repo: Path, image: str = DEFAULT_IMAGE, timeout: int = 3600) -> SuiteMetrics:
    """Instala el repo y corre su suite dentro de un contenedor desechable."""
    command = [
        "docker", "run", "--rm",
        "-v", f"{repo.resolve()}:/repo",
        "-w", "/repo",
        image, "bash", "-lc", INSTALL_AND_TEST,
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return SuiteMetrics()
    return parse_pytest_summary(completed.stdout + completed.stderr)
