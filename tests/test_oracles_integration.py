"""Los dos controles sobre un repositorio real y transformado. Docker y red.

§5.4.6 dice que esto se corre antes de cada bloque, no una sola vez al
principio, así que tiene que ser barato: los dos controles van en la MISMA
sesión y la corrida de referencia se paga una vez. El no-op no gasta ni eso —su
resultado ES la corrida de referencia, porque no toca el árbol—, de modo que el
bloque entero cuesta una instalación y dos corridas.

La condición es A2+A4 y no T0 por lo que se quiere ver caer: con A2 el símbolo y
todo lo que su línea nombra se llaman distinto, y con A4 desaparecen justo las
líneas de contexto con las que el hunk se sitúa —el comentario y la prosa de la
docstring—. Un oráculo que solo sepa revertir texto pasa T0 y falla aquí, y en
la tabla principal ese fallo se lee exactamente igual que un agente que no supo
arreglar el fallo.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from acp.cli import manifest_path_for, transform_repo
from acp.oracles import repaired_source, run_oracle
from acp.tasks.inject import apply_patch, inject, module_path
from acp.tasks.validate import SuiteSession

pytestmark = [pytest.mark.integration, pytest.mark.docker]

REPO = "https://github.com/arthurdejong/python-stdnum"

# La misma tarea que la fase anterior dejó validada contra el árbol original:
# mutar el dígito de control de la CURP mexicana rompe dos tests y solo dos.
MODULO, SIMBOLO = "stdnum.mx.curp", "calc_check_digit"


def test_the_oracle_scores_a_hundred_percent_on_a_transformed_tree(tmp_path: Path):
    clone = tmp_path / "stdnum-source"
    subprocess.run(
        ["git", "clone", "--depth", "1", REPO, str(clone)],
        check=True, capture_output=True,
    )
    # El orden de la campaña: primero el fallo, después la transformación. Al
    # revés, la celda no alcanzaría al código roto y el árbol entregado tendría
    # una función escrita en dos estilos, que es una pista que nadie diseñó.
    task = inject(clone, module=MODULO, symbol=SIMBOLO, kind="off_by_one")
    roto = module_path(clone, MODULO)
    roto.write_text(
        apply_patch(roto.read_text(encoding="utf-8"), task.patch), encoding="utf-8"
    )
    # El nombre del árbol es el del contenedor (`acp-<nombre>`): distinto del que
    # usan los demás tests de Docker, o una suite le arranca el contenedor a la
    # otra.
    entregado = transform_repo(clone, ["A2", "A4"], tmp_path / "stdnum-oracle")

    simbolo = json.loads(manifest_path_for(entregado).read_text(encoding="utf-8"))["symbols"]
    assert simbolo[f"{MODULO}.{SIMBOLO}"]["current_name"] != SIMBOLO, "A2 no renombró"

    with SuiteSession(entregado, timeout=1800) as sesion:
        vacio = run_oracle("no_op", entregado, task, session=sesion)
        # 0%: el árbol se entrega roto y el no-op no lo toca. Un no-op que
        # puntúa significa que los tests de la tarea no discriminan en esta
        # condición, y entonces la celda entera mide otra cosa.
        assert vacio.resolved is False
        assert vacio.still_failing, "el árbol entregado no tiene nada en rojo"

        arreglado = run_oracle("oracle", entregado, task, session=sesion)
        assert arreglado.resolved is True, (
            arreglado.still_failing, arreglado.broken
        )
        # Y lo que se puso verde es exactamente lo que el no-op vio en rojo: sin
        # esto la corrida seguiría valiendo si el arreglo no llegara a entrar en
        # el contenedor y no hubiera nada roto que arreglar.
        assert sorted(arreglado.repaired) == sorted(vacio.still_failing)
        print(
            f"\nno-op: {len(vacio.still_failing)} en rojo, resuelto={vacio.resolved}\n"
            f"oráculo: {arreglado.repaired} -> resuelto={arreglado.resolved}"
        )

    # El árbol del anfitrión sigue roto: el arreglo vive y muere dentro del
    # contenedor, así que un control no puede dejar la condición arreglada para
    # el siguiente.
    relativa, arreglable = repaired_source(entregado, task)
    assert arreglable != (entregado / relativa).read_text(encoding="utf-8")
