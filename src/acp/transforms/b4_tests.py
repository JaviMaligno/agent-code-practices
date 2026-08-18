"""B4 — tests visibles: la suite del repo, fuera del alcance del agente.

No se mide si el agente escribe tests: se mide si los que ya existen le sirven
de documentación ejecutable para entender cómo se usa una pieza (§4.2 del spec).
Por eso la suite **se mueve**, no se borra: los tests de validación siguen
ejecutándose —fuera del árbol, donde el agente no llega— y no se tocan nunca.
Ocultarlos no puede significar perderlos, porque son lo que decide si la
condición fue equivalente (§4.3).
"""

from __future__ import annotations

import ast
import re
import shutil
from fnmatch import fnmatch
from pathlib import Path

from acp.metrics.size import is_excluded_dir, is_test_dir, read_source
from acp.transforms.base import (
    NOT_TRANSFORMABLE,
    PYTEST_CONFIG_FILES,
    TransformResult,
    iter_transformable_files,
)

# Los nombres desnudos con los que un repo llama a su suite. Reconocerlos es
# `acp.metrics.size.is_test_dir`, que es también quien decide qué texto es de la
# suite en B2: es la misma pregunta, y contestarla dos veces por separado es lo
# que ya produjo una celda a cero. Esta tupla se queda porque nombra la forma
# habitual y es lo que se comprueba contra los repos reales.
SUITE_DIRS = ("tests", "test", "testsuite")
# Los artefactos que, con la suite ya fuera, la siguen nombrando desde dentro
# del árbol: `.pytest_cache/v/cache/nodeids` lista los IDs de los tests y
# `*.egg-info/SOURCES.txt` sus rutas. Son los dos que `NOT_COPYABLE` nombra por
# esta misma razón; aquí se repiten sueltos y no se reutiliza aquella lista
# porque aquella dice qué NO copiar y esta qué BORRAR de un árbol existente, y
# entre lo que no se copia está `.git`.
POINTERS_TO_THE_SUITE = (".pytest_cache", "*.egg-info")

# El conftest de la raíz es maquinaria de la suite —fixtures, plugins, rutas—:
# dejarlo enseña la mitad de lo que B4 esconde.
SUITE_FILES = ("conftest.py",)


def _suite_dirs(root: Path) -> list[Path]:
    """Los directorios de test del árbol, también los que viven dentro del código.

    Mirar solo el primer nivel dejaba a pint leyéndose como un repo sin suite:
    la suya es `pint/testsuite/` —29 ficheros `test_*.py`, 43 en total— y en la
    raíz no hay ninguno de los nombres que se buscaban, así que la celda salía a
    cero ficheros sacados, cero directorios y la condición «los tests no están»
    sin aplicar.

    Hacia dentro solo se busca por donde hay código. La suite escondida que esto
    persigue vive dentro del paquete; un `tests/` de la documentación o de un
    directorio de datos no es la suite del repo, y descender a todas partes es
    exactamente cómo se cuelan. El filtro es el de `acp.metrics.size` otra vez:
    lo que no es código del repo tampoco esconde su suite. Y no se desciende a
    lo ya encontrado: lo que cuelga de un directorio de tests viaja con él.
    """
    found: list[Path] = []
    pending = [root]
    while pending:
        current = pending.pop()
        for child in sorted(current.iterdir()):
            if not child.is_dir() or child.is_symlink() or child.name.startswith("."):
                continue
            if child.name in NOT_TRANSFORMABLE:
                continue
            if is_test_dir(child.name):
                found.append(child)
            elif not is_excluded_dir(child.name) and any(child.glob("*.py")):
                pending.append(child)
    return found


def _imported_modules(path: Path, root: Path) -> set[str]:
    """Los módulos que este fichero importa, los relativos ya resueltos.

    Se resuelven los relativos porque dentro de un paquete la forma normal de
    depender de un subdirectorio es `from . import testsuite`, y buscar solo la
    ruta absoluta dejaría pasar justo el caso más común.
    """
    try:
        tree = ast.parse(read_source(path))
    except (SyntaxError, ValueError):
        return set()
    # El paquete desde el que cuentan los puntos de un import relativo. Vale
    # igual para un `__init__.py`: el fichero *es* su paquete.
    package = path.relative_to(root).parts[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                kept = len(package) - (node.level - 1)
                if kept < 0:
                    continue
                base = ".".join([*package[:kept], *([base] if base else [])])
            if not base:
                continue
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def _the_program_imports_it(root: Path, candidate: Path, suites: list[Path]) -> bool:
    """Si algo que se queda en el árbol importa este directorio de tests.

    Es el guardarraíl que sustituye al límite de profundidad, y la razón por la
    que aquel límite existía: la verificación RESTAURA la suite antes de correr,
    así que un import roto por habérsela llevado no lo vería nadie —el
    contenedor pasaría y el árbol que explora el agente estaría roto en
    silencio—. La pregunta que importa no es dónde está el directorio, sino si
    alguien de fuera depende de él; si el código fuente lo importa, ese
    directorio es parte del programa y se queda.

    Lo que se pregunta desde dentro de otra suite no cuenta: se va también, así
    que su import no puede quedar colgando.
    """
    target = ".".join(candidate.relative_to(root).parts)
    for path in iter_transformable_files(root):
        if any(suite in path.parents for suite in suites):
            continue
        for name in _imported_modules(path, root):
            if name == target or name.startswith(f"{target}."):
                return True
    return False


def kept_suite_path(root: Path) -> Path:
    """Dónde se guarda la suite: hermana del árbol, nunca dentro.

    Mismo criterio que el manifiesto (§5.4.1): dentro del árbol, el agente la
    encuentra con un `ls` y la celda no mide nada.
    """
    return root.parent / f"{root.name}.acp-tests"


def suite_paths(root: Path) -> list[Path]:
    """Lo que B4 se llevaría de este repo, antes de llevárselo.

    Es pública por la misma razón que las de B3: la dosis real cambia de repo en
    repo y quien escriba los resultados tiene que poder declararla sin deducirla
    de un contador de ficheros.
    """
    suites = _suite_dirs(root)
    # El guardarraíl del import solo se le pregunta a lo anidado. Un `tests/` de
    # primer nivel no es código del programa por convención —y es el que B4 ya
    # se llevaba de los tres repos del sustrato—, así que preguntarlo ahí solo
    # podría restar dosis ya medida.
    found = [
        path
        for path in suites
        if path.parent == root or not _the_program_imports_it(root, path, suites)
    ]
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
    """La ruta de la suite como argumento suelto, escrita como la escriba el repo.

    Lo que se quita es un elemento de la lista de colecta que apunta a lo que B4
    acaba de sacar del árbol, y ahí no cuenta la cadena exacta sino la ruta:
    `tests`, `"tests"`, `tests,`, `./tests`, `tests/` y `tests/unit`. Las tres
    últimas formas son las que hacían falta y no estaban. Un repo que separa
    unitarios de integración no nombra el directorio, nombra sus hijos —`addopts
    = "... pkg tests/unit"`— y ese hijo desaparece con el padre: pytest muere
    con `ERROR: file or directory not found: tests/unit` antes de colectar nada,
    o sea la condición quitando otra vez mucho más que su dosis. La barra final
    y el `./` no rompen la colecta pero dejan el árbol diciendo `testpaths =
    ["tests/"]`, que es la otra mitad del arreglo: el árbol tiene que parecer el
    de un repo que nunca tuvo suite.

    Lo que NO se quita, y por eso los dos lookarounds: `tests-slow` —que no es
    la suite, lo dice `is_test_dir`, y por eso B4 no se lo lleva: lo que sigue en
    el árbol tiene que seguir nombrado—, `docs/tests` (otro sitio, que el nombre
    de la suite viene relativo a la raíz), y `--cov=tests` o `--ignore=tests`
    (opciones con valor, que apuntar a algo que ya no existe no rompe nada y
    reescribirlas sería quitarle al repo una decisión suya). Ojo con el ejemplo
    fácil: `tests_helpers` SÍ se quita, porque `is_test_dir` lo cuenta como suite
    y entonces B4 se lo ha llevado.

    El subcamino se corta en lo que separa un argumento de otro —espacio,
    comilla, coma, corchete—: dentro de la lista de colecta lo que sigue a la
    ruta es siempre otro elemento, y comerse el de al lado sería reescribir la
    configuración en vez de quitarle un elemento.
    """
    return re.compile(
        rf"""(?<![\w./=:-])(["']?)(?:\./)?{re.escape(name)}(?:/[^\s"',\]]*)?\1(?![\w./-])[ \t]*,?[ \t]*"""
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


def _drop_the_pointers_to_the_suite(root: Path) -> None:
    """Los artefactos que nombran los tests que acaban de salir del árbol.

    Deliberadamente un segundo guardarraíl, igual que el bytecode rancio de B2
    (9b5ddcf): `copy_tree` filtra estos mismos artefactos en la única entrada que
    el pipeline tiene **hoy**, pero `apply` recibe un árbol sin saber quién lo
    preparó ni qué se ha corrido dentro desde entonces —`run_suite_in_venv`
    ejecuta pytest y pip con cwd en el repo—. Y aquí lo que sobrevive no rompe
    nada: un `nodeids` con `tests/test_core.py::test_f` dentro de un árbol sin
    `tests/` deja la celda en verde con la condición sin aplicar del todo, que es
    el peor modo de fallo del experimento porque se lee como éxito.

    Borrarlos es además lo correcto de por sí: los dos describen un árbol que ya
    no existe y los dos se regeneran solos en la siguiente corrida.
    """
    for path in sorted(root.rglob("*"), reverse=True):
        if not path.is_dir() or path.is_symlink():
            continue
        if any(fnmatch(path.name, pattern) for pattern in POINTERS_TO_THE_SUITE):
            shutil.rmtree(path)


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
        kept = destination / path.relative_to(root)
        # La suite puede estar dentro del paquete (`pint/testsuite/`), y ahí el
        # directorio intermedio no existe todavía en lo guardado: sin crearlo,
        # el movimiento falla y la condición se queda a medias.
        kept.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(kept))
        changed += 1
    # Después de mover: lo que se guarda es la configuración tal y como estaba
    # cuando la suite todavía existía.
    changed += _stop_naming_the_suite(root, found, destination)
    # Y después de todo: lo que queda en el árbol no puede seguir nombrando lo
    # que se acaba de esconder. No cuenta como fichero cambiado —no es dosis,
    # es que la dosis sea la declarada—, igual que el bytecode en B2.
    _drop_the_pointers_to_the_suite(root)
    return TransformResult(files_changed=changed)
