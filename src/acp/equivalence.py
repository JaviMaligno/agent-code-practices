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
    return EquivalenceReport(equivalent=not differences, differences=differences)
