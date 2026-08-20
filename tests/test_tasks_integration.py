"""Valida una tarea contra un repo real. Necesita Docker y red.

Tres fases anteriores dejaron la misma lección cinco veces: un arreglo
verificado solo contra fixtures se cae al pasarlo por un repo de verdad. Aquí lo
que hay que ver caer es el circuito entero —clonar, instalar, correr, parchear,
volver a correr, comparar— sobre python-stdnum, que es el más barato del
sustrato y donde antes se notaría un desacuerdo entre lo que la tarea declara y
lo que la suite dice.

Los dos casos que importan van en la MISMA sesión: la corrida de referencia es
una propiedad del árbol y no de la tarea, así que dos tareas cuestan una
instalación y tres corridas, no dos instalaciones y cuatro.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from acp.tasks.inject import inject
from acp.tasks.validate import SuiteSession, validate_task

pytestmark = [pytest.mark.integration, pytest.mark.docker]

REPO = "https://github.com/arthurdejong/python-stdnum"

# Lo que rompe mutar el cálculo del dígito de control de la CURP mexicana,
# DECLARADO antes de correr nada: `calc_check_digit` solo lo usa `validate` del
# mismo módulo, y a `stdnum.mx.curp` no lo importa nadie más en el repo. Los dos
# sitios que lo ejercitan son el ejemplo del propio módulo y el fichero de
# doctests que le corresponde.
CURP_ROMPE = [
    "stdnum/mx/curp.py::stdnum.mx.curp",
    "tests/test_mx_curp.doctest::test_mx_curp.doctest",
]


def test_an_injected_failure_breaks_exactly_the_tests_it_declares(tmp_path: Path):
    """python-stdnum es el más barato del sustrato (96 s por corrida), que es lo
    que hace viable validar 24 tareas."""
    # El nombre del clon es el del contenedor (`acp-<nombre>`), y `SuiteSession`
    # borra el que encuentre con ese nombre antes de empezar. Un clon llamado
    # `repo` chocaría con cualquier otra suite de Docker del repositorio y le
    # arrancaría el contenedor de debajo.
    clone = tmp_path / "stdnum-validate"
    subprocess.run(
        ["git", "clone", "--depth", "1", REPO, str(clone)],
        check=True, capture_output=True,
    )

    acotada = inject(
        clone, module="stdnum.mx.curp", symbol="calc_check_digit", kind="off_by_one",
        fail_to_pass=CURP_ROMPE,
    )
    # El mismo catálogo sobre el módulo del que cuelga medio repo: `mod_97_10` lo
    # usan el IBAN y una docena de identificadores nacionales. Es la tarea que
    # NO puede valer, y el circuito tiene que decirlo por sí solo.
    desbordada = inject(
        clone, module="stdnum.iso7064.mod_97_10", symbol="checksum", kind="off_by_one",
    )

    with SuiteSession(clone, timeout=1800) as sesion:
        informe = validate_task(clone, acotada, session=sesion)
        assert informe.valid is True, (
            informe.unexpected_failures, informe.observed_failures
        )
        # Y no solo "válida": lo que se rompió es EXACTAMENTE lo declarado. Sin
        # esto la corrida seguiría en verde si el parche no llegara a entrar en
        # el contenedor y `fail_to_pass` estuviera vacío.
        assert informe.observed_failures == CURP_ROMPE

        exceso = validate_task(clone, desbordada, session=sesion)
        assert exceso.valid is False
        assert len(exceso.unexpected_failures) > 5, exceso.unexpected_failures
        print(
            f"\nacotada: {informe.observed_failures}\n"
            f"desbordada: {len(exceso.observed_failures)} rotos, "
            f"{len(exceso.unexpected_failures)} sin declarar"
        )

    # El clon del anfitrión queda como estaba: el parche vive y muere dentro del
    # contenedor, así que validar una tarea no puede contaminar la siguiente.
    assert "18 - i" in (clone / "stdnum" / "mx" / "curp.py").read_text(encoding="utf-8")
