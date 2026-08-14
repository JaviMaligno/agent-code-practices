"""Ejecución de la suite de un repositorio candidato.

Sin contenedores: la máquina de ejecución no admite Docker, así que el
aislamiento es un entorno virtual desechable por repositorio. Aísla las
dependencias, no el sistema — que es la razón por la que solo entran
repositorios públicos y conocidos.

Dos decisiones gobiernan este módulo:

1. Solo se intenta instalar lo que el repo **declara**. `pip install -e '.[test]'`
   sobre un repo que no declara ese extra imprime un aviso y sale con código 0,
   así que una cadena de fallbacks encadenada con `||` nunca dispara y la suite
   acaba corriendo sin sus dependencias.
2. El éxito de la preparación se comprueba **funcionalmente**, colectando los
   tests, no leyendo códigos de salida.
"""

from __future__ import annotations

import configparser
import json
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from acp.models import SuiteMetrics
from acp.runners import DEFAULT_IMAGE, DockerRunner, VenvRunner

COUNT_PATTERN = re.compile(r"(\d+)\s+(passed|failed|errors|error|skipped)\b")

# Línea de resumen de pytest: la última que termina en `in <n>s`, con o sin el
# marco de `=` (con `-q` no lo lleva) y con o sin el reloj de las corridas
# largas. Anclar aquí es lo que impide que los números de una traza de fallo
# entren en el recuento y que la duración se lea de la primera coincidencia.
SUMMARY_LINE_PATTERN = re.compile(r"\bin\s+(\d+(?:\.\d+)?)s(?:\s*\([^)]*\))?\s*=*\s*$")

# Nombres de extra que aportan dependencias de test, del más específico al menos.
TEST_EXTRAS = ("test", "tests", "testing", "dev")
REQUIREMENTS_FILES = (
    "requirements-test.txt",
    "requirements-dev.txt",
    "requirements_test.txt",
    "requirements_dev.txt",
    "requirements/test.txt",
    "requirements/dev.txt",
)

COLLECTION_ERROR_MARKERS = ("ERROR collecting", "ImportError", "ModuleNotFoundError", "INTERNALERROR")

# Los `addopts` de un proyecto pueden exigir plugins que no declara en ninguna
# parte. Neutralizar los addopts no es opción: suelen incluir cosas como
# --doctest-modules, que en algunos repos son media suite.
PLUGIN_BY_FLAG = {
    "--cov": "pytest-cov",
    "-n": "pytest-xdist",
    "--numprocesses": "pytest-xdist",
    "--timeout": "pytest-timeout",
    "--benchmark": "pytest-benchmark",
    "--asyncio": "pytest-asyncio",
    "--randomly": "pytest-randomly",
    "--mypy": "pytest-mypy",
    "--flake8": "pytest-flake8",
    "--snapshot": "pytest-snapshot",
}
UNRECOGNISED_PATTERN = re.compile(r"unrecognized arguments:(.*)")


@dataclass(frozen=True)
class Strategy:
    """Un intento de instalar las dependencias de test que el repo declara."""

    label: str
    args: list[str]

    def __eq__(self, other: object) -> bool:  # pragma: no cover - trivial
        return isinstance(other, Strategy) and (self.label, self.args) == (other.label, other.args)


def _read_pyproject(repo: Path) -> dict:
    path = repo / "pyproject.toml"
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def install_strategies(repo: Path) -> list[Strategy]:
    """Intentos de instalación ordenados, derivados de lo que el repo declara.

    Nunca inventa un extra: si el repo no lo declara, no se intenta. Solo se
    consideran extras y grupos cuyo nombre sugiera dependencias de test —
    instalar el extra `docs` no acerca la suite a poder ejecutarse.
    """
    strategies: list[Strategy] = []
    config = _read_pyproject(repo)

    extras = config.get("project", {}).get("optional-dependencies", {})
    for name in TEST_EXTRAS:
        if name in extras:
            strategies.append(Strategy(f"extra:{name}", ["-e", f".[{name}]"]))

    groups = config.get("dependency-groups", {})
    for name in TEST_EXTRAS:
        if name in groups:
            strategies.append(Strategy(f"group:{name}", ["--group", name]))

    for relative in REQUIREMENTS_FILES:
        if (repo / relative).exists():
            strategies.append(Strategy(f"requirements:{relative}", ["-r", relative]))

    tox_deps = _tox_testenv_deps(repo)
    if tox_deps:
        strategies.append(Strategy("tox:testenv", tox_deps))

    return strategies


def _tox_testenv_deps(repo: Path) -> list[str]:
    """Dependencias del entorno de test por defecto declaradas en tox.ini.

    Muchos repos anteriores a pyproject no declaran extras en ninguna parte y
    solo dicen qué necesita su suite aquí. Se lee `[testenv]` y nada más: los
    entornos de lint o de tipos traen herramientas que no hacen falta.
    """
    path = repo / "tox.ini"
    if not path.exists():
        return []
    parser = configparser.ConfigParser()
    try:
        parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
    except (configparser.Error, OSError):
        return []
    raw = parser.get("testenv", "deps", fallback="")
    deps = []
    for line in raw.splitlines():
        dep = line.split("#")[0].strip()
        if dep:
            deps.append(dep)
    return deps


def plugins_for_unrecognised(output: str) -> list[str]:
    """Plugins de pytest que hacen falta, deducidos de los flags rechazados."""
    match = UNRECOGNISED_PATTERN.search(output)
    if not match:
        return []
    rejected = match.group(1)
    found = {
        package
        for flag, package in PLUGIN_BY_FLAG.items()
        # El flag puede venir compuesto (`--cov-fail-under=100`), así que se
        # busca como prefijo de token; el `(?![\w])` impide que `--covfefe`
        # cuente, pero deja pasar las variantes con guion, que son las que
        # escriben los proyectos.
        if re.search(rf"(?<![\w-]){re.escape(flag)}(?=[\s=\-]|$)", rejected)
    }
    return sorted(found)


def editable_locations(pip_list_output: str) -> set[str]:
    """Directorios de proyecto que pip tiene instalados en modo editable.

    Se le pregunta a pip en vez de importar el paquete y mirar su `__file__`,
    porque el nombre de distribución no tiene por qué coincidir con el de
    import — python-dateutil se importa como dateutil — y adivinar esa
    correspondencia es justo el tipo de heurística que falla en silencio.
    """
    try:
        entries = json.loads(pip_list_output)
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(entries, list):
        return set()
    return {
        entry["editable_project_location"]
        for entry in entries
        if isinstance(entry, dict) and entry.get("editable_project_location")
    }


def collection_failed(output: str) -> bool:
    """True si pytest no pudo colectar una suite utilizable.

    Colectar cero tests cuenta como fallo: un repo del que no sale ningún test
    no puede admitirse como suite verde.
    """
    if any(marker in output for marker in COLLECTION_ERROR_MARKERS):
        return True
    if "no tests ran" in output:
        return True
    return not re.search(r"\b(\d+)\s+tests?\s+collected", output)


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


def _run(command: list[str], cwd: Path, timeout: int) -> tuple[int, str, bool]:
    """Devuelve (código, salida combinada, si expiró el tiempo)."""
    try:
        completed = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, check=False, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as expired:
        partial = (expired.stdout or "") + (expired.stderr or "")
        return 1, partial if isinstance(partial, str) else "", True
    except OSError as error:
        return 1, f"{type(error).__name__}: {error}", False
    return completed.returncode, completed.stdout + completed.stderr, False


def resolve_locations(repo: Path, env_dir: Path | None) -> tuple[Path, Path]:
    """Rutas absolutas del repo y de su entorno.

    Los comandos se lanzan con `cwd=repo`, así que una ruta relativa se
    resolvería dos veces y el entorno acabaría en `repo/repo/.acp-venv`, fuera
    del alcance de todo lo que viene después.
    """
    repo = Path(repo).expanduser().resolve()
    env_dir = Path(env_dir).expanduser().resolve() if env_dir else repo / ".acp-venv"
    return repo, env_dir


def prepare_environment(repo: Path, env_dir: Path, timeout: int = 1800) -> SuiteMetrics:
    """Crea el entorno del repo e instala lo necesario para colectar su suite.

    Devuelve las métricas con la parte de preparación rellena. `collect_ok` dice
    si la suite llegó a colectarse: es la comprobación funcional que sustituye a
    mirar códigos de salida de pip, que mienten.
    """
    repo, env_dir = resolve_locations(repo, env_dir)
    metrics = SuiteMetrics(attempted=True)
    started = time.monotonic()

    code, output, timed_out = _run([sys.executable, "-m", "venv", str(env_dir)], repo, timeout)
    if code != 0 or timed_out:
        metrics.install_error = f"venv: {output[-800:]}"
        metrics.timed_out = timed_out
        metrics.install_seconds = time.monotonic() - started
        return metrics

    return install_and_collect(repo, VenvRunner(repo, env_dir), timeout, metrics, started)


def install_and_collect(
    repo: Path,
    runner: VenvRunner | DockerRunner,
    timeout: int,
    metrics: SuiteMetrics,
    started: float,
) -> SuiteMetrics:
    """Instala lo que el repo declara y comprueba que su suite se colecta.

    Es la capa de decisión, y es idéntica en los dos ejecutores: qué se intenta
    instalar, cómo se comprueba que funcionó y qué plugins faltan no dependen de
    si esto corre en un entorno virtual o dentro de un contenedor.
    """
    pip = [runner.python, "-m", "pip", "install", "--disable-pip-version-check", "-q"]

    code, output, timed_out = _run(runner.wrap([*pip, "-e", "."]), repo, timeout)
    if code != 0 or timed_out:
        metrics.install_error = f"install -e .: {output[-800:]}"
        metrics.timed_out = timed_out
        metrics.install_seconds = time.monotonic() - started
        return metrics
    metrics.install_ok = True

    _run(runner.wrap([*pip, "pytest"]), repo, timeout)

    collect = runner.wrap([runner.python, "-m", "pytest", "--collect-only", "-q"])

    def try_collect() -> str:
        """Colecta, y si pytest rechaza flags de los addopts, instala los
        plugins que faltan y vuelve a intentarlo una vez."""
        _, output, _ = _run(collect, repo, timeout)
        plugins = plugins_for_unrecognised(output)
        if plugins:
            _run(runner.wrap([*pip, *plugins]), repo, timeout)
            _, output, _ = _run(collect, repo, timeout)
        return output

    collect_output = try_collect()

    if not collection_failed(collect_output):
        metrics.install_strategy = "base"
        metrics.collect_ok = True
    else:
        for strategy in install_strategies(repo):
            code, output, timed_out = _run(runner.wrap([*pip, *strategy.args]), repo, timeout)
            if timed_out:
                metrics.timed_out = True
                break
            if code != 0:
                continue
            collect_output = try_collect()
            if not collection_failed(collect_output):
                metrics.install_strategy = strategy.label
                metrics.collect_ok = True
                break

    if metrics.collect_ok:
        metrics.tree_under_test = _restore_tree_under_test(repo, runner, pip, timeout)
        if not metrics.tree_under_test:
            metrics.install_error = (
                "el árbol del repo no quedó instalado como el paquete bajo prueba"
            )
    else:
        metrics.install_error = f"collect: {collect_output[-800:]}"
    metrics.install_seconds = time.monotonic() - started
    return metrics


def _tree_is_under_test(repo: Path, runner, timeout: int) -> bool:
    listing = runner.wrap([runner.python, "-m", "pip", "list", "--format=json"])
    _, output, _ = _run(listing, repo, timeout)
    return runner.project_dir in editable_locations(output)


def _restore_tree_under_test(repo: Path, runner, pip: list[str], timeout: int) -> bool:
    """Comprueba que lo instalado es el árbol, y lo reinstala si no lo es.

    Verificado con dateutil: `pip install -r requirements-dev.txt` arrastra
    freezegun, que depende de python-dateutil, y pip **desinstala la editable**
    para poner la versión de PyPI. A partir de ahí la suite mide el paquete
    publicado y no el repositorio, sin que nada lo anuncie.
    """
    if _tree_is_under_test(repo, runner, timeout):
        return True
    _run(runner.wrap([*pip, "-e", "."]), repo, timeout)
    return _tree_is_under_test(repo, runner, timeout)


def run_suite_in_venv(
    repo: Path,
    env_dir: Path | None = None,
    timeout: int = 3600,
    keep_env: bool = False,
) -> SuiteMetrics:
    """Prepara el entorno del repo, ejecuta su suite y limpia.

    El entorno se borra al terminar salvo que se pida conservarlo: en la campaña
    completa se reutiliza uno por repositorio, pero al perfilar candidatos se
    descarta para no acumular gigas (§2 del spec).
    """
    repo, env_dir = resolve_locations(repo, env_dir)
    try:
        metrics = prepare_environment(repo, env_dir, timeout=timeout)
        return run_prepared_suite(repo, VenvRunner(repo, env_dir), timeout, metrics)
    finally:
        if not keep_env and env_dir.exists():
            shutil.rmtree(env_dir, ignore_errors=True)


def run_suite_in_docker(
    repo: Path,
    image: str = DEFAULT_IMAGE,
    timeout: int = 3600,
) -> SuiteMetrics:
    """Prepara y pasa la suite dentro de un contenedor, y lo destruye siempre.

    Es el ejecutor de la campaña: aquí el aislamiento es de sistema, así que un
    repo que ensucie el entorno global no puede contaminar la corrida siguiente
    — el coste declarado del ejecutor sin contenedor (§5.6 del spec).
    """
    repo, _ = resolve_locations(repo, None)
    runner = DockerRunner(repo=repo, image=image)
    metrics = SuiteMetrics(attempted=True)
    started = time.monotonic()

    # Un contenedor huérfano de una corrida anterior bloquearía el nombre.
    _run(runner.stop_command(), repo, timeout)

    code, output, timed_out = _run(runner.start_command(), repo, timeout)
    if code != 0 or timed_out:
        metrics.install_error = f"docker run: {output[-800:]}"
        metrics.timed_out = timed_out
        metrics.install_seconds = time.monotonic() - started
        return metrics

    try:
        code, output, timed_out = _run(runner.copy_command(), repo, timeout)
        if code != 0 or timed_out:
            metrics.install_error = f"docker cp: {output[-800:]}"
            metrics.timed_out = timed_out
            metrics.install_seconds = time.monotonic() - started
            return metrics

        _run(runner.trust_command(), repo, timeout)
        metrics = install_and_collect(repo, runner, timeout, metrics, started)
        return run_prepared_suite(repo, runner, timeout, metrics)
    finally:
        _run(runner.stop_command(), repo, timeout)


def run_prepared_suite(
    repo: Path,
    runner: VenvRunner | DockerRunner,
    timeout: int,
    metrics: SuiteMetrics,
) -> SuiteMetrics:
    """Pasa la suite de un entorno ya preparado y conserva lo medido al prepararlo.

    El coste de preparación viaja con el resultado porque es la mitad del
    criterio de coste del spec §3.2, y se pierde si solo se devuelve el resumen
    de pytest.
    """
    if not metrics.collect_ok:
        return metrics

    _, output, timed_out = _run(
        runner.wrap([runner.python, "-m", "pytest", "-q"]), repo, timeout
    )
    if timed_out:
        metrics.timed_out = True
        return metrics

    result = parse_pytest_summary(output)
    result.attempted = True
    result.install_ok = metrics.install_ok
    result.install_strategy = metrics.install_strategy
    result.install_seconds = metrics.install_seconds
    result.collect_ok = True
    result.tree_under_test = metrics.tree_under_test
    return result
