import pytest

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


GUARDED = '''\
def buscar(clave, tabla):
    if clave is None:
        return None
    return tabla[clave]
'''

CALLING = '''\
def rango(inicio, fin):
    return construir(inicio, fin)
'''


def test_the_symbol_can_be_a_method():
    """La mitad de lo mutable de los finalistas vive dentro de una clase, y
    nombrar el método a secas confundiría dos métodos homónimos de clases
    distintas: la tarea diría que rompe uno y rompería el otro."""
    source = "class Uno:\n    def cabe(self, n):\n        return n > 10\n\n\nclass Dos:\n    def cabe(self, n):\n        return n > 10\n"

    mutated = mutate(source, "Dos.cabe", "invert_condition")

    assert mutated.count("n > 10") == 1
    assert "class Dos:\n    def cabe(self, n):\n        return n <= 10" in mutated


def test_a_symbol_that_is_not_in_the_source_is_an_error():
    """`None` significa "esta forma no aplica aquí" y el generador lo trata como
    algo normal, así que un símbolo mal escrito se leería como una función sin
    comparaciones y pasaría desapercibido. Un nombre que no está es un fallo del
    generador, no del catálogo."""
    with pytest.raises(LookupError):
        mutate(SOURCE, "clasificarr", "invert_condition")


def test_a_kind_that_is_not_in_the_catalogue_is_an_error():
    with pytest.raises(KeyError):
        mutate(SOURCE, "clasificar", "invert_conditon")


def test_an_off_by_one_moves_a_boundary():
    source = "def cabe(n):\n    if n > 10:\n        return False\n    return True\n"

    mutated = mutate(source, "cabe", "off_by_one")

    espacio: dict = {}
    exec(compile(mutated, "m.py", "exec"), espacio)
    assert espacio["cabe"](11) is True


def test_the_constant_does_not_have_to_be_in_a_comparison():
    """El primer candidato del sustrato —`mod_97_10.checksum` de python-stdnum,
    `int(number) % 97`— no tiene ninguna comparación: si el off-by-one solo
    supiera mover límites de comparación, el catálogo no aplicaría en la mitad
    de las funciones aritméticas de los finalistas, que es justo donde ese fallo
    es más natural."""
    source = "def resto(numero):\n    return int(numero) % 97\n"

    mutated = mutate(source, "resto", "off_by_one")

    assert mutated is not None
    assert "% 98" in mutated


def test_a_boundary_in_a_comparison_wins_over_any_other_constant():
    """Cuando hay las dos, se mueve el límite: es donde el off-by-one cambia el
    comportamiento en el borde y no en todo el rango, que es la forma que §3.3.1
    nombra."""
    source = "def cabe(n):\n    total = n * 3\n    if total > 10:\n        return False\n    return True\n"

    mutated = mutate(source, "cabe", "off_by_one")

    assert "n * 3" in mutated
    assert "total > 11" in mutated


def test_a_none_guard_disappears():
    mutated = mutate(GUARDED, "buscar", "drop_none_check")

    assert mutated is not None
    assert "is None" not in mutated
    assert "return tabla[clave]" in mutated


def test_a_guard_that_is_the_whole_body_is_not_dropped():
    """Quitarla dejaría un cuerpo vacío, que no compila: el parche no sería una
    tarea sino un repo roto, y la validación lo vería como una tarea que rompe
    la suite entera."""
    source = "def comprobar(x):\n    if x is None:\n        raise ValueError(x)\n"

    assert mutate(source, "comprobar", "drop_none_check") is None


def test_swapping_two_arguments_changes_the_call():
    mutated = mutate(CALLING, "rango", "swap_args")

    assert mutated is not None
    assert "construir(fin, inicio)" in mutated


def test_swapping_two_identical_arguments_is_not_a_task():
    """Sería una mutación aplicada que no cambia el programa: una tarea que no
    rompe nada y que se contaría como resuelta siempre (§3.2.1)."""
    source = "def doble(x):\n    return maximo(x, x)\n"

    assert mutate(source, "doble", "swap_args") is None


def test_a_mutation_of_a_later_function_leaves_the_earlier_one_alone():
    """El test del plan nombra la PRIMERA función del fichero, y ahí una fuga de
    alcance no se ve: la mutación se gasta en la función correcta antes de llegar
    a la de al lado. Nombrando la segunda, cualquier fuga hacia delante sale."""
    source = SOURCE + "\n\ndef otra(a, b):\n    if a > b:\n        return 1\n    return 0\n"

    mutated = mutate(source, "otra", "invert_condition")

    assert "def clasificar(valor, limite):\n    if valor > limite:" in mutated
    assert "if a <= b:" in mutated
