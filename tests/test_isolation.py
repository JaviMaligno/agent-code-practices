"""El filtro que decide si un fallo es de dominio (§3.6.2b).

Se prueba con el juicio del modelo sustituido, porque lo que hay que fijar aquí
es la **regla de decisión**: cuántos votos hacen mayoría, qué pasa cuando el
modelo señala también la original, y —lo que motivó esto— qué pasa cuando dos
modelos discrepan sobre la misma función.
"""

from acp.tasks.isolation import judge_across_models


def test_a_task_is_domain_only_if_no_model_in_the_campaign_spots_it(monkeypatch):
    """El estrato no puede depender de con qué modelo se filtró, y dependía.

    Medido sobre el convertidor logarítmico de pint: gpt-5.4-mini señala la
    mutación 2 de 3 veces y la original ninguna —detección real, no ruido—
    mientras gpt-5.4 no ve nada en ninguna de las dos. Con el filtro sobre un
    solo modelo, la misma función es de dominio o no según a quién preguntes.

    Así que se pregunta a todos los modelos de la campaña y basta que uno la vea
    para que no entre: si algún agente del experimento reconoce el fallo leyendo
    la función suelta, para ese agente no es un fallo de dominio y su celda
    mediría otra cosa. La regla es conservadora a propósito — el estrato de
    dominio es la mitad del diseño que más fácil es contaminar.
    """
    guion = {"listo": 2, "torpe": 0}

    def flags_falso(fuente, model, votes):
        return guion[model] if "MUTADA" in fuente else 0

    monkeypatch.setattr("acp.tasks.isolation._flags", flags_falso)

    veredicto = judge_across_models("ORIGINAL", "MUTADA", ["listo", "torpe"], votes=3)

    assert veredicto.detected is True
    assert veredicto.detected_by == ["listo"]


def test_it_is_domain_when_every_model_stays_blind(monkeypatch):
    monkeypatch.setattr("acp.tasks.isolation._flags", lambda fuente, model, votes: 0)

    veredicto = judge_across_models("ORIGINAL", "MUTADA", ["listo", "torpe"], votes=3)

    assert veredicto.detected is False
    assert veredicto.detected_by == []


def test_the_per_model_verdicts_are_kept_not_just_the_conclusion(monkeypatch):
    """Quien lea el conjunto de datos tiene que poder ver qué modelo vio qué: la
    conclusión sola esconde justo la discrepancia que motivó este cambio."""

    def flags_falso(fuente, model, votes):
        if "MUTADA" not in fuente:
            return 0
        return 3 if model == "listo" else 0

    monkeypatch.setattr("acp.tasks.isolation._flags", flags_falso)

    veredicto = judge_across_models("ORIGINAL", "MUTADA", ["listo", "torpe"], votes=3)

    assert veredicto.per_model["listo"].positives_mutated == 3
    assert veredicto.per_model["torpe"].positives_mutated == 0


def test_a_model_that_flags_both_versions_does_not_count_as_detecting(monkeypatch):
    """El control emparejado sigue vigente por modelo. Señalar las dos no es
    detectar la mutación: es no entender la función, y ya está medido que con el
    prompt directo los dos tiers dicen que sí al 100% de las funciones."""
    monkeypatch.setattr("acp.tasks.isolation._flags", lambda fuente, model, votes: 3)

    veredicto = judge_across_models("ORIGINAL", "MUTADA", ["listo"], votes=3)

    assert veredicto.detected is False
    assert veredicto.per_model["listo"].inconclusive is True
