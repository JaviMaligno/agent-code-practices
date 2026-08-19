"""Qué necesita una definición para vivir en otro fichero.

Pieza compartida por B1 y B5: mover una función sin llevarse lo que usa da
`NameError` en el primer uso, y un repo roto se lee igual que un agente que
fracasa (§11 del spec).
"""

from __future__ import annotations

import ast
import sys

import pytest

from acp.transforms.dependencies import free_names, module_bindings


def first(source: str) -> ast.AST:
    return ast.parse(source).body[0]


def test_free_names_are_what_the_definition_needs_from_outside():
    node = first(
        "def total(rows):\n"
        "    result = 0\n"
        "    for row in rows:\n"
        "        result += rate(row) * TAX\n"
        "    return result\n"
    )

    assert free_names(node) == {"rate", "TAX"}


def test_parameters_and_locals_are_not_free():
    node = first("def f(a, b=1, *args, **kwargs):\n    c = a + b\n    return c\n")

    assert free_names(node) == set()


def test_a_comprehension_variable_is_not_free():
    node = first("def f(rows):\n    return [x for x in rows if x > LIMIT]\n")

    assert free_names(node) == {"LIMIT"}


def test_module_bindings_say_where_each_name_comes_from():
    tree = ast.parse(
        "import os\n"
        "from math import pi\n"
        "\n"
        "TAX = 0.21\n"
        "\n"
        "\n"
        "def rate(x):\n"
        "    return x\n"
    )

    assert module_bindings(tree) == {
        "os": "import",
        "pi": "import",
        "TAX": "assign",
        "rate": "def",
    }


def test_a_name_bound_in_the_class_body_is_not_free_there():
    """El cuerpo de una clase sí es un ámbito para sí mismo: `RATE` ve `TAX`."""
    node = first(
        "class Invoice:\n"
        "    TAX = 0.21\n"
        "    RATE = TAX * 2\n"
    )

    assert free_names(node) == set()


def test_a_class_attribute_is_not_in_scope_for_its_methods():
    """Y no lo es para sus métodos: Python no mete el ámbito de clase en la
    cadena de los métodos, así que ese `TAX` se busca en el módulo. Si dijéramos
    que no hace falta, B1 movería la clase sin llevárselo y el método daría
    NameError en el primer uso."""
    node = first(
        "class Invoice:\n"
        "    TAX = 0.21\n"
        "\n"
        "    def total(self, amount):\n"
        "        return amount * TAX\n"
    )

    assert free_names(node) == {"TAX"}


def test_a_global_declaration_needs_the_module_it_names():
    """`global` dice justo lo contrario que una asignación: el nombre NO es
    local, vive en el módulo. Contarlo como ligado escondería la dependencia
    más fuerte que puede tener una definición —escribe estado del módulo— y es
    justo el caso que B1 tiene que sacar del reparto."""
    node = first("def bump():\n    global COUNTER\n    COUNTER += 1\n")

    assert free_names(node) == {"COUNTER"}


def test_a_nonlocal_name_belongs_to_the_enclosing_function():
    """`nonlocal` no es `global`: el nombre está en la función de fuera y viaja
    con ella, así que no se le pide nada al módulo."""
    node = first(
        "def outer():\n"
        "    total = 0\n"
        "\n"
        "    def inner():\n"
        "        nonlocal total\n"
        "        total += 1\n"
        "\n"
        "    return inner\n"
    )

    assert free_names(node) == set()


def test_a_match_capture_is_local_and_not_a_dependency():
    """Un patrón captura en una local (`kind`), pero un patrón de clase LEE un
    nombre del módulo (`Punto`). Confundirlos no deja la dosis corta: hace que
    quien mueve escriba un import de un nombre que no existe en ninguna parte,
    y eso es ImportError al cargar el módulo, no un NameError al usarlo."""
    node = first(
        "def f(value):\n"
        "    match value:\n"
        "        case {'kind': kind}:\n"
        "            return kind\n"
        "        case [primero, *resto]:\n"
        "            return primero, resto\n"
        "        case Punto(x=x):\n"
        "            return x\n"
        "        case _:\n"
        "            return DEFAULT\n"
    )

    assert free_names(node) == {"DEFAULT", "Punto"}


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 es de Python 3.12")
def test_a_type_parameter_is_not_a_dependency():
    """Mismo fallo que el `match` y por el mismo motivo: `T` lo liga la propia
    definición, así que pedirlo al módulo inventa un import."""
    funcion = first("def f[T](x: T) -> T:\n    return x\n")
    clase = ast.parse("class C[T]:\n    def get(self) -> T:\n        return LIMIT\n").body[0]

    assert free_names(funcion) == set()
    assert free_names(clase) == {"LIMIT"}
