"""El filtro que separa un fallo de dominio de uno genérico disfrazado.

§3.6.2b: a cada tarea de dominio se le pasa al modelo **solo la función
modificada, fuera de contexto**, y se le pregunta si contiene un fallo. Si lo
detecta, no es de dominio: su forma bastaba para reconocerlo (§3.3.1).

Pero preguntar solo por la función mutada no sirve, y está medido. Con el prompt
directo —"¿contiene un fallo?"— los dos modelos dicen que sí al 100% de las
funciones, incluidas las correctas. Con el prompt conservador dicen que no
siempre, incluidas las rotas. Y con un prompt neutro que discrimina (4 de 6 en
un control de seis casos), el error más llamativo es que **marcan como
defectuoso el código CORRECTO** de `stdnum.mx.curp.get_gender`: en el CURP la
entrada está en español (H de Hombre) y la salida en convención inglesa (M de
Male), así que el mapeo bueno parece un fallo y el fallo parece bueno.

De ahí el control pareado: se pregunta por la función ORIGINAL y por la MUTADA.
Solo cuenta como detectado si el modelo ve el fallo en la mutada Y NO lo ve en la
original. Si lo ve en las dos, no está detectando la mutación: está diciendo que
no entiende la función, y su veredicto sobre esa tarea no vale para clasificarla.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from acp.model import ask

SISTEMA = "Respondes con una sola letra."
PROMPT = """\
Clasifica esta función en una de dos categorías:
  A = el código es coherente consigo mismo; para saber si hace lo correcto haría
      falta conocer la regla de negocio o ver otro fichero.
  B = se ve un error mirando solo esto: la lógica se contradice o el nombre dice
      lo contrario de lo que hace.
Responde una sola letra: A o B.

```python
{code}
```
"""


@dataclass
class IsolationVerdict:
    """Lo que el modelo vio mirando cada versión de la función, a solas."""

    detected: bool
    votes: int
    positives_mutated: int
    positives_original: int
    # El modelo señala fallo en las dos versiones: no está viendo la mutación,
    # está diciendo que la función le parece rara. Su voto no clasifica la tarea.
    inconclusive: bool = False


def _flags(code: str, model: str, votes: int) -> int:
    respuestas = [
        ask(PROMPT.format(code=code), model=model, system=SISTEMA, max_tokens=400)
        for _ in range(votes)
    ]
    return sum(1 for respuesta in respuestas if respuesta.strip().upper().startswith("B"))


def judge_in_isolation(
    original: str, mutated: str, model: str, *, votes: int = 3
) -> IsolationVerdict:
    """Si el modelo ve el fallo mirando la función mutada, y no antes.

    Se pregunta varias veces porque la respuesta decide la composición del
    estrato, y una sola muestra no distingue "lo ve" de "lo dijo esa vez".
    """
    en_mutada = _flags(mutated, model, votes)
    en_original = _flags(original, model, votes)
    mayoria = votes // 2 + 1
    inconclusive = en_mutada >= mayoria and en_original >= mayoria
    return IsolationVerdict(
        detected=en_mutada >= mayoria and en_original < mayoria,
        votes=votes,
        positives_mutated=en_mutada,
        positives_original=en_original,
        inconclusive=inconclusive,
    )


@dataclass
class CrossModelVerdict:
    """Si el fallo se ve en aislamiento, preguntado a todos los modelos.

    El veredicto por modelo se conserva y no solo la conclusión: la conclusión
    sola esconde la discrepancia, que es justo lo que hay que poder auditar.
    """

    detected: bool
    detected_by: list[str]
    per_model: dict[str, IsolationVerdict]


def judge_across_models(
    original: str, mutated: str, models: Sequence[str], *, votes: int = 3
) -> CrossModelVerdict:
    """El estrato de una tarea, definido contra todos los modelos de la campaña.

    Preguntar a uno solo hacía que el estrato dependiera de a quién se preguntó.
    Medido sobre el convertidor logarítmico de pint: `gpt-5.4-mini` señala la
    mutación 2 de 3 veces y la original ninguna —detección real— mientras
    `gpt-5.4` no ve nada en ninguna de las dos. La misma función era de dominio
    o genérica según el modelo del filtro, y eso no es una propiedad de la tarea.

    Basta que **un** modelo la vea para que no entre en dominio: si algún agente
    del experimento reconoce el fallo leyendo la función suelta, para ese agente
    no es un fallo de dominio y su celda mediría otra cosa. La regla es
    deliberadamente conservadora, porque el estrato de dominio es la mitad del
    diseño más fácil de contaminar y la más costosa de rehacer.
    """
    por_modelo = {
        modelo: judge_in_isolation(original, mutated, modelo, votes=votes)
        for modelo in models
    }
    vieron = [modelo for modelo, v in por_modelo.items() if v.detected]
    return CrossModelVerdict(
        detected=bool(vieron), detected_by=vieron, per_model=por_modelo
    )
