"""Qué necesita una definición para vivir en otro fichero.

Pieza compartida por B1 y B5: mover una función sin llevarse lo que usa da
`NameError` en el primer uso, y un repo roto se lee igual que un agente que
fracasa (§11 del spec).
"""

from __future__ import annotations

import ast

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
