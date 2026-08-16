from __future__ import annotations

from dataclasses import dataclass, field

from acp.models import SuiteMetrics

# El tiempo queda fuera a propósito: varía entre corridas de la misma máquina.
COMPARED = ("ran", "passed", "failed", "errors", "skipped")


@dataclass
class EquivalenceReport:
    equivalent: bool
    differences: list[str] = field(default_factory=list)


def compare(before: SuiteMetrics, after: SuiteMetrics) -> EquivalenceReport:
    """La suite del árbol transformado tiene que dar el mismo resultado (§3.6.3).

    Una transformación que rompe el repo produce exactamente la misma señal que
    un agente incapaz de arreglarlo, y es el error más caro de descubrir tarde.
    """
    differences = [
        f"{field_name}: {getattr(before, field_name)} -> {getattr(after, field_name)}"
        for field_name in COMPARED
        if getattr(before, field_name) != getattr(after, field_name)
    ]
    # Dos corridas vacías son idénticas, y comparar la nada con la nada saldría
    # verde. Es la peor forma de fallar de esta función: cuando Docker se cae o
    # la instalación revienta, las dos versiones dan cero y el visto bueno
    # dejaría pasar un bloque entero corrido sobre un repo roto.
    if not _observed_anything(before):
        differences.insert(0, _why_nothing_was_observed(before))
    return EquivalenceReport(equivalent=not differences, differences=differences)


def _observed_anything(reference: SuiteMetrics) -> bool:
    """Si la referencia no ejecutó ni un test, no hay nada que preservar."""
    return reference.ran and (reference.passed + reference.failed + reference.errors) > 0


def _why_nothing_was_observed(reference: SuiteMetrics) -> str:
    if not reference.ran:
        return "la suite de referencia no corrió: la comparación no prueba nada"
    return "la suite de referencia no ejecutó ningún test: la comparación no prueba nada"
