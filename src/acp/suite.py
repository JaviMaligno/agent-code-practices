from __future__ import annotations

import re
import subprocess
from pathlib import Path

from acp.models import SuiteMetrics

COUNT_PATTERN = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped)")
DURATION_PATTERN = re.compile(r"in\s+([\d.]+)s")

DEFAULT_IMAGE = "python:3.12-slim"
INSTALL_AND_TEST = (
    "python -m pip install --quiet --upgrade pip && "
    "python -m pip install --quiet -e '.[test,dev]' || python -m pip install --quiet -e . ; "
    "python -m pip install --quiet pytest && python -m pytest -q"
)


def parse_pytest_summary(output: str) -> SuiteMetrics:
    counts = {"passed": 0, "failed": 0, "errors": 0}
    found = False
    for number, label in COUNT_PATTERN.findall(output):
        found = True
        key = "errors" if label.startswith("error") else label
        if key in counts:
            counts[key] += int(number)

    if not found:
        return SuiteMetrics()

    duration = DURATION_PATTERN.search(output)
    return SuiteMetrics(
        ran=True,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["errors"],
        seconds=float(duration.group(1)) if duration else 0.0,
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
