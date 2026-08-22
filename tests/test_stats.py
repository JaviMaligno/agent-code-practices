"""Si una diferencia se distingue del ruido, y cuántas celdas harían falta.

Esto faltaba, y su ausencia es la razón de que la campaña publicara frases como
"entre 67% y 89%, alrededor de una base del 78%", que no dicen nada: de 0% a 100%
también está alrededor del 78%. Sin intervalo ni potencia, un porcentaje sobre 18
celdas no permite afirmar ni negar.
"""

import pytest

from acp.stats import fisher_exact, sample_size_for, wilson


def test_the_interval_is_wide_when_there_are_few_cells():
    """14 de 18 es un 78%, y el intervalo llega del 55% al 91%. Publicar solo el
    78% invita a leer como distintas dos condiciones que caen dentro."""
    lo, hi = wilson(14, 18)

    assert 0.54 < lo < 0.56
    assert 0.90 < hi < 0.92


def test_a_difference_inside_the_interval_is_not_a_difference():
    """T1 salió por encima del baseline (88% contra 78%). Con estos tamaños eso
    es indistinguible de que no pase nada, y contarlo como mejora sería el mismo
    error que contar una bajada como daño."""
    p = fisher_exact(15, 2, 14, 4)

    assert p > 0.05


def test_the_one_effect_that_survives():
    """pint bajo la familia A: 2 de 12 contra 8 de 12. Es el único contraste de
    la campaña que se separa del ruido, y por eso es el que sostiene el
    artículo."""
    p = fisher_exact(2, 10, 8, 4)

    assert p < 0.05


def test_it_says_how_many_cells_a_difference_would_need():
    """La pregunta que evita correr un experimento condenado: para ver los 11
    puntos que enseña el desglose harían falta cientos de celdas por condición,
    no 18."""
    assert sample_size_for(0.78, 0.67) > 200
    assert sample_size_for(0.67, 0.17) < 20


@pytest.mark.parametrize("k,n", [(0, 10), (10, 10), (0, 0)])
def test_the_extremes_do_not_explode(k, n):
    """0 de 10 y 10 de 10 son justo los casos de las zonas muertas, así que el
    intervalo tiene que existir ahí en vez de dividir por cero."""
    lo, hi = wilson(k, n)

    assert 0.0 <= lo <= hi <= 1.0
