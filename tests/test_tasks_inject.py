"""Convertir una mutación del catálogo en una tarea con su parche.

`mutate` devuelve el fuente entero mutado; una tarea necesita un PARCHE, que es
lo que se aplica y se revierte. El parche es además la pieza de la que depende el
oráculo de control (§5.4.6): si no se puede revertir exactamente, no hay forma de
demostrar que el circuito de medida da 100% cuando el fallo se arregla.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.tasks.inject import apply_patch, declared_tests, inject, module_path

FUENTE = '''\
"""Un módulo con su ejemplo.

>>> clasificar(10, 5)
'alto'
"""


def clasificar(valor, limite):
    """Clasifica.

    >>> clasificar(1, 5)
    'bajo'
    """
    if valor > limite:
        return "alto"
    return "bajo"


def sin_ejemplo(valor):
    """No trae ejemplo."""
    return valor + 1
'''


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    paquete = tmp_path / "pkg"
    paquete.mkdir()
    (paquete / "__init__.py").write_text("", encoding="utf-8")
    (paquete / "core.py").write_text(FUENTE, encoding="utf-8")
    return tmp_path


def test_the_module_is_found_where_python_would_look_for_it(repo: Path):
    assert module_path(repo, "pkg.core") == repo / "pkg" / "core.py"
    assert module_path(repo, "pkg") == repo / "pkg" / "__init__.py"


def test_a_module_that_is_not_there_is_loud(repo: Path):
    """Un módulo mal escrito tiene que sonar aquí. Si `inject` se lo tragara, la
    tarea se generaría contra otro fichero o contra ninguno, y el error saldría
    dos corridas de Docker más tarde."""
    with pytest.raises(LookupError):
        module_path(repo, "pkg.no_existe")


def test_a_layout_with_src_is_found_too(tmp_path: Path):
    paquete = tmp_path / "src" / "pkg"
    paquete.mkdir(parents=True)
    (paquete / "core.py").write_text("x = 1\n", encoding="utf-8")

    assert module_path(tmp_path, "pkg.core") == paquete / "core.py"


def test_the_patch_applied_to_the_original_gives_the_mutated_file(repo: Path):
    task = inject(repo, module="pkg.core", symbol="clasificar", kind="invert_condition")

    mutado = apply_patch(FUENTE, task.patch)

    assert 'if valor <= limite:' in mutado
    assert mutado != FUENTE


def test_the_patch_reverted_gives_the_original_back(repo: Path):
    """Es lo que el oráculo hace para demostrar que arreglar el fallo da 100%
    (§5.4.6). Un parche que no revierte al carácter deja al oráculo por debajo
    del 100% por un defecto del circuito, no de la tarea."""
    task = inject(repo, module="pkg.core", symbol="clasificar", kind="invert_condition")
    mutado = apply_patch(FUENTE, task.patch)

    assert apply_patch(mutado, task.patch, reverse=True) == FUENTE


def test_applying_a_patch_to_the_wrong_file_is_loud(repo: Path):
    """Con B1/B2/B5 el símbolo vive en otro fichero y con A2 se llama distinto:
    el oráculo tiene que enterarse de que aplicó el parche donde no era, no
    dejar el árbol a medias en silencio."""
    task = inject(repo, module="pkg.core", symbol="clasificar", kind="invert_condition")

    with pytest.raises(ValueError):
        apply_patch("def otra_cosa():\n    return 1\n", task.patch)


def test_applying_a_patch_where_the_lines_no_longer_match_is_loud(repo: Path):
    """El caso que de verdad le va a pasar al oráculo: el fichero está, tiene el
    tamaño de siempre y el símbolo ya no dice lo mismo —A1 le quitó los
    comentarios, A4 le cambió el formato—. `patch` buscaría el hueco unas líneas
    más allá y aplicaría igual, dejando el árbol con un fallo distinto del que la
    tarea declara."""
    task = inject(repo, module="pkg.core", symbol="clasificar", kind="invert_condition")
    retocado = FUENTE.replace('if valor > limite:', 'if valor > limite:  # nota')

    with pytest.raises(ValueError, match="esperaba"):
        apply_patch(retocado, task.patch)


def test_injecting_does_not_touch_the_working_tree(repo: Path):
    """El árbol del anfitrión se queda como estaba: la tarea es un parche, y
    quién lo aplica y dónde lo decide la condición. Escribirlo aquí obligaría a
    restaurar el clon entre tareas y convertiría un olvido en una tarea que
    hereda el fallo de la anterior."""
    inject(repo, module="pkg.core", symbol="clasificar", kind="invert_condition")

    assert (repo / "pkg" / "core.py").read_text(encoding="utf-8") == FUENTE


def test_a_shape_that_does_not_apply_is_not_a_task(repo: Path):
    with pytest.raises(ValueError):
        inject(repo, module="pkg.core", symbol="sin_ejemplo", kind="invert_condition")


def test_a_function_declares_its_own_doctest_as_the_test_it_must_break():
    assert declared_tests(FUENTE, "pkg.core", "pkg/core.py", "clasificar") == [
        "pkg/core.py::pkg.core.clasificar"
    ]


def test_a_function_without_an_example_falls_back_to_the_one_its_module_declares():
    """En un repo cuyos tests viven en los docstrings, lo que un símbolo declara
    como su prueba está escrito al lado. Es una DECLARACIÓN, hecha antes de
    correr nada: si saliera de observar la corrida, `fail_to_pass_ok` sería
    tautológico y la validación solo podría fallar por exceso."""
    assert declared_tests(FUENTE, "pkg.core", "pkg/core.py", "sin_ejemplo") == [
        "pkg/core.py::pkg.core"
    ]


def test_a_module_that_declares_no_example_cannot_declare_a_task():
    with pytest.raises(ValueError):
        declared_tests("def f():\n    return 1\n", "pkg.core", "pkg/core.py", "f")


def test_the_task_carries_the_stratum_and_the_reading_cost_it_was_given(repo: Path):
    task = inject(
        repo,
        module="pkg.core",
        symbol="clasificar",
        kind="invert_condition",
        stratum="domain",
        min_files_to_judge=3,
        fail_to_pass=["pkg/core.py::pkg.core.clasificar"],
    )

    assert (task.stratum, task.min_files_to_judge) == ("domain", 3)
    assert task.module == "pkg.core"
    assert task.symbol == "clasificar"


def test_two_injections_of_the_same_fault_give_the_same_task(repo: Path):
    """El parche de referencia del oráculo tiene que seguir siendo el mismo dos
    meses después, o la tarea guardada en JSON deja de poder regenerarse."""
    primera = inject(repo, module="pkg.core", symbol="clasificar", kind="invert_condition")
    segunda = inject(repo, module="pkg.core", symbol="clasificar", kind="invert_condition")

    assert primera.to_json() == segunda.to_json()
