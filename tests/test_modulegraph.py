from acp.transforms.modulegraph import components


def test_a_cycle_puts_every_module_of_it_in_the_same_component():
    """Lo que hace falta saber antes de tocar un grafo de imports: qué parte ya
    se importaba en círculo. Ahí una arista más no cambia nada —el intérprete ya
    sobrevive a ese enredo— y fuera sí."""
    etiquetas = components([("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")])

    assert etiquetas["a"] == etiquetas["b"] == etiquetas["c"]
    assert etiquetas["d"] != etiquetas["a"]


def test_a_chain_leaves_each_module_alone():
    etiquetas = components([("a", "b"), ("b", "c")])

    assert len(set(etiquetas.values())) == 3


def test_two_runs_label_the_same_graph_the_same_way():
    """Las etiquetas deciden qué movimientos se aceptan, así que si dependieran
    del orden de recorrido dos corridas de la misma celda darían árboles
    distintos (§5.4.4)."""
    aristas = [("b", "a"), ("a", "b"), ("c", "b"), ("d", "e")]

    assert components(aristas) == components(list(reversed(aristas)))
