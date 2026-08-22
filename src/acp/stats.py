"""Si una diferencia se distingue del ruido, y cuántas celdas harían falta.

Este módulo llega tarde, y su ausencia explica el peor defecto de la campaña: se
publicaron porcentajes sobre 18 celdas sin decir nunca si eran distinguibles
entre sí. "Entre 67% y 89%, alrededor de una base del 78%" no afirma nada — de 0%
a 100% también está alrededor del 78% —, y con esos tamaños media docena de
lecturas del experimento eran compatibles con los mismos datos.

Sin dependencias externas a propósito: son tres fórmulas cerradas, y el
experimento ya tiene bastantes piezas móviles.
"""

from __future__ import annotations

from math import comb, sqrt

# Los valores críticos habituales, escritos una vez. z al 95% para el intervalo;
# el segundo es el z de una potencia del 80%, que es el punto donde se suele
# considerar que un experimento merece la pena correrse.
Z_95 = 1.959964
Z_POTENCIA_80 = 0.841621


def wilson(k: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Intervalo de confianza para una proporción, por el método de Wilson.

    Wilson y no la aproximación normal porque con 18 celdas y tasas cercanas al
    100% la normal se sale del intervalo [0, 1] y da límites imposibles — y las
    zonas muertas de esta campaña son justamente 0/12 y 18/18.
    """
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denominador = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / denominador
    margen = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominador
    return (max(0.0, centro - margen), min(1.0, centro + margen))


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """p a dos colas de la tabla [[a, b], [c, d]], por el test exacto de Fisher.

    Exacto y no chi-cuadrado porque las celdas de esta campaña son de 12 a 18
    corridas, y ahí la aproximación asintótica no vale.
    """
    n = a + b + c + d
    if n == 0 or (a + b) == 0 or (c + d) == 0:
        return 1.0

    def probabilidad(x: int) -> float:
        return comb(a + b, x) * comb(c + d, a + c - x) / comb(n, a + c)

    observada = probabilidad(a)
    minimo = max(0, a + c - (c + d))
    maximo = min(a + b, a + c)
    # El margen relativo evita que la propia tabla observada se quede fuera de la
    # suma por un error de redondeo en coma flotante.
    return min(1.0, sum(
        probabilidad(x) for x in range(minimo, maximo + 1)
        if probabilidad(x) <= observada * (1 + 1e-9)
    ))


def sample_size_for(
    p1: float, p2: float, potencia: float = 0.80, alfa: float = 0.05
) -> int:
    """Celdas por condición para poder ver una diferencia entre `p1` y `p2`.

    Es la pregunta que había que hacerse **antes** de correr el desglose: para
    distinguir 78% de 67% hacen falta cientos de corridas por condición, así que
    dieciséis condiciones de dieciocho celdas no podían concluir nada. Correrlo
    igual no fue caro en dinero, pero produjo una tabla que invitaba a leer
    diferencias donde no las había.
    """
    if p1 == p2:
        raise ValueError("no hay diferencia que detectar: p1 y p2 son iguales")
    z_alfa = Z_95 if abs(alfa - 0.05) < 1e-9 else _z(1 - alfa / 2)
    z_beta = Z_POTENCIA_80 if abs(potencia - 0.80) < 1e-9 else _z(potencia)
    media = (p1 + p2) / 2
    numerador = (
        z_alfa * sqrt(2 * media * (1 - media))
        + z_beta * sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    ) ** 2
    from math import ceil

    return ceil(numerador / (p1 - p2) ** 2)


def detectable_difference(n: int, p_base: float = 0.78) -> float:
    """La caída más pequeña que `n` celdas por condición permiten ver.

    El complemento útil de `sample_size_for`: dado lo que uno puede permitirse
    correr, dice qué efectos quedan fuera de alcance, que es lo que hay que
    declarar cuando un bloque sale sin diferencias.
    """
    if n <= 0:
        return 1.0
    return min(1.0, (Z_95 + Z_POTENCIA_80) * sqrt(2 * p_base * (1 - p_base) / n))


def _z(p: float) -> float:
    """Cuantil de la normal estándar, por bisección sobre la función de error."""
    from math import erf

    bajo, alto = -10.0, 10.0
    for _ in range(200):
        medio = (bajo + alto) / 2
        if 0.5 * (1 + erf(medio / sqrt(2))) < p:
            bajo = medio
        else:
            alto = medio
    return (bajo + alto) / 2
