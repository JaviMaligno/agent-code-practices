"""Qué necesita una definición para vivir en otro fichero.

Pieza compartida por B1 y B5: mover una función sin llevarse lo que usa da
`NameError` en el primer uso, y un repo roto se lee igual que un agente que
fracasa (§11 del spec).
"""

from __future__ import annotations

import ast
import sys

import pytest

from acp.transforms.dependencies import (
    annotation_names,
    free_names,
    module_bindings,
    star_imports,
)


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


def test_a_conditional_import_is_still_a_module_binding():
    """El patrón más común de la stdlib y de los repos reales: el acelerador en
    C con su respaldo en Python. `c_scanstring` es un nombre del módulo aunque
    no esté en el nivel superior del cuerpo, y quien mueve una definición que lo
    use necesita saberlo o lo dejará sin resolver."""
    tree = ast.parse(
        "try:\n"
        "    from _json import scanstring as c_scanstring\n"
        "except ImportError:\n"
        "    c_scanstring = None\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    from decimal import Decimal\n"
    )

    bindings = module_bindings(tree)

    # Gana la última forma de ligarlo, que aquí es la conservadora: copiarse
    # solo el `from _json import ...` al destino perdería el respaldo.
    assert bindings == {"c_scanstring": "assign", "Decimal": "import"}


def test_a_star_import_does_not_bind_a_name_called_star():
    """`from .base import *` trae nombres que no se pueden saber sin importar el
    otro módulo. Lo que no se puede saber se calla: publicar un binding llamado
    `*` es peor que no publicar nada, porque parece un nombre."""
    tree = ast.parse("from .base_events import *\n")

    assert module_bindings(tree) == {}


def test_module_level_loops_and_handlers_bind_names_too():
    tree = ast.parse(
        "for index in range(3):\n"
        "    LEVELS = index\n"
        "\n"
        "with open('x') as handle:\n"
        "    DATA = handle.read()\n"
    )

    assert module_bindings(tree) == {
        "index": "assign",
        "LEVELS": "assign",
        "handle": "assign",
        "DATA": "assign",
    }


def test_what_a_function_binds_inside_is_not_a_module_binding():
    """La otra mitad de la regla: se baja por los `if` y los `try`, que siguen
    siendo el ámbito del módulo, pero no por los cuerpos de def y class, que no
    lo son."""
    tree = ast.parse(
        "def outer():\n"
        "    interna = 1\n"
        "    import json\n"
        "    return interna, json\n"
        "\n"
        "\n"
        "class C:\n"
        "    ATRIBUTO = 1\n"
    )

    assert module_bindings(tree) == {"outer": "def", "C": "def"}


def test_a_comprehension_variable_is_not_a_module_binding():
    """`symtable`, que es el analizador de CPython, sí lista `v` como símbolo
    del módulo desde que 3.12 inlinea las comprehensions (PEP 709) — y sin
    embargo `v` no existe después de la línea. Publicarlo haría que quien mueve
    una definición creyera que el módulo lo aporta y escribiera un import roto,
    así que aquí se ignora a propósito."""
    tree = ast.parse("responses = {v: v.phrase for v in members()}\n")

    assert module_bindings(tree) == {"responses": "assign"}


def test_a_name_that_only_appears_in_an_annotation_is_marked_apart():
    """En pint y en sqlglot la mayoría de los nombres libres de una definición
    de nivel de módulo salen de sus anotaciones, y con `from __future__ import
    annotations` esos nombres no se evalúan nunca: el equipo los importa bajo
    `if TYPE_CHECKING` justo para evitar un ciclo. Quien mueva la definición y
    copie ese import sin la guarda cambia un repo que arranca por uno que no,
    así que la separación tiene que estar disponible, no adivinarse."""
    node = first(
        "def total(rows: Sequence[Row], factor: Decimal = DEFAULT) -> Report:\n"
        "    return build(rows)\n"
    )

    assert free_names(node) == {
        "Sequence", "Row", "Decimal", "Report", "DEFAULT", "build",
    }
    # `DEFAULT` es un valor por defecto, y ese sí se evalúa siempre.
    assert annotation_names(node) == {"Sequence", "Row", "Decimal", "Report"}


def test_a_name_also_used_outside_an_annotation_is_not_annotation_only():
    node = first("def f(value: Decimal) -> Decimal:\n    return Decimal(value)\n")

    assert annotation_names(node) == set()


def test_an_annotated_attribute_of_a_class_counts_too():
    """El caso de `stdnum/numdb.py`: la clase declara `prefixes: list[PrefixInfo]`
    y `PrefixInfo` no aparece en ninguna otra parte."""
    node = first(
        "class NumDB:\n"
        "    prefixes: list[PrefixInfo]\n"
        "\n"
        "    def add(self, prefix: PrefixInfo) -> None:\n"
        "        self.prefixes.append(prefix)\n"
    )

    assert free_names(node) == {"list", "PrefixInfo"}
    assert annotation_names(node) == {"list", "PrefixInfo"}


def test_a_star_import_is_reported_apart_because_its_names_cannot_be_known():
    """python-stdnum, el finalista más barato, hace `from stdnum.exceptions
    import *` en 246 de sus 368 ficheros: los nombres que trae son el 18% de lo
    que sus definiciones necesitan. Callarlo del todo haría que quien mueve
    concluyera «esto no lo aporta el módulo» y dejara la definición sin
    resolver. No se pueden enumerar sin importar el otro módulo, pero sí se
    puede decir de dónde vienen, que es lo que hace falta para llevarse el
    import entero al destino."""
    tree = ast.parse(
        "from stdnum.exceptions import *\n"
        "from stdnum.util import clean\n"
        "\n"
        "try:\n"
        "    from .compat import *\n"
        "except ImportError:\n"
        "    pass\n"
    )

    assert star_imports(tree) == ["stdnum.exceptions", ".compat"]
    assert "clean" in module_bindings(tree)


def test_a_module_without_star_imports_says_so():
    tree = ast.parse("from stdnum.util import clean\n")

    assert star_imports(tree) == []
