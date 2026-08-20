import subprocess
import sys
from pathlib import Path

from acp.runners import DEFAULT_IMAGE, DockerRunner, VenvRunner


def test_the_container_starts_without_mounting_anything():
    """Medido sobre python-stdnum en el mismo contenedor y con el mismo
    entorno: 113 s de suite sobre el volumen montado frente a 43 s copiando el
    repo dentro. El coste se multiplica por 54 corridas (§3.2 del spec)."""
    runner = DockerRunner(repo=Path("/clones/pint"), container="acp-pint")

    assert runner.start_command() == [
        "docker", "run", "--detach", "--name", "acp-pint", "--workdir", "/repo",
        DEFAULT_IMAGE, "sleep", "infinity",
    ]


def test_the_repo_is_copied_into_the_container():
    """El `/.` copia el contenido, no el directorio: sin él acabaría en
    /repo/pint y nada de lo que viene después encontraría el repo."""
    runner = DockerRunner(repo=Path("/clones/pint"), container="acp-pint")

    assert runner.copy_command() == ["docker", "cp", "/clones/pint/.", "acp-pint:/repo"]


def test_commands_run_inside_the_container():
    runner = DockerRunner(repo=Path("/clones/pint"), container="acp-pint")

    assert runner.wrap(["python", "-m", "pytest", "-q"]) == [
        "docker", "exec", "--workdir", "/repo", "acp-pint",
        "python", "-m", "pytest", "-q",
    ]


def test_the_container_is_destroyed_by_force():
    """Sin `--force` un contenedor vivo no se borra, y el siguiente repo
    chocaría con el nombre en vez de arrancar."""
    runner = DockerRunner(repo=Path("/clones/pint"), container="acp-pint")

    assert runner.stop_command() == ["docker", "rm", "--force", "acp-pint"]


def test_git_is_trusted_inside_the_mount():
    """El montaje entra con otro uid, así que git lo declara `dubious
    ownership` y aborta — y varios candidatos derivan su versión del
    repositorio al instalarse."""
    runner = DockerRunner(repo=Path("/clones/pint"), container="acp-pint")

    assert runner.trust_command() == [
        "docker", "exec", "--workdir", "/repo", "acp-pint",
        "git", "config", "--global", "--add", "safe.directory", "/repo",
    ]


def test_container_name_is_derived_from_the_repo():
    runner = DockerRunner(repo=Path("/clones/python-stdnum"))

    assert runner.container == "acp-python-stdnum"


def test_container_name_drops_characters_docker_rejects():
    runner = DockerRunner(repo=Path("/clones/py.moneyed 2"))

    assert runner.container == "acp-py.moneyed_2"


def test_the_default_image_carries_git():
    """`python:3.12-slim` no trae git y `pip install -e .` aborta en pint,
    jsonschema, dateutil y sqlglot, que derivan su versión del repositorio."""
    assert "slim" not in DEFAULT_IMAGE


def test_a_virtualenv_runner_runs_the_command_it_is_given():
    """El comando sale entero y sin tocar: lo único que se le pone delante es el
    entorno de la corrida, que es lo que impide que dos condiciones compartan la
    caché del usuario."""
    runner = VenvRunner(repo=Path("/clones/pint"), env_dir=Path("/clones/pint/.acp-venv"))

    wrapped = runner.wrap(["python", "-m", "pytest", "-q"])

    assert wrapped[-4:] == ["python", "-m", "pytest", "-q"]
    assert wrapped[0] == "env"
    assert f"HOME={runner.home}" in wrapped


def test_a_virtualenv_runner_points_python_at_its_own_environment():
    runner = VenvRunner(repo=Path("/clones/pint"), env_dir=Path("/clones/pint/.acp-venv"))

    assert runner.python.startswith("/clones/pint/.acp-venv")
    assert runner.python.endswith(("python", "python.exe"))


def test_each_runner_knows_where_the_project_lives_for_it():
    """La comprobación de que se prueba el árbol y no la versión de PyPI
    compara rutas, y la del contenedor no es la del anfitrión."""
    assert DockerRunner(repo=Path("/clones/pint")).project_dir == "/repo"
    assert VenvRunner(repo=Path("/clones/pint"), env_dir=Path("/e")).project_dir == "/clones/pint"


def test_a_docker_runner_uses_the_interpreter_of_the_image():
    runner = DockerRunner(repo=Path("/clones/pint"))

    assert runner.python == "python"


def _seen_home(runner: VenvRunner, marker: Path) -> str:
    """Lo que ve un proceso lanzado por este ejecutor: su HOME y si el testigo
    de la corrida anterior sigue ahí."""
    code = (
        "import pathlib, sys;"
        "home = pathlib.Path.home();"
        f"print(home, (home / {str(marker)!r}).exists())"
    )
    salida = subprocess.run(
        runner.wrap([sys.executable, "-c", code]),
        capture_output=True, text=True, check=False,
    )
    assert salida.returncode == 0, salida.stderr
    return salida.stdout.strip()


def test_what_one_run_leaves_in_its_home_the_next_one_does_not_find(tmp_path: Path):
    """§5.4.4: cada ejecución arranca sin estado compartido con la anterior, y
    el ejecutor sin contenedor corre en la máquina de verdad.

    Medido sobre pint, que guarda su caché de unidades en la del usuario con
    `cache_folder=":auto:"`: la condición base deja ahí los pickles, B1 cambia
    el `__module__` de 25 definiciones y la corrida siguiente muere con
    `AttributeError: Can't get attribute 'OffsetConverter'` —un fallo que no
    tiene nada que ver con lo que la celda mide—. Con la caché limpia, la misma
    corrida vuelve a dar 2.289 passed, idéntico a la base.
    """
    primera = VenvRunner(tmp_path / "base", tmp_path / ".acp-venv-base")
    primera.home.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        primera.wrap([
            sys.executable, "-c",
            "import pathlib; (pathlib.Path.home() / 'unidades.pickle').write_text('1')",
        ]),
        capture_output=True, text=True, check=True,
    )

    segunda = VenvRunner(tmp_path / "B1", tmp_path / ".acp-venv-B1")
    segunda.home.mkdir(parents=True, exist_ok=True)

    assert _seen_home(segunda, Path("unidades.pickle")).endswith("False")


def test_the_hosts_home_is_not_where_the_suite_runs(tmp_path: Path):
    """La caché `:auto:` de pint vive bajo el HOME del anfitrión, así que dejar
    que el hijo lo herede es compartir un directorio entre todas las condiciones
    de la campaña."""
    runner = VenvRunner(tmp_path / "repo", tmp_path / ".acp-venv-repo")
    runner.home.mkdir(parents=True, exist_ok=True)

    visto = _seen_home(runner, Path("nada")).split(" ")[0]

    assert visto != str(Path.home())


def test_the_container_does_not_need_a_home_of_its_own():
    """El contenedor se crea y se destruye en cada corrida, así que su
    aislamiento ya es de sistema: meterle un HOME falso sería fontanería que no
    aísla nada y una diferencia más entre los dos ejecutores."""
    runner = DockerRunner(repo=Path("/clones/pint"), container="acp-pint")

    assert runner.wrap(["python"])[:3] == ["docker", "exec", "--workdir"]
