"""El agente que intenta arreglar una tarea, y la traza de lo que hizo.

§5.1: el harness es propio porque un agente comercial es caja negra, cambia
entre versiones y no instrumenta lo que hace falta medir. Lo que hace falta medir
es **qué regiones de código llegó a ver y en qué orden** (§5.4.2): esa es la
hipótesis medida directamente, en vez de inferida del resultado.

§5.3: el presupuesto es fijo e idéntico en todas las condiciones. Sin techo, la
mala organización solo se paga en coste —el agente acaba encontrando el sitio a
base de abrir ficheros— y el resolve rate no se movería, con lo que el
experimento concluiría que la organización da igual.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from acp.agent.tools import Toolbox
from acp.model import ModelError, converse

MAX_TURNS = 40
SISTEMA = """\
Eres un programador trabajando en un repositorio existente. Hay un fallo: un test \
falla. Tu trabajo es encontrarlo y arreglarlo.

Usa las herramientas para explorar el repositorio y editar el código. Cuando creas \
que está arreglado, ejecuta los tests para comprobarlo.

No reescribas el test para que pase: arregla el código.
"""


@dataclass
class Trace:
    """Lo que pasó, con lo que hace falta para las métricas de proceso (§7)."""

    turns: int = 0
    tool_calls: list = field(default_factory=list)
    # Rangos que el agente tuvo delante, en orden. La localización se proyecta
    # sobre esto y sobre el mapa de identidad de símbolos.
    seen: list[tuple[str, int, int]] = field(default_factory=list)
    first_edit_turn: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    stopped_because: str = ""


def solve(
    session,
    prompt: str,
    model: str,
    *,
    grep: bool = True,
    max_turns: int = MAX_TURNS,
) -> Trace:
    """Deja al agente trabajar sobre el árbol hasta agotar su presupuesto."""
    caja = Toolbox(session, grep=grep)
    trace = Trace()
    mensajes: list[dict] = [
        {"role": "system", "content": SISTEMA},
        {"role": "user", "content": prompt},
    ]

    for turno in range(1, max_turns + 1):
        trace.turns = turno
        try:
            respuesta = converse(mensajes, model=model, tools=caja.schema())
        except ModelError as error:
            # Un fallo de la pasarela no es un fallo del agente (§5.4.5): se
            # marca aparte para poder contarlo y descartarlo del resolve rate.
            trace.stopped_because = f"infraestructura: {error}"
            return trace

        uso = respuesta.get("usage") or {}
        trace.prompt_tokens += uso.get("prompt_tokens", 0)
        trace.completion_tokens += uso.get("completion_tokens", 0)

        mensaje = respuesta["message"]
        mensajes.append(mensaje)
        llamadas = mensaje.get("tool_calls") or []
        if not llamadas:
            trace.stopped_because = "el agente dejó de pedir herramientas"
            return trace

        for llamada in llamadas:
            nombre = llamada["function"]["name"]
            try:
                argumentos = json.loads(llamada["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                argumentos = {}
            salida = caja.call(nombre, argumentos)

            ultima = caja.calls[-1]
            for fichero, rangos in ultima.seen.items():
                for inicio, fin in rangos:
                    trace.seen.append((fichero, inicio, fin))
            if nombre == "edit_file" and trace.first_edit_turn is None:
                trace.first_edit_turn = turno

            mensajes.append({
                "role": "tool",
                "tool_call_id": llamada["id"],
                "content": salida,
            })

    trace.stopped_because = "presupuesto de turnos agotado"
    trace.tool_calls = caja.calls
    return trace
