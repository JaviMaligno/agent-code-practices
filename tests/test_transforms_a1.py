from pathlib import Path

from acp.transforms import a1_types

SOURCE = '''\
from __future__ import annotations

import os  # comentario que debe sobrevivir

TOTAL: int = 0


def rate(value: int, factor: float = 1.0) -> float:
    """Sobrevive: esto es A4, no A1."""
    partial: float = value * factor
    return partial
'''


def write(root: Path, source: str = SOURCE) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(exist_ok=True)
    path = pkg / "core.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_annotations_are_gone(tmp_path: Path):
    path = write(tmp_path)

    a1_types.apply(tmp_path)

    result = path.read_text(encoding="utf-8")
    assert "value: int" not in result
    assert "-> float" not in result
    assert "partial: float" not in result
    assert "TOTAL: int" not in result


def test_only_the_types_change(tmp_path: Path):
    """A1 mide el valor de los tipos. Si de paso se lleva un comentario o una
    docstring, mide A4 y el resultado no es atribuible."""
    path = write(tmp_path)

    a1_types.apply(tmp_path)

    result = path.read_text(encoding="utf-8")
    assert "# comentario que debe sobrevivir" in result
    assert "Sobrevive: esto es A4, no A1." in result


def test_defaults_keep_their_spacing(tmp_path: Path):
    """Quitar la anotación deja `factor = 1.0`, y ese espaciado es un cambio de
    formato: sería A3 colándose dentro de A1."""
    path = write(tmp_path)

    a1_types.apply(tmp_path)

    assert "factor=1.0" in path.read_text(encoding="utf-8")


def test_an_annotated_assignment_without_value_becomes_nothing(tmp_path: Path):
    """`x: int` sin valor no declara nada en ejecución: al quitar el tipo no
    puede quedar `x`, que sería un NameError."""
    path = write(tmp_path, "def f():\n    x: int\n    x = 1\n    return x\n")

    a1_types.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "x: int" not in source


def test_a_dataclass_keeps_the_fields_its_annotations_declare(tmp_path: Path):
    """En el cuerpo de una clase la anotación no describe el atributo: lo
    declara. Quitarla borra el campo, el repo transformado deja de construir sus
    objetos y eso se lee como un agente que fracasa (§4.3)."""
    path = write(
        tmp_path,
        "from dataclasses import dataclass\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class Invoice:\n"
        "    amount: int\n"
        "    tax: float = 0.21\n",
    )

    a1_types.apply(tmp_path)

    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), "core.py", "exec"), namespace)
    assert namespace["Invoice"](100).tax == 0.21


def test_a_method_signature_inside_that_class_still_loses_its_types(tmp_path: Path):
    """La excepción es solo para el cuerpo de la clase. Si se comiera también las
    firmas, A1 dejaría de quitar tipos justo donde están casi todos: en los tres
    finalistas los parámetros y retornos son más del 90% de las anotaciones."""
    path = write(
        tmp_path,
        "class Invoice:\n"
        "    amount: int = 0\n"
        "\n"
        "    def total(self, factor: float) -> float:\n"
        "        return self.amount * factor\n",
    )

    a1_types.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "factor: float" not in source
    assert "-> float" not in source
    assert "amount: int = 0" in source


def test_the_code_still_runs(tmp_path: Path):
    path = write(tmp_path)

    a1_types.apply(tmp_path)

    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), "core.py", "exec"), namespace)
    assert namespace["rate"](21, 2.0) == 42.0


def run(path: Path) -> dict:
    """Ejecuta el fichero transformado. `exec` y no `compile` a secas porque el
    despacho por anotación no falla al compilar: falla al ejecutar el decorador,
    que es lo que convierte el árbol roto en algo que se lee como un agente que
    fracasa (§4.3)."""
    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), path.name, "exec"), namespace)
    return namespace


def test_a_function_registered_by_dispatch_keeps_the_annotation_that_selects_it(tmp_path: Path):
    """En `@show.register` sin argumentos la anotación del primer parámetro no es
    documentación: es el selector que `functools` lee para elegir la
    implementación. Quitarla cambia el comportamiento del programa, así que A1
    dejaría de ser semánticamente equivalente (§4.3).

    El fichero vive en `tests/` a propósito: A1 transforma la suite del repo
    (`iter_transformable_files`, §4.3.1) mientras la métrica `runtime_typing` de
    la fase 0 solo recorre `iter_source_files`, así que un `@singledispatch` en
    la suite es invisible al criterio de exclusión y solo lo puede proteger A1.
    """
    suite = tmp_path / "tests"
    suite.mkdir()
    path = suite / "test_dispatch.py"
    path.write_text(
        "from functools import singledispatch\n"
        "\n"
        "\n"
        "@singledispatch\n"
        "def show(value):\n"
        "    return 'any'\n"
        "\n"
        "\n"
        "@show.register\n"
        "def _(value: int):\n"
        "    return 'int'\n",
        encoding="utf-8",
    )

    a1_types.apply(tmp_path)

    namespace = run(path)
    assert namespace["show"](1) == "int"
    assert namespace["show"]("x") == "any"


def test_a_method_registered_by_dispatch_keeps_it_even_though_self_comes_first(tmp_path: Path):
    """`singledispatchmethod` lee la primera anotación que encuentra, y en un
    método esa no está en el primer parámetro: `self` va delante y no se anota.
    Proteger solo el parámetro nº1 dejaría los métodos rotos."""
    path = write(
        tmp_path,
        "from functools import singledispatchmethod\n"
        "\n"
        "\n"
        "class Printer:\n"
        "    @singledispatchmethod\n"
        "    def show(self, value):\n"
        "        return 'any'\n"
        "\n"
        "    @show.register\n"
        "    def _(self, value: int):\n"
        "        return 'int'\n",
    )

    a1_types.apply(tmp_path)

    namespace = run(path)
    printer = namespace["Printer"]()
    assert printer.show(1) == "int"
    assert printer.show("x") == "any"


def test_only_the_annotation_that_dispatches_survives(tmp_path: Path):
    """La protección es del selector, no de la firma entera: el resto de
    anotaciones de esa función siguen siendo documentación y A1 se las lleva. Si
    no, cada `@register` regalaría dosis que el experimento cree haber quitado."""
    path = write(
        tmp_path,
        "from functools import singledispatch\n"
        "\n"
        "\n"
        "@singledispatch\n"
        "def show(value, factor=1):\n"
        "    return 'any'\n"
        "\n"
        "\n"
        "@show.register\n"
        "def _(value: int, factor: float = 1.0) -> str:\n"
        "    return 'int'\n",
    )

    a1_types.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "value: int" in source
    assert "factor: float" not in source
    assert "factor=1.0" in source
    assert "-> str" not in source


def test_a_registration_that_names_its_class_loses_its_annotations(tmp_path: Path):
    """`@show.register(int)` da la clase explícitamente: ahí la anotación vuelve a
    ser documentación y quitarla no cambia nada. Protegerla también sería inflar
    la dosis de tipos que sobrevive sin ninguna razón semántica."""
    path = write(
        tmp_path,
        "from functools import singledispatch\n"
        "\n"
        "\n"
        "@singledispatch\n"
        "def show(value):\n"
        "    return 'any'\n"
        "\n"
        "\n"
        "@show.register(int)\n"
        "def _(value: int):\n"
        "    return 'int'\n",
    )

    a1_types.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    assert "value: int" not in source
    namespace = run(path)
    assert namespace["show"](1) == "int"
