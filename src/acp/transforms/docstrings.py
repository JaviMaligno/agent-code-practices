"""Qué es una docstring y qué parte de ella se conserva.

Vive fuera de A4 y de B3 porque las dos la necesitan idéntica. A4 borra las
docstrings de función y de clase; B3 borra la de módulo. Son celdas distintas
del experimento y se comparan entre sí, así que si cada una decidiera por su
cuenta qué cuenta como docstring —o qué trozo sobrevive— la diferencia medida
entre las dos incluiría la diferencia entre sus dos reglas. Importar una desde
la otra habría atado B3 a A4 sin decir por qué; con la regla en su propio sitio,
tocarla se ve como lo que es: un cambio en las dos celdas a la vez.
"""

from __future__ import annotations

import libcst as cst


def docstring_literal(statement: cst.BaseStatement) -> cst.SimpleStatementLine | None:
    """La sentencia si es una docstring, o None.

    Se devuelve la sentencia entera y no la cadena porque quien llama necesita
    reemplazarla dentro del bloque conservando su posición.
    """
    if (
        isinstance(statement, cst.SimpleStatementLine)
        and len(statement.body) == 1
        and isinstance(statement.body[0], cst.Expr)
        and isinstance(statement.body[0].value, cst.SimpleString)
    ):
        return statement
    return None


DOCTEST_PROMPT = ">>>"


def _is_example_start(line: str) -> bool:
    """Si la línea abre un ejemplo de doctest.

    Misma regla de prompt que `a2_names._prompt`: `>>>x` no es un prompt porque
    doctest exige el espacio, o nada detrás. Allí sirve para saber qué trozo del
    ejemplo es código renombrable; aquí, para saber dónde empieza lo que no se
    puede borrar.
    """
    stripped = line.lstrip(" ")
    if not stripped.startswith(DOCTEST_PROMPT):
        return False
    rest = stripped[len(DOCTEST_PROMPT) :]
    return not rest or rest.startswith(" ")


def _example_blocks(lines: list[str]) -> list[list[str]]:
    """Los ejemplos del texto: el `>>>`, sus continuaciones y su salida esperada.

    Un ejemplo llega hasta la primera línea en blanco o hasta el final, que es
    exactamente donde doctest da por terminada la salida esperada. Por eso la
    prosa pegada bajo un resultado se conserva: para doctest no es prosa, es
    parte de lo que el ejemplo espera, y quitarla haría fallar el test.
    """
    blocks: list[list[str]] = []
    index = 0
    while index < len(lines):
        if not _is_example_start(lines[index]):
            index += 1
            continue
        block = []
        while index < len(lines) and lines[index].strip():
            block.append(lines[index])
            index += 1
        blocks.append(block)
    return blocks


def only_doctests(statement: cst.SimpleStatementLine) -> cst.SimpleString | None:
    """La docstring recortada a sus ejemplos, o None si no tenía ninguno.

    None significa «bórrala entera», que es el caso normal de A4. Cuando quedan
    ejemplos se reconstruye el literal con el mismo prefijo y las mismas
    comillas: la sangría original de cada línea viaja intacta porque doctest
    compara la salida esperada contra el margen del `>>>`.
    """
    original = statement.body[0].value  # type: ignore[union-attr]
    assert isinstance(original, cst.SimpleString)
    lines = original.raw_value.split("\n")
    blocks = _example_blocks(lines)
    if not blocks:
        return None
    # Un ejemplo pegado a las comillas de apertura no tiene sangría propia:
    # moverlo de línea cambiaría el margen contra el que doctest compara la
    # salida. Es rarísimo y no vale el riesgo, así que esa docstring se queda
    # como está.
    if _is_example_start(lines[0]):
        return original
    # La última línea en blanco es la sangría de las comillas de cierre.
    closing = lines[-1] if lines and not lines[-1].strip() else ""
    rebuilt = "\n" + "\n\n".join("\n".join(block) for block in blocks) + "\n" + closing
    if rebuilt == original.raw_value:
        return original
    return original.with_changes(
        value=f"{original.prefix}{original.quote}{rebuilt}{original.quote}"
    )
