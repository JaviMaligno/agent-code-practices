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

import ast
import configparser
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from acp.models import SuiteMetrics
from acp.runners import CONTAINER_WORKDIR, DEFAULT_IMAGE, DockerRunner, VenvRunner

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


def declared_dependencies(repo: Path) -> list[str]:
    """Lo que el repo necesita para correr su suite, sin instalarlo a él.

    Se usa cuando la transformación ha destruido la estructura que el
    `pyproject` declara —B2 aplana los directorios— y una instalación editable
    ya no encontraría sus paquetes. El árbol se pone al alcance de pytest por
    ruta, así que ninguna transformación puede invalidar el entorno (§5.6).

    Se ordena el resultado porque la condición tiene que ser reproducible: dos
    corridas de la misma celda deben instalar lo mismo en el mismo orden.
    """
    config = _read_pyproject(repo)
    project = config.get("project", {})
    found = {item for item in project.get("dependencies", []) if isinstance(item, str)}

    # Un repo puede no tener `pyproject.toml` en absoluto —python-stdnum, que es
    # finalista, lo declara todo en setup.cfg—, y entonces leer solo el
    # pyproject devuelve la lista vacía. Con el repo deliberadamente sin
    # instalar, esa lista vacía deja la suite sin sus dependencias y la
    # condición se lee como un fracaso del agente cuando es fontanería rota,
    # que es exactamente lo que §5.6 manda evitar.
    found.update(_setup_cfg_requirements(repo))
    found.update(_setup_py_requirements(repo))

    extras = {
        **_setup_cfg_extras(repo),
        **_setup_py_extras(repo),
        **project.get("optional-dependencies", {}),
    }
    for name in TEST_EXTRAS:
        found.update(item for item in extras.get(name, []) if isinstance(item, str))

    groups = config.get("dependency-groups", {})
    for name in TEST_EXTRAS:
        # Un grupo puede incluir a otro con `{include-group = "..."}`, que no es
        # un requisito instalable: se descarta en vez de romper el pip.
        found.update(item for item in groups.get(name, []) if isinstance(item, str))

    return sorted(found)


def install_strategies(repo: Path) -> list[Strategy]:
    """Intentos de instalación ordenados, derivados de lo que el repo declara.

    Nunca inventa un extra: si el repo no lo declara, no se intenta. Solo se
    consideran extras y grupos cuyo nombre sugiera dependencias de test —
    instalar el extra `docs` no acerca la suite a poder ejecutarse.
    """
    strategies: list[Strategy] = []
    config = _read_pyproject(repo)

    extras = config.get("project", {}).get("optional-dependencies", {})
    # Un pyproject con `dynamic = ["optional-dependencies"]` no lista sus
    # extras: los declara el setup.py o el setup.cfg, y hay que ir a buscarlos.
    extras = {**_setup_cfg_extras(repo), **_setup_py_extras(repo), **extras}
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


def _setup_py_tree(repo: Path) -> ast.AST | None:
    """El setup.py parseado, o None si no hay o no se deja parsear.

    Se parsea con `ast`, no se ejecuta: correr el setup.py de un repositorio de
    terceros para averiguar qué instalar echaría abajo el aislamiento que
    justifica todo el ejecutor.
    """
    path = repo / "setup.py"
    if not path.exists():
        return None
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return None


def _string_list(node: ast.AST) -> list[str]:
    """Los literales de texto de una lista, ignorando lo que no lo sea.

    Un `install_requires` construido con una variable o una comprensión no se
    puede leer sin ejecutar el fichero: se devuelve lo que sí es literal en vez
    de inventarlo o de abortar.
    """
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return []
    return [
        element.value
        for element in node.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]


def _setup_keyword(repo: Path, name: str) -> ast.AST | None:
    """El valor de un argumento con nombre pasado a `setup(...)`."""
    tree = _setup_py_tree(repo)
    if tree is None:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == name:
                return keyword.value
    return None


def _setup_py_requirements(repo: Path) -> list[str]:
    """Dependencias de ejecución declaradas en `install_requires` del setup.py."""
    value = _setup_keyword(repo, "install_requires")
    return _string_list(value) if value is not None else []


def _setup_py_extras(repo: Path) -> dict[str, list]:
    """Extras declarados en `extras_require` dentro de setup.py, con su contenido.

    Devuelve también los requisitos y no solo los nombres porque el modo sin
    instalar el repo necesita instalarlos uno a uno: ahí no hay un
    `pip install -e '.[test]'` que los resuelva por él.
    """
    value = _setup_keyword(repo, "extras_require")
    if not isinstance(value, ast.Dict):
        return {}
    return {
        key.value: _string_list(item)
        for key, item in zip(value.keys, value.values)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _cfg_lines(raw: str) -> list[str]:
    """Los requisitos de un campo multilínea de setup.cfg, sin comentarios."""
    found = []
    for line in raw.splitlines():
        item = line.split("#")[0].strip()
        if item:
            found.append(item)
    return found


def _read_setup_cfg(repo: Path) -> configparser.ConfigParser | None:
    path = repo / "setup.cfg"
    if not path.exists():
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read_string(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (configparser.Error, OSError):
        return None
    return parser


def _setup_cfg_requirements(repo: Path) -> list[str]:
    """Dependencias de ejecución declaradas en `[options] install_requires`."""
    parser = _read_setup_cfg(repo)
    if parser is None:
        return []
    try:
        return _cfg_lines(parser.get("options", "install_requires", fallback=""))
    except configparser.Error:
        return []


def _setup_cfg_extras(repo: Path) -> dict[str, list]:
    """Extras declarados en `[options.extras_require]` de setup.cfg, con su contenido."""
    parser = _read_setup_cfg(repo)
    if parser is None or not parser.has_section("options.extras_require"):
        return {}
    found = {}
    for name in parser.options("options.extras_require"):
        try:
            found[name] = _cfg_lines(parser.get("options.extras_require", name, fallback=""))
        except configparser.Error:
            found[name] = []
    return found


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


# Versión que se le da a un repo que ya no puede derivarla de su repositorio.
# El valor da igual mientras sea válido: ninguna métrica del experimento lo usa,
# y lo que se compara entre condiciones es el resultado de la suite.
PRETEND_VERSION = "0.0.0"


def needs_pretend_version(repo: Path) -> bool:
    """Si hay que decirle su versión porque ya no puede deducirla.

    El árbol transformado se copia sin `.git` a propósito: llevarlo dentro le
    daría al agente el historial del repositorio y, con `git checkout .`, el
    código sin transformar — todas las condiciones se volverían T0. Pero varios
    candidatos derivan su versión del repositorio al instalarse, y sin `.git`
    `pip install -e .` aborta, con lo que hasta la baseline saldría NO EVALUABLE.
    """
    if (repo / ".git").exists():
        return False
    config = _read_pyproject(repo)
    if "setuptools_scm" in config.get("tool", {}):
        return True
    requires = config.get("build-system", {}).get("requires", [])
    if any(
        "setuptools" in requirement and "scm" in requirement for requirement in requires
    ):
        return True
    # hatch-vcs es setuptools-scm con otro nombre: pint lo usa, y sin
    # reconocerlo su copia sin `.git` no se instala. Se mira también
    # `[tool.hatch.version] source = "vcs"` porque es la declaración que de
    # verdad activa el plugin; el requisito de build puede escribirse de varias
    # formas (`hatch-vcs`, `hatch_vcs`, con marcador de versión).
    if any("hatch" in requirement and "vcs" in requirement for requirement in requires):
        return True
    hatch_version = config.get("tool", {}).get("hatch", {}).get("version", {})
    if hatch_version.get("source") == "vcs":
        return True
    setup_py = repo / "setup.py"
    if setup_py.exists():
        text = setup_py.read_text(encoding="utf-8-sig", errors="replace")
        if "use_scm_version" in text:
            return True
    return False


def _install_command(pip: list[str], args: list[str], pretend: bool) -> list[str]:
    """Comando de instalación, con la versión fingida si el repo la necesita."""
    command = [*pip, *args]
    if not pretend:
        return command
    return [
        "sh", "-lc",
        f"SETUPTOOLS_SCM_PRETEND_VERSION={PRETEND_VERSION} "
        + " ".join(shlex.quote(part) for part in command),
    ]


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

    El entorno vive **fuera** del árbol, hermano suyo y con su nombre delante.
    Dentro sería un artefacto del pipeline dentro del repositorio que explora el
    agente —y con `keep_env`, que es como corre la campaña, uno permanente—;
    además `docker cp` copiaría al contenedor un entorno construido en el host.
    El nombre lo ata al repo: dos clones hermanos no pueden compartir entorno.
    """
    repo = Path(repo).expanduser().resolve()
    if env_dir:
        return repo, Path(env_dir).expanduser().resolve()
    return repo, repo.parent / f".acp-venv-{repo.name}"


def _pytest_command(runner, args: list[str], install_repo: bool) -> list[str]:
    """Comando de pytest; con el repo sin instalar, hay que encontrarlo por ruta.

    Se usa `$PWD` y no `.` porque una entrada relativa de PYTHONPATH se resuelve
    contra el directorio actual **en cada import**, y las suites cambian de
    directorio a mitad de corrida: con `.` bastaría un `os.chdir` en un test para
    que el resto de módulos del árbol dejaran de encontrarse.
    """
    command = [runner.python, "-m", "pytest", *args]
    if install_repo:
        return command
    return [
        "sh", "-lc",
        'PYTHONPATH="$PWD" ' + " ".join(shlex.quote(part) for part in command),
    ]


def prepare_environment(
    repo: Path, env_dir: Path, timeout: int = 1800, install_repo: bool = True
) -> SuiteMetrics:
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

    return install_and_collect(
        repo, VenvRunner(repo, env_dir), timeout, metrics, started, install_repo=install_repo
    )


def install_and_collect(
    repo: Path,
    runner: VenvRunner | DockerRunner,
    timeout: int,
    metrics: SuiteMetrics,
    started: float,
    prepare: str | None = None,
    install_repo: bool = True,
) -> SuiteMetrics:
    """Instala lo que el repo declara y comprueba que su suite se colecta.

    Es la capa de decisión, y es idéntica en los dos ejecutores: qué se intenta
    instalar, cómo se comprueba que funcionó y qué plugins faltan no dependen de
    si esto corre en un entorno virtual o dentro de un contenedor.
    """
    pip = [runner.python, "-m", "pip", "install", "--disable-pip-version-check", "-q"]

    # `pip install --group` no existe antes de pip 25.1, y la imagen trae una
    # anterior: sin esto, un repo que declare `[dependency-groups]` —jsonschema
    # lo hace— se lee como un repo que no declara nada.
    _run(runner.wrap([*pip, "--upgrade", "pip"]), repo, timeout)

    # Se pasa por entorno y no escribiendo la versión en el pyproject: tocar el
    # pyproject cambiaría el árbol que ve el agente, y esto es fontanería del
    # pipeline, no parte de la condición. Envuelve TODOS los comandos de
    # instalación, no solo el primero: las estrategias declaradas
    # —`pip install -e '.[dev]'`— vuelven a construir el proyecto, y con el
    # arreglo a medias sqlglot instalaba pero se quedaba sin dependencias de test.
    pretend = needs_pretend_version(repo)

    def installer(args: list[str]) -> list[str]:
        return _install_command(pip, args, pretend)

    if install_repo:
        code, output, timed_out = _run(runner.wrap(installer(["-e", "."])), repo, timeout)
        if code != 0 or timed_out:
            metrics.install_error = f"install -e .: {output[-800:]}"
            metrics.timed_out = timed_out
            metrics.install_seconds = time.monotonic() - started
            return metrics
    else:
        # El árbol transformado ya no encaja con lo que declara su pyproject
        # —B2 aplana los directorios—, así que se instala lo que necesita sin
        # instalarlo a él, y pytest lo alcanza por ruta (§5.6).
        dependencies = declared_dependencies(repo)
        if dependencies:
            code, output, timed_out = _run(
                runner.wrap([*pip, *dependencies]), repo, timeout
            )
            if code != 0 or timed_out:
                metrics.install_error = f"install deps: {output[-800:]}"
                metrics.timed_out = timed_out
                metrics.install_seconds = time.monotonic() - started
                return metrics
    metrics.install_ok = True

    _run(runner.wrap([*pip, "pytest"]), repo, timeout)

    collect = runner.wrap(
        _pytest_command(runner, ["--collect-only", "-q"], install_repo)
    )

    def try_collect() -> str:
        """Colecta, y si pytest rechaza flags de los addopts, instala los
        plugins que faltan y vuelve a intentarlo una vez."""
        _, output, _ = _run(collect, repo, timeout)
        plugins = plugins_for_unrecognised(output)
        if plugins:
            _run(runner.wrap([*pip, *plugins]), repo, timeout)
            _, output, _ = _run(collect, repo, timeout)
        return output

    # Primero lo que el repo declara para sus tests, y "base" solo como último
    # recurso. Colectar no prueba que las dependencias estén: pint colecta sin
    # `pytest-subtests` y luego falla con 332 errores al ejecutar, así que usar
    # la colecta como señal para no instalar nada deja suites rotas por
    # dependencias que el propio repo sí declaraba.
    for strategy in install_strategies(repo):
        # Con el repo deliberadamente sin instalar, las estrategias que lo
        # construyen —`pip install -e '.[test]'`— no solo fallarían: si llegaran
        # a cuajar, dejarían un finder editable con el mapa de paquetes que
        # declara el pyproject, y ese finder va ANTES que PYTHONPATH, con lo que
        # los imports se resolverían contra rutas que la transformación borró.
        # Lo que traen esos extras ya lo instaló `declared_dependencies`.
        if not install_repo and "-e" in strategy.args:
            continue
        code, output, timed_out = _run(runner.wrap(installer(strategy.args)), repo, timeout)
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

    if not metrics.collect_ok and not metrics.timed_out:
        collect_output = try_collect()
        if not collection_failed(collect_output):
            metrics.install_strategy = "base"
            metrics.collect_ok = True

    if metrics.collect_ok and prepare:
        # Después de instalar lo declarado, porque el script del repo puede
        # usar una de esas dependencias: el de holidays importa polib, que
        # viene en su grupo de tests.
        metrics.prepare_command = prepare
        code, output, timed_out = _run(
            runner.wrap(["sh", "-lc", prepare]) if isinstance(runner, DockerRunner)
            else ["sh", "-lc", prepare],
            repo,
            timeout,
        )
        metrics.prepare_ok = code == 0 and not timed_out
        if not metrics.prepare_ok:
            metrics.install_error = f"prepare: {output[-800:]}"
            metrics.install_seconds = time.monotonic() - started
            return metrics

    if metrics.collect_ok:
        if install_repo:
            metrics.tree_under_test = _restore_tree_under_test(repo, runner, pip, timeout)
            if not metrics.tree_under_test:
                metrics.install_error = (
                    "el árbol del repo no quedó instalado como el paquete bajo prueba"
                )
        else:
            # No hay editable que restaurar: el árbol está bajo prueba por ruta,
            # y ninguna dependencia puede sustituirlo por su versión de PyPI
            # porque PYTHONPATH se mira antes que site-packages.
            metrics.tree_under_test = True
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
    # Vuelve a construir el proyecto, así que necesita la versión igual que la
    # instalación inicial: sin ella la reinstalación falla en silencio y el árbol
    # se queda midiendo el paquete de PyPI.
    command = _install_command(pip, ["-e", "."], needs_pretend_version(repo))
    _run(runner.wrap(command), repo, timeout)
    return _tree_is_under_test(repo, runner, timeout)


def run_suite_in_venv(
    repo: Path,
    env_dir: Path | None = None,
    timeout: int = 3600,
    keep_env: bool = False,
    install_repo: bool = True,
) -> SuiteMetrics:
    """Prepara el entorno del repo, ejecuta su suite y limpia.

    El entorno se borra al terminar salvo que se pida conservarlo: en la campaña
    completa se reutiliza uno por repositorio, pero al perfilar candidatos se
    descarta para no acumular gigas (§2 del spec).
    """
    repo, env_dir = resolve_locations(repo, env_dir)
    try:
        metrics = prepare_environment(
            repo, env_dir, timeout=timeout, install_repo=install_repo
        )
        return run_prepared_suite(
            repo, VenvRunner(repo, env_dir), timeout, metrics, install_repo=install_repo
        )
    finally:
        if not keep_env and env_dir.exists():
            shutil.rmtree(env_dir, ignore_errors=True)


def run_suite_in_docker(
    repo: Path,
    image: str = DEFAULT_IMAGE,
    timeout: int = 3600,
    prepare: str | None = None,
    install_repo: bool = True,
    tests_from: Path | None = None,
) -> SuiteMetrics:
    """Prepara y pasa la suite dentro de un contenedor, y lo destruye siempre.

    Es el ejecutor de la campaña: aquí el aislamiento es de sistema, así que un
    repo que ensucie el entorno global no puede contaminar la corrida siguiente
    — el coste declarado del ejecutor sin contenedor (§5.6 del spec).

    `tests_from` es lo que hace medible a B4: la suite que la transformación se
    llevó fuera del árbol vuelve **solo aquí dentro**, que es donde no hay
    agente. El árbol del anfitrión no se toca, así que la condición sigue siendo
    la misma después de verificarla (§4.2).
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

        # `is_dir()` y no solo `is not None`: B4 no deja directorio guardado
        # cuando no encontró suite de raíz que esconder —la forma de pint, cuya
        # suite vive dentro del paquete—, y quien corre la celda pasa igualmente
        # `kept_suite_path`, porque solo el repo decide si existe. Sin este
        # guardarraíl, `docker cp` falla con "no such file or directory" y la
        # celda entera se lee como un fracaso total del agente cuando lo que
        # pasa es que aquí no había nada que mover: fontanería rota disfrazada
        # de resultado, justo lo que prohíbe §5.6. Saltárselo no puede esconder
        # una ruta equivocada: sin la suite restaurada la colecta falla y la
        # corrida lo dice.
        if tests_from is not None and tests_from.is_dir():
            # Después de que el árbol esté en su sitio —si no, `docker cp` del
            # repo aplastaría lo restaurado— y antes de instalar, porque la
            # colecta tiene que encontrarla: la configuración de pytest de
            # varios repos nombra `tests` como ruta, y sin restaurar la suite
            # pytest aborta antes de colectar nada.
            code, output, timed_out = _run(
                ["docker", "cp", f"{tests_from}/.", f"{runner.container}:{CONTAINER_WORKDIR}"],
                repo, timeout,
            )
            if code != 0 or timed_out:
                # Sin la suite restaurada no hay nada que verificar, y una
                # corrida sin tests se lee igual que una suite en rojo.
                metrics.install_error = f"docker cp tests: {output[-800:]}"
                metrics.timed_out = timed_out
                metrics.install_seconds = time.monotonic() - started
                return metrics

        _run(runner.trust_command(), repo, timeout)
        metrics = install_and_collect(
            repo, runner, timeout, metrics, started, prepare, install_repo=install_repo
        )
        return run_prepared_suite(repo, runner, timeout, metrics, install_repo=install_repo)
    finally:
        _run(runner.stop_command(), repo, timeout)


def run_prepared_suite(
    repo: Path,
    runner: VenvRunner | DockerRunner,
    timeout: int,
    metrics: SuiteMetrics,
    install_repo: bool = True,
) -> SuiteMetrics:
    """Pasa la suite de un entorno ya preparado y conserva lo medido al prepararlo.

    El coste de preparación viaja con el resultado porque es la mitad del
    criterio de coste del spec §3.2, y se pierde si solo se devuelve el resumen
    de pytest.
    """
    if not metrics.collect_ok:
        return metrics

    _, output, timed_out = _run(
        runner.wrap(_pytest_command(runner, ["-q"], install_repo)), repo, timeout
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
    result.prepare_command = metrics.prepare_command
    result.prepare_ok = metrics.prepare_ok
    return result
