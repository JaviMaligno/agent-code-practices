"""Las tablas del artículo, generadas desde los registros.

Existe porque el artículo llegó a publicar un "4/12" que nunca ocurrió — la
cifra real era 2 de 4 — y porque una tabla decía "las dieciséis condiciones al
100%" cuando eran trece. Ninguna de las dos habría sobrevivido a que las tablas
salieran de los datos en vez de mi memoria.
"""

from acp.tables import contrast_row, dead_zone


def filas(cond, k, n, turnos=10, techo=0):
    """n celdas de `cond`, k resueltas, `techo` de ellas contra el límite."""
    salida = []
    for i in range(n):
        t = 40 if i < techo else turnos
        salida.append({"condition": cond, "measurable": True, "solved": i < k, "turns": t})
    return salida


def test_a_row_carries_the_interval_not_just_the_percentage():
    """Un porcentaje sin intervalo invita a leer como distintas dos condiciones
    que no lo son, que es lo que hizo la tabla del desglose."""
    datos = filas("T0", 14, 18) + filas("X", 12, 18)

    fila = contrast_row(datos, base="T0", condicion="X", nombre="cohesión")

    assert "12/18" in fila and "67%" in fila
    assert "%" in fila.split("|")[3], "debe haber una columna de intervalo"


def test_a_flat_row_says_what_would_have_shown():
    """Cuando no hay diferencia, la fila tiene que decir qué caída sí se habría
    visto. Sin eso, la tabla se lee como 'da igual' cuando dice 'no lo sabemos'."""
    datos = filas("T0", 14, 18) + filas("X", 12, 18)

    fila = contrast_row(datos, base="T0", condicion="X", nombre="cohesión")

    assert "≥" in fila, "una fila sin efecto declara el mínimo detectable"


def test_a_dead_zone_is_labelled_by_which_end_it_is_stuck_at():
    """Techo y suelo no son el mismo problema: uno es un modelo que resuelve
    todo, el otro tareas que nadie resuelve. El artículo los mezclaba en un
    'no interpretable' que no explicaba nada."""
    assert dead_zone(18, 18) == "techo"
    assert dead_zone(1, 12) == "suelo"
    assert dead_zone(9, 18) is None
