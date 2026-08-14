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


def test_a_virtualenv_runner_runs_commands_as_they_come():
    runner = VenvRunner(repo=Path("/clones/pint"), env_dir=Path("/clones/pint/.acp-venv"))

    assert runner.wrap(["python", "-m", "pytest", "-q"]) == ["python", "-m", "pytest", "-q"]


def test_a_virtualenv_runner_points_python_at_its_own_environment():
    runner = VenvRunner(repo=Path("/clones/pint"), env_dir=Path("/clones/pint/.acp-venv"))

    assert runner.python.startswith("/clones/pint/.acp-venv")
    assert runner.python.endswith(("python", "python.exe"))


def test_a_docker_runner_uses_the_interpreter_of_the_image():
    runner = DockerRunner(repo=Path("/clones/pint"))

    assert runner.python == "python"
