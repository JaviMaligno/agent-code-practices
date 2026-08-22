"""La suite de un repositorio de Node, leída con el mismo contrato que la de Python.

Lo que hay que fijar aquí es la traducción: qué es un identificador de test y qué
cuenta como veredicto. El resto del circuito —el oráculo de la celda, el
checkpoint, el resumen— no distingue lenguajes y no debería tener que hacerlo.
"""

import json

from acp.node_suite import outcomes_from_vitest


SALIDA = {
    "numTotalTests": 3,
    "testResults": [
        {
            "name": "/repo/src/router.test.ts",
            "assertionResults": [
                {"fullName": "Router matches a path", "status": "passed"},
                {"fullName": "Router rejects a bad path", "status": "failed"},
            ],
        },
        {
            "name": "/repo/src/util.test.ts",
            "assertionResults": [
                {"fullName": "Util skips on windows", "status": "skipped"},
            ],
        },
    ],
}


def test_a_test_is_identified_by_file_and_name(tmp_path):
    """El identificador tiene que ser estable entre corridas y no depender de
    dónde esté montado el repo: vitest da rutas absolutas del contenedor."""
    resultado = outcomes_from_vitest(SALIDA, root="/repo")

    assert resultado["src/router.test.ts::Router matches a path"] == "passed"
    assert resultado["src/util.test.ts::Util skips on windows"] == "skipped"


def test_the_verdicts_use_the_same_words_as_the_python_suite():
    """`compare_runs` y `cell_oracle` deciden mirando estas palabras. Si vitest
    dijera "fail" donde pytest dice "failed", una celda entera se leería como que
    nada cambió."""
    resultado = outcomes_from_vitest(SALIDA, root="/repo")

    assert set(resultado.values()) == {"passed", "failed", "skipped"}


def test_a_run_that_produced_no_verdict_is_not_silently_empty():
    """Un diccionario vacío es indistinguible de "todo pasó" para quien compare
    dos corridas, y ese es el error que hace que una suite rota parezca un agente
    que no rompió nada."""
    import pytest

    with pytest.raises(RuntimeError):
        outcomes_from_vitest({"testResults": []}, root="/repo")


def test_a_leftover_container_does_not_block_the_next_run(monkeypatch, tmp_path):
    """Una corrida que murió a mitad deja su contenedor con el mismo nombre, y el
    siguiente arranque falla con `Conflict. The container name is already in
    use`. Eso convierte un fallo pasajero en uno permanente hasta que alguien lo
    limpia a mano.
    """
    from acp import node_suite

    ejecutados: list[list[str]] = []

    def falso_run(command, timeout, check=True):
        ejecutados.append(command)
        return ""

    monkeypatch.setattr(node_suite, "_run", falso_run)
    sesion = node_suite.NodeSuiteSession(repo=tmp_path)
    sesion._clear_previous_container()

    assert any("rm" in c and "--force" in c for c in ejecutados), (
        "tiene que retirar el contenedor anterior antes de arrancar"
    )


def test_outcomes_reads_the_report_and_not_the_exit_code(monkeypatch, tmp_path):
    """`run()` devuelve (código, salida) y es fácil desempaquetarlo al revés: así
    `outcomes` intentaba parsear un entero como JSON y moría con un TypeError
    después de haber pagado la instalación y la corrida completa de la suite."""
    from acp import node_suite

    informe = {
        "testResults": [
            {"name": "/repo/a.test.ts",
             "assertionResults": [{"fullName": "hace algo", "status": "passed"}]}
        ]
    }

    monkeypatch.setattr(node_suite, "_run", lambda *a, **k: "")
    sesion = node_suite.NodeSuiteSession(repo=tmp_path)
    sesion.run = lambda cmd: (0, __import__("json").dumps(informe) if "cat" in cmd else "", False)

    assert sesion.outcomes() == {"a.test.ts::hace algo": "passed"}


def test_run_returns_what_the_agent_toolbox_unpacks(monkeypatch, tmp_path):
    """`Toolbox._shell` hace `code, output, _ = session.run(...)`, así que una
    sesión que devuelva dos valores rompe TODAS las herramientas del agente con
    un ValueError. Y el agente no distingue eso de una herramienta que no
    encuentra nada: gasta sus turnos recibiendo errores y se rinde, con
    `regions_seen` a cero y sin una sola edición.

    Pasó: las cuatro celdas de la sonda salieron como "no lo arregló" cuando
    ninguna herramienta había llegado a funcionar.
    """
    from acp import node_suite

    monkeypatch.setattr(node_suite, "_run", lambda *a, **k: "")
    sesion = node_suite.NodeSuiteSession(repo=tmp_path)

    class FalsoProceso:
        returncode = 0
        stdout = "salida"
        stderr = ""

    monkeypatch.setattr(node_suite.subprocess, "run", lambda *a, **k: FalsoProceso())

    codigo, salida, expirado = sesion.run("ls")

    assert (codigo, salida, expirado) == (0, "salida", False)
