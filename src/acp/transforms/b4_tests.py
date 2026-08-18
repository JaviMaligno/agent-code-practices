"""B4 — tests visibles: la suite del repo, fuera del alcance del agente.

No se mide si el agente escribe tests: se mide si los que ya existen le sirven
de documentación ejecutable para entender cómo se usa una pieza (§4.2 del spec).
Por eso la suite **se mueve**, no se borra: los tests de validación siguen
ejecutándose —fuera del árbol, donde el agente no llega— y no se tocan nunca.
Ocultarlos no puede significar perderlos, porque son lo que decide si la
condición fue equivalente (§4.3).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from acp.metrics.size import read_source
from acp.transforms.base import PYTEST_CONFIG_FILES, TransformResult

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


# Las claves con las que un repo declara DÓNDE están sus tests. `addopts` puede
# llevar rutas sueltas entre las opciones (python-stdnum: `--doctest-modules
# --doctest-glob="*.doctest" stdnum tests --ignore=...`) y `testpaths` es una
# lista de rutas. El prefijo opcional cubre la forma de clave con puntos que usa
# holidays —`[tool.pytest]` + `ini_options.testpaths = [ "tests" ]`—, que un
# lector de secciones `[tool.pytest.ini_options]` no vería.
_COLLECT_PATH_KEYS = re.compile(r"^\s*(?:ini_options\.)?(?:addopts|testpaths)\s*=")


def _names_the_suite(name: str) -> re.Pattern[str]:
    """La ruta de la suite como argumento suelto, y nada que se le parezca.

    Lo que se quita es un elemento de la lista de colecta: `tests`, `"tests"`,
    `tests,`. Lo que NO se quita, y por eso los dos lookarounds: `tests_helpers`
    y `tests/slow` (otra ruta), `--cov=tests` y `--ignore=tests` (opciones con
    valor, que apuntar a algo que ya no existe no rompe nada y reescribirlas
    sería quitarle al repo una decisión suya).
    """
    return re.compile(
        rf"""(?<![\w./=:-])(["']?){re.escape(name)}\1(?![\w./-])[ \t]*,?[ \t]*"""
    )


def _value_continues(collected: str, line: str) -> bool:
    """Si la línea siguiente sigue siendo el valor de la misma clave.

    Dos formas, una por formato: una lista TOML abierta continúa hasta que
    cierra el corchete, y un valor INI multilínea continúa mientras las líneas
    vengan sangradas. Sin esto, un `addopts` repartido en varias líneas —la
    forma habitual cuando son muchas opciones— dejaría la ruta de la suite en la
    segunda línea sin tocar.
    """
    if collected.count("[") > collected.count("]"):
        return True
    return line[:1] in (" ", "\t") and line.strip() != ""


def _drop_collect_paths(text: str, names: list[str]) -> str:
    patterns = [_names_the_suite(name) for name in names]
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    index = 0
    while index < len(lines):
        if _COLLECT_PATH_KEYS.match(lines[index]) is None:
            out.append(lines[index])
            index += 1
            continue
        block = lines[index]
        index += 1
        while index < len(lines) and _value_continues(block, lines[index]):
            block += lines[index]
            index += 1
        for pattern in patterns:
            block = pattern.sub("", block)
        out.append(block)
    return "".join(out)


def _stop_naming_the_suite(root: Path, found: list[Path], destination: Path) -> int:
    """Saca la suite de la configuración de pytest, y guarda el original.

    Medido sobre el clon de python-stdnum, cuyos `addopts` nombran `tests` como
    ruta de colecta: con la suite fuera del árbol, pytest **no arranca** —`ERROR:
    file or directory not found: tests`— y colecta 0 donde el baseline colecta
    422. Los 251 que se pierden de más son los doctests sobre `stdnum/` que
    `--doctest-modules` colecta y que B4 nunca quiso esconder: la condición
    quitaba mucho más que su dosis y, de paso, le anunciaba al agente que le
    habían quitado algo. Con la ruta fuera, el árbol colecta esos 251 y ya no
    queda nada que apunte a lo escondido.

    No es "neutralizar los addopts", que §5.6 descarta: se quita UN elemento de
    la lista de colecta —el que la propia transformación acaba de mover— y se
    conserva todo lo demás, `--doctest-modules` el primero, que es justo lo que
    aquella regla protege. Es lo mismo que hace `_rewrite_configured_paths` de
    B2 en estos mismos ficheros, en la otra forma: quien mueve algo que la
    configuración nombra tiene que dejar la configuración consistente con el
    movimiento.

    El original se guarda con la suite, no se pierde. La verificación restaura
    volcando lo guardado sobre la raíz dentro del contenedor, así que la
    configuración vuelve junto a los tests y la corrida de equivalencia colecta
    exactamente lo que colectaba el baseline. Guardar el fichero entero es
    seguro porque B4 es la última del orden canónico: la copia se toma después
    de que B2 y B3 hayan hecho lo suyo, así que restaurarla no deshace nada más
    que esta edición.

    Las tres salidas posibles, colectadas sobre ese mismo clon:

        sin tocar nada (el defecto)          0
        dejando un `tests/` vacío          251
        quitando la ruta de la colecta     251
        tras restaurar, en los dos         422

    Las dos salidas empatan en lo que se puede contar, así que la decisión se
    tomó por lo que dejan en el árbol. Un `tests/` vacío es un fichero que el
    repositorio original no tenía, puesto ahí por la transformación, y dice más
    alto que ninguna otra cosa del árbol que aquí había tests; además le devuelve
    a `suite_paths` un `tests` que existe, así que una segunda pasada de B4 sobre
    su propia salida encontraría suite, recrearía el directorio guardado y
    borraría la suite de verdad que había dentro. Quitar la ruta no añade nada al
    árbol: lo deja como el de un repo que no tiene suite, que es la condición.

    Lo que esto NO arregla, y se declara: el árbol sigue mencionando sus tests en
    prosa —el ChangeLog, el CONTRIBUTING, el `mypy tests` de `tox.ini`—. Eso es
    un límite viejo de B4, que esconde la suite y no el recuerdo de que existió;
    lo que se cierra aquí es que la condición se llevara por delante 422 tests
    cuando su dosis declarada son 171.
    """
    names = [path.relative_to(root).as_posix() for path in found]
    changed = 0
    for name in PYTEST_CONFIG_FILES:
        path = root / name
        if not path.exists():
            continue
        source = read_source(path)
        rewritten = _drop_collect_paths(source, names)
        if rewritten == source:
            continue
        (destination / name).write_text(source, encoding="utf-8")
        path.write_text(rewritten, encoding="utf-8")
        changed += 1
    return changed


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
    # Después de mover: lo que se guarda es la configuración tal y como estaba
    # cuando la suite todavía existía.
    changed += _stop_naming_the_suite(root, found, destination)
    return TransformResult(files_changed=changed)
