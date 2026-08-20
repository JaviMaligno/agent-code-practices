"""De una mutación del catálogo a una tarea con su parche.

`mutate` devuelve el fuente entero mutado. Una tarea guarda un PARCHE, y la
diferencia no es de formato: el parche es lo que se aplica para crear la
condición y lo que se revierte para demostrar que arreglar el fallo devuelve la
suite a verde (§5.4.6). Un fuente entero no se puede revertir sobre un árbol que
la transformación ya movió de sitio.

Dos decisiones que este módulo toma y conviene tener escritas:

**Inyectar no escribe en el árbol.** La tarea es un parche; quién lo aplica y
dónde lo decide la condición. Escribir aquí obligaría a restaurar el clon entre
tarea y tarea, y un olvido convertiría la tarea siguiente en la anterior más la
suya —dos fallos, medidos como uno—.

**`fail_to_pass` se DECLARA antes de correr, no se observa después.** Es la mitad
que hace no vacía a la validación de §3.3: si el conjunto saliera de mirar qué se
puso rojo, `fail_to_pass_ok` sería cierto por construcción y la tarea solo podría
fallar por exceso. En un repo cuyos tests viven en los docstrings, lo que un
símbolo declara como su prueba está escrito a su lado, y eso es una declaración.
Cuando se queda corta, la validación lo dice —y el generador puede volver a
declarar la tarea con `observed_failures`, respaldado por esa misma corrida—.
"""

from __future__ import annotations

import ast
import difflib
import re
from pathlib import Path
from typing import Sequence

from acp.tasks.models import Task
from acp.tasks.mutations import mutate

# Cabecera de hunk de un diff unificado: `@@ -12,7 +12,7 @@`.
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def module_path(repo: Path, module: str) -> Path:
    """El fichero donde vive un módulo, buscado donde Python lo buscaría.

    Un módulo que no está es un fallo del generador y suena aquí: si se dejara
    pasar, la tarea se generaría contra otro fichero o contra ninguno y el error
    saldría dos corridas de Docker más tarde.
    """
    repo = Path(repo)
    parts = module.split(".")
    for raiz in (repo, repo / "src"):
        candidatos = (raiz.joinpath(*parts).with_suffix(".py"), raiz.joinpath(*parts, "__init__.py"))
        for candidato in candidatos:
            if candidato.is_file():
                return candidato
    raise LookupError(f"el módulo {module!r} no está en {repo}")


def _docstring_of(source: str, symbol: str) -> str | None:
    """El docstring del símbolo, o del módulo si `symbol` es None.

    El símbolo se nombra igual que en el catálogo: ruta desde el módulo,
    `funcion` o `Clase.metodo`.
    """
    tree = ast.parse(source)
    if symbol is None:
        return ast.get_docstring(tree)
    nodo: ast.AST = tree
    for nombre in symbol.split("."):
        for hijo in ast.iter_child_nodes(nodo):
            if (
                isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and hijo.name == nombre
            ):
                nodo = hijo
                break
        else:
            return None
    return ast.get_docstring(nodo)  # type: ignore[arg-type]


def declared_tests(source: str, module: str, relative: str, symbol: str) -> list[str]:
    """Los tests que el símbolo declara, en forma de nodeid de pytest.

    Primero el ejemplo del propio símbolo, que es el más estrecho; si no lo
    tiene, el del módulo, que es lo que ejercita el símbolo desde fuera. Si no
    hay ninguno, no se puede declarar la tarea desde el fuente y hay que pasarle
    el conjunto a mano: sin tests que distingan arreglado de roto no hay medida
    (§3.2.1) y callarlo fabricaría una tarea que se cuenta resuelta siempre.
    """
    propio = _docstring_of(source, symbol)
    if propio and ">>>" in propio:
        return [f"{relative}::{module}.{symbol}"]
    del_modulo = _docstring_of(source, None)
    if del_modulo and ">>>" in del_modulo:
        return [f"{relative}::{module}"]
    raise ValueError(
        f"{module}.{symbol} no declara ningún ejemplo: pasa `fail_to_pass` a mano"
    )


def apply_patch(source: str, patch: str, *, reverse: bool = False) -> str:
    """El fuente con el parche aplicado, o revertido.

    Se aplica a mano en vez de llamar a `git apply` o a `patch` por dos razones:
    el contenedor de la campaña no garantiza ninguno de los dos, y sobre todo,
    aquí un contexto que no cuadra tiene que ser RUIDOSO. `patch` busca el hueco
    unas líneas más arriba o más abajo y lo aplica de todas formas; el oráculo
    necesita lo contrario —enterarse de que el símbolo no está donde el
    manifiesto dice— porque eso significa que la condición no es medible
    (§5.4.6), y es mejor saberlo aquí que a mitad de campaña.
    """
    lineas = source.splitlines(keepends=True)
    salida: list[str] = []
    posicion = 0
    for cabecera, cuerpo in _hunks(patch):
        # Al revertir, el hunk se sitúa por su lado NUEVO: el fuente de entrada
        # es el que el parche produjo.
        inicio = (cabecera[2] if reverse else cabecera[0]) - 1
        if inicio < posicion or inicio > len(lineas):
            raise ValueError(f"el hunk empieza en la línea {inicio + 1}, fuera del fuente")
        salida.extend(lineas[posicion:inicio])
        posicion = inicio
        for linea in cuerpo:
            marca, texto = linea[0], linea[1:]
            if reverse:
                marca = {"+": "-", "-": "+"}.get(marca, marca)
            if marca in " -":
                if posicion >= len(lineas) or lineas[posicion] != texto:
                    encontrado = lineas[posicion] if posicion < len(lineas) else "<fin>"
                    raise ValueError(
                        f"el parche esperaba {texto!r} en la línea {posicion + 1} "
                        f"y encontró {encontrado!r}"
                    )
                posicion += 1
                if marca == " ":
                    salida.append(texto)
            else:
                salida.append(texto)
    salida.extend(lineas[posicion:])
    return "".join(salida)


def _hunks(patch: str) -> list[tuple[tuple[int, int, int, int], list[str]]]:
    """Los hunks del diff, con su cabecera ya en números."""
    trozos: list[tuple[tuple[int, int, int, int], list[str]]] = []
    for linea in patch.splitlines(keepends=True):
        cabecera = _HUNK.match(linea)
        if cabecera:
            numeros = (
                int(cabecera.group(1)), int(cabecera.group(2) or 1),
                int(cabecera.group(3)), int(cabecera.group(4) or 1),
            )
            trozos.append((numeros, []))
            continue
        if not trozos:
            # Las cabeceras `---` / `+++` van antes del primer hunk.
            continue
        if linea.startswith((" ", "+", "-")):
            trozos[-1][1].append(linea)
    return trozos


def inject(
    repo: Path,
    module: str,
    symbol: str,
    kind: str,
    *,
    fail_to_pass: Sequence[str] | None = None,
    pass_to_pass: Sequence[str] | None = None,
    stratum: str = "generic",
    task_id: str | None = None,
    min_files_to_judge: int = 1,
) -> Task:
    """La tarea que resulta de romper `symbol` con la forma `kind`.

    No escribe en el árbol: devuelve el parche. Y recoge `fail_to_pass` ANTES de
    construir la `Task`, porque la `Task` valida al construir y una lista vacía
    aborta ahí —lo que se quiere: una tarea sin tests que romper no es tarea—.
    """
    repo = Path(repo)
    ruta = module_path(repo, module)
    relativa = ruta.relative_to(repo).as_posix()
    original = ruta.read_text(encoding="utf-8")

    mutado = mutate(original, symbol, kind)
    if mutado is None:
        raise ValueError(f"la forma {kind!r} no aplica a {module}.{symbol}")

    patch = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            mutado.splitlines(keepends=True),
            fromfile=f"a/{relativa}",
            tofile=f"b/{relativa}",
        )
    )

    declarados = (
        list(fail_to_pass)
        if fail_to_pass is not None
        else declared_tests(original, module, relativa, symbol)
    )

    return Task(
        # Determinista y legible: la misma inyección tiene que dar la misma
        # tarea dos meses después, o el parche guardado en JSON deja de poder
        # regenerarse y el oráculo pierde su referencia.
        task_id=task_id or f"{repo.name}-{module}.{symbol}-{kind}",
        repo=repo.name,
        module=module,
        symbol=symbol,
        stratum=stratum,
        patch=patch,
        fail_to_pass=declarados,
        pass_to_pass=list(pass_to_pass or []),
        min_files_to_judge=min_files_to_judge,
    )
