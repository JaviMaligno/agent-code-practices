"""Comparar una condición con su base, con todo lo que hace falta para escribirlo.

Vive aparte de `report.py`, que rinde los perfiles de admisión de repositorios:
son dos informes distintos y mezclarlos ya costó un módulo sobrescrito.

El artículo se escribió leyendo porcentajes, y un porcentaje suelto no permite
redactar una frase honesta sobre 18 celdas. Aquí cada contraste sale con su
intervalo, su p, y —cuando no hay diferencia— la caída que sí se habría visto con
ese número de celdas.

Ese último número es el que faltaba. Sin él, "no se detectó efecto" se lee como
"no hay efecto", y son cosas distintas: el desglose de esta campaña no podía ver
nada por debajo de una caída de 38 puntos, así que sus dieciséis condiciones
planas no dicen que las prácticas den igual — dicen que el experimento estaba mal
dimensionado.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from acp.stats import detectable_difference, fisher_exact, sample_size_for, wilson

# El presupuesto de turnos del experimento. Una corrida que lo toca no es un
# agente que se rinde: es uno al que se le acabó el dinero, y por eso se cuenta
# aparte de la resolución.
TECHO_DE_TURNOS = 40


@dataclass(frozen=True)
class Contraste:
    """Una condición frente a su base, con lo necesario para describirla."""

    condicion: str
    k: int
    n: int
    base_k: int
    base_n: int
    p: float
    intervalo: tuple[float, float]
    base_intervalo: tuple[float, float]
    turnos: float
    base_turnos: float
    techo: int
    base_techo: int
    p_techo: float
    detectable: float

    @property
    def tasa(self) -> float:
        return self.k / self.n if self.n else 0.0

    @property
    def base_tasa(self) -> float:
        return self.base_k / self.base_n if self.base_n else 0.0

    @property
    def significativo(self) -> bool:
        return self.p < 0.05

    @property
    def significativo_en_techo(self) -> bool:
        """Si lo que cambia es cuántas corridas agotan el presupuesto.

        Se mira aparte porque la mediana de turnos satura: cuando una condición
        empuja a la mayoría contra el techo, su mediana se queda clavada en 40 y
        deja de reflejar el daño. La proporción que llega al techo sí lo hace.
        """
        return self.p_techo < 0.05

    def hacen_falta(self) -> int:
        """Celdas por condición que harían falta para ver la diferencia observada."""
        if self.tasa == self.base_tasa:
            return 0
        return sample_size_for(self.base_tasa, self.tasa)


def comparar(filas: list[dict], base: str, condicion: str) -> Contraste:
    """El contraste entre dos condiciones del mismo registro."""
    b = _medibles(filas, base)
    c = _medibles(filas, condicion)

    bk, bn = sum(r["solved"] for r in b), len(b)
    k, n = sum(r["solved"] for r in c), len(c)
    bt = [r.get("turns") or 0 for r in b]
    t = [r.get("turns") or 0 for r in c]
    b_techo = sum(1 for x in bt if x >= TECHO_DE_TURNOS)
    techo = sum(1 for x in t if x >= TECHO_DE_TURNOS)

    return Contraste(
        condicion=condicion,
        k=k, n=n, base_k=bk, base_n=bn,
        p=fisher_exact(k, n - k, bk, bn - bk),
        intervalo=wilson(k, n),
        base_intervalo=wilson(bk, bn),
        turnos=median(t) if t else 0.0,
        base_turnos=median(bt) if bt else 0.0,
        techo=techo, base_techo=b_techo,
        p_techo=fisher_exact(techo, n - techo, b_techo, bn - b_techo),
        detectable=detectable_difference(min(n, bn) or 1, bk / bn if bn else 0.5),
    )


def _medibles(filas: list[dict], condicion: str) -> list[dict]:
    return [r for r in filas if r["condition"] == condicion and r["measurable"]]
