"""Las tablas del artículo, generadas desde los registros.

Existe por dos erratas que llegaron a publicarse: un "4/12" que nunca ocurrió —la
cifra era 2 de 4— y un "las dieciséis condiciones al 100%" cuando eran trece.
Ninguna habría sobrevivido a que las tablas salieran de los datos.

Y una decisión de formato que es de fondo: cada fila lleva su intervalo, y una
fila sin diferencia significativa declara **qué caída sí se habría visto** con ese
número de celdas. Sin ese dato, una tabla plana se lee como "da igual" cuando lo
que dice es "no lo sabemos".
"""

from __future__ import annotations

from acp.contrasts import comparar

# Debajo de esto un bloque no puede medir una caída: o el árbol limpio ya falla
# casi siempre, o acierta siempre y no hay hueco por donde bajar.
SUELO = 0.15
TECHO = 0.95


def dead_zone(k: int, n: int) -> str | None:
    """Si la base está pegada a un extremo, y a cuál.

    Techo y suelo no son el mismo problema y el artículo los mezclaba en un
    "no interpretable" que no explicaba nada. En el techo el modelo resuelve todo
    y la degradación no tiene dónde notarse; en el suelo las tareas eran
    demasiado caras y la culpa es de quien las eligió.
    """
    if n == 0:
        return "suelo"
    tasa = k / n
    if tasa <= SUELO:
        return "suelo"
    if tasa >= TECHO:
        return "techo"
    return None


def contrast_row(filas: list[dict], base: str, condicion: str, nombre: str) -> str:
    """Una fila de tabla en markdown, con todo lo que permite leerla bien."""
    c = comparar(filas, base=base, condicion=condicion)
    lo, hi = c.intervalo
    if c.significativo:
        veredicto = f"**p={c.p:.3f}**"
    else:
        veredicto = f"n.s. (≥{c.detectable:.0%} sería visible)"
    return (
        f"| {nombre} | {c.k}/{c.n} — {c.tasa:.0%} | [{lo:.0%}, {hi:.0%}] | "
        f"{c.turnos:.0f} | {c.base_techo}→{c.techo} | {veredicto} |"
    )


def contrast_table(
    filas: list[dict], base: str, condiciones: list[tuple[str, str]]
) -> str:
    """La tabla entera: cabecera, base y una fila por condición.

    La columna del techo va como "antes→después" porque escrita en una sola
    cifra es ambigua —un lector no puede saber si el número es del árbol intacto
    o del degradado—, y es justo la columna donde vive el mecanismo.
    """
    c0 = comparar(filas, base=base, condicion=base)
    lo, hi = c0.intervalo
    cabecera = [
        "| Condición | Resuelve | IC 95% | Turnos | Techo antes→después | ¿Se distingue? |",
        "|---|---|---|---|---|---|",
        f"| sin tocar (base) | {c0.k}/{c0.n} — {c0.tasa:.0%} | [{lo:.0%}, {hi:.0%}] | "
        f"{c0.turnos:.0f} | {c0.techo} | — |",
    ]
    return "\n".join(
        cabecera + [contrast_row(filas, base, cond, nombre) for cond, nombre in condiciones]
    )
