"""B4 — tests visibles: la suite del repo, fuera del alcance del agente.

No se mide si el agente escribe tests: se mide si los que ya existen le sirven
de documentación ejecutable para entender cómo se usa una pieza (§4.2 del spec).
Por eso la suite **se mueve**, no se borra: los tests de validación siguen
ejecutándose —fuera del árbol, donde el agente no llega— y no se tocan nunca.
Ocultarlos no puede significar perderlos, porque son lo que decide si la
condición fue equivalente (§4.3).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from acp.transforms.base import TransformResult

# Solo directorios de test de primer nivel y el conftest de la raíz.
#
# Dos límites, los dos deliberados. Un paquete `testing` dentro del código
# fuente es parte del programa, no de la suite, y llevárselo cambiaría lo que se
# está midiendo. Y un directorio de tests dentro del paquete —`pint/testsuite/`
# es la forma real— tampoco se toca: el propio código fuente puede importarlo, y
# como la verificación RESTAURA la suite antes de correr, un import roto por
# habérsela llevado no lo vería nadie; el contenedor pasaría y el árbol que
# explora el agente estaría roto en silencio. Se paga en dosis, nunca en
# equivalencia, que es el mismo reparto que hace B3.
SUITE_DIRS = ("tests", "test", "testsuite")
# El conftest de la raíz es maquinaria de la suite —fixtures, plugins, rutas—:
# dejarlo enseña la mitad de lo que B4 esconde.
SUITE_FILES = ("conftest.py",)


def kept_suite_path(root: Path) -> Path:
    """Dónde se guarda la suite: hermana del árbol, nunca dentro.

    Mismo criterio que el manifiesto (§5.4.1): dentro del árbol, el agente la
    encuentra con un `ls` y la celda no mide nada.
    """
    return root.parent / f"{root.name}.acp-tests"


def suite_paths(root: Path) -> list[Path]:
    """Lo que B4 se llevaría de este repo, antes de llevárselo.

    Es pública por la misma razón que las de B3: la dosis real cambia de repo en
    repo —en pint, cuya suite vive dentro del paquete, B4 no esconde nada— y
    quien escriba los resultados tiene que poder declararla sin deducirla de un
    contador de ficheros.
    """
    found = [root / name for name in SUITE_DIRS if (root / name).is_dir()]
    found += [root / name for name in SUITE_FILES if (root / name).is_file()]
    return sorted(found)


def apply(root: Path) -> TransformResult:
    found = suite_paths(root)
    if not found:
        # Sin suite que esconder no se deja un directorio guardado vacío: la
        # verificación lo volcaría sin restaurar nada y la corrida se leería
        # como una suite que no colecta, o sea como un fracaso, cuando lo que
        # pasa es que aquí no había nada que mover.
        return TransformResult()

    destination = kept_suite_path(root)
    # Una corrida anterior de la misma condición dejaría mezclada su suite con
    # esta, y la verificación restauraría ficheros de otro árbol.
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)

    changed = 0
    for path in found:
        # Se conserva la ruta relativa al árbol porque la verificación restaura
        # volcando lo guardado sobre la raíz: si `tests/` volviera a otro sitio,
        # la configuración de pytest del repo —python-stdnum nombra `tests` como
        # ruta de colecta en sus addopts— dejaría de encontrarlo.
        shutil.move(str(path), str(destination / path.relative_to(root)))
        changed += 1
    return TransformResult(files_changed=changed)
