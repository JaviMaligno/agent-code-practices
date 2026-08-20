from acp.tasks.mutations import MUTATIONS, mutate

SOURCE = '''\
def clasificar(valor, limite):
    if valor > limite:
        return "alto"
    return "bajo"
'''


def test_the_catalogue_covers_the_forms_the_design_names():
    """§3.3.1 los enumera: condición invertida, off-by-one, comprobación de nulo
    que falta, argumento cambiado de orden. Repartir el set entre formas
    distintas es lo que impide que mida una sola habilidad."""
    assert {"invert_condition", "off_by_one", "drop_none_check", "swap_args"} <= set(MUTATIONS)


def test_inverting_a_condition_changes_the_program():
    mutated = mutate(SOURCE, "clasificar", "invert_condition")

    assert mutated is not None
    espacio: dict = {}
    exec(compile(mutated, "m.py", "exec"), espacio)
    assert espacio["clasificar"](10, 5) == "bajo"


def test_a_mutation_that_does_not_apply_returns_none():
    """Una función sin comparaciones no admite off-by-one. Devolver el fuente
    intacto haría creer que hay tarea donde no la hay, y la validación lo
    descubriría más tarde y más caro."""
    assert mutate("def f(x):\n    return x\n", "f", "off_by_one") is None


def test_the_mutation_only_touches_the_named_symbol():
    source = SOURCE + "\n\ndef otra(a, b):\n    if a > b:\n        return 1\n    return 0\n"

    mutated = mutate(source, "clasificar", "invert_condition")

    assert "def otra(a, b):\n    if a > b:" in mutated


def test_the_result_still_compiles():
    for kind in MUTATIONS:
        mutated = mutate(SOURCE, "clasificar", kind)
        if mutated is not None:
            compile(mutated, "m.py", "exec")
