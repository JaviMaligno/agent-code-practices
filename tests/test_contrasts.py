"""El contraste que el artículo debería haber tenido delante al escribirse.

Un porcentaje suelto no permite escribir una frase honesta: hace falta el
intervalo, la comparación contra la base y, cuando no hay diferencia, qué caída
habría hecho falta para verla. Sin ese último número, "no se detectó efecto" y
"no hay efecto" se confunden, que es exactamente lo que pasó con el desglose.
"""

from acp.contrasts import Contraste, comparar


def test_a_contrast_carries_what_a_sentence_needs():
    filas = [
        {"condition": "T0", "measurable": True, "solved": True, "turns": 10},
        {"condition": "T0", "measurable": True, "solved": False, "turns": 40},
        {"condition": "A", "measurable": True, "solved": False, "turns": 40},
        {"condition": "A", "measurable": True, "solved": False, "turns": 40},
    ]

    c = comparar(filas, base="T0", condicion="A")

    assert isinstance(c, Contraste)
    assert (c.k, c.n) == (0, 2)
    assert c.base_k == 1 and c.base_n == 2
    assert 0 <= c.p <= 1
    assert c.techo == 2 and c.base_techo == 1


def test_it_says_what_would_have_been_visible_when_nothing_is():
    """Lo que faltaba: si el contraste no es significativo, cuánta caída habría
    hecho falta con ese número de celdas. Es la diferencia entre "no hay efecto"
    y "este experimento no podía verlo"."""
    filas = (
        [{"condition": "T0", "measurable": True, "solved": i < 14, "turns": 10} for i in range(18)]
        + [{"condition": "X", "measurable": True, "solved": i < 12, "turns": 10} for i in range(18)]
    )

    c = comparar(filas, base="T0", condicion="X")

    assert not c.significativo
    assert c.detectable > 0.25, "con 18 celdas solo se ven caídas grandes"


def test_unmeasurable_cells_never_reach_the_arithmetic():
    """Una celda cuyo fallo no rompe ningún test no dice nada del agente, y
    contarla como fracaso movería la tasa sin que nadie lo note."""
    filas = [
        {"condition": "T0", "measurable": True, "solved": True, "turns": 5},
        {"condition": "X", "measurable": False, "solved": False, "turns": 0},
        {"condition": "X", "measurable": True, "solved": True, "turns": 5},
    ]

    c = comparar(filas, base="T0", condicion="X")

    assert c.n == 1
