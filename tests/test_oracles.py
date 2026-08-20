"""Los dos agentes falsos de §5.4.6.

El no-op no edita nada y tiene que dar 0%; el oráculo aplica el parche de
referencia y tiene que dar 100%. Lo caro de verdad es lo segundo, y no por el
parche: bajo A2 el símbolo se llama distinto, bajo A3 la línea está aplastada,
bajo A4 el contexto del hunk ya no existe y bajo B1/B2/B5 el fichero es otro.
Un oráculo que solo sepa revertir texto se lee exactamente igual que un agente
que fracasa, que es el error más caro que este control existe para atrapar.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.cli import manifest_path_for, transform_repo
from acp.oracles import (
    compare_to_delivered,
    no_op,
    oracle,
    repaired_source,
    run_oracle,
)
from acp.tasks.inject import apply_patch, inject

ORIGINAL = '''\
"""Un módulo con su ejemplo, que es lo que la tarea tiene que romper.

>>> clasificar(10, 5)
'alto'
"""


def clasificar(valor, limite):
    """Alto o bajo, según dónde caiga el valor.

    >>> clasificar(1, 5)
    'bajo'
    """
    # El umbral es estricto: justo en el límite todavía es bajo.
    if valor > limite:
        return "alto"
    return "bajo"
'''


def build(root: Path) -> Path:
    """Un repositorio mínimo con un símbolo que declara su propio ejemplo."""
    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    path = pkg / "core.py"
    path.write_text(ORIGINAL, encoding="utf-8")
    return path


def delivered(root: Path):
    """El árbol tal y como se le entrega al agente: con el fallo ya dentro.

    La tarea se declara sobre el árbol SANO —el parche es original→roto— y el
    árbol que recibe el agente es el resultado de aplicarlo. Es el orden de la
    campaña: primero se inyecta el fallo, y solo después se transforma, porque
    la transformación tiene que alcanzar también al código roto.
    """
    path = build(root)
    task = inject(root, module="pkg.core", symbol="clasificar", kind="invert_condition")
    path.write_text(apply_patch(ORIGINAL, task.patch), encoding="utf-8")
    return path, task


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_the_no_op_changes_nothing(tmp_path: Path):
    """Debe dar 0% en todas las condiciones. Si da más, hay tareas cuyos tests
    no discriminan (§5.4.6)."""
    _, task = delivered(tmp_path)
    antes = snapshot(tmp_path)

    no_op(tmp_path, task)

    assert snapshot(tmp_path) == antes


def test_the_oracle_restores_what_the_task_broke(tmp_path: Path):
    """Debe dar 100% en todas. Si da menos, o la transformación rompió el repo,
    o el mapa de identidad de símbolos está mal."""
    path, task = delivered(tmp_path)

    oracle(tmp_path, task)

    assert path.read_text(encoding="utf-8") == ORIGINAL


def test_the_oracle_does_not_write_when_asked_only_for_the_repair(tmp_path: Path):
    """El circuito de validación mete y saca el parche por `docker cp` y deja el
    árbol del anfitrión intacto (§4.2). El oráculo tiene que poder entrar por
    ahí, así que el arreglo se calcula aparte de escribirlo."""
    path, task = delivered(tmp_path)
    roto = path.read_text(encoding="utf-8")

    relativa, arreglado = repaired_source(tmp_path, task)

    assert relativa == "pkg/core.py"
    assert arreglado == ORIGINAL
    assert path.read_text(encoding="utf-8") == roto


def test_the_oracle_finds_the_symbol_even_after_a_rename(tmp_path: Path):
    """Con A2 el símbolo se llama distinto y con B1 vive en otro fichero: el
    oráculo tiene que localizarlo por el mapa de identidad, no por su nombre."""
    fuente = tmp_path / "repo"
    fuente.mkdir()
    _, task = delivered(fuente)
    trabajo = transform_repo(fuente, ["A2"], tmp_path / "work")
    transformado = (trabajo / "pkg" / "core.py").read_text(encoding="utf-8")
    assert "def clasificar" not in transformado, "A2 no renombró: el test no prueba nada"

    oracle(trabajo, task)

    arreglado = (trabajo / "pkg" / "core.py").read_text(encoding="utf-8")
    # La condición se conserva —el oráculo arregla, no deshace la celda— y el
    # fallo se fue: el operador vuelve a ser el que la tarea invirtió.
    assert "def clasificar" not in arreglado
    assert "valor > limite" not in arreglado.replace(" ", "")
    assert ">" in arreglado.split("if ")[1].split(":")[0]


def test_the_oracle_survives_a_tree_with_the_spacing_crushed(tmp_path: Path):
    """A3 junta las continuaciones y quita el aire de los operadores, así que la
    línea del hunk no aparece en el árbol tal y como el parche la escribió."""
    fuente = tmp_path / "repo"
    fuente.mkdir()
    _, task = delivered(fuente)
    trabajo = transform_repo(fuente, ["A3"], tmp_path / "work")

    oracle(trabajo, task)

    arreglado = (trabajo / "pkg" / "core.py").read_text(encoding="utf-8")
    assert "valor>limite" in arreglado.replace(" ", "")
    assert "valor<=limite" not in arreglado.replace(" ", "")


def test_the_oracle_survives_a_tree_without_its_docs(tmp_path: Path):
    """A4 se lleva el comentario y la prosa de la docstring, que son justo las
    líneas de contexto con las que el hunk se sitúa."""
    fuente = tmp_path / "repo"
    fuente.mkdir()
    _, task = delivered(fuente)
    trabajo = transform_repo(fuente, ["A4"], tmp_path / "work")
    assert "umbral" not in (trabajo / "pkg" / "core.py").read_text(encoding="utf-8")

    oracle(trabajo, task)

    arreglado = (trabajo / "pkg" / "core.py").read_text(encoding="utf-8")
    assert "valor > limite" in arreglado
    # El arreglo no reintroduce lo que la condición quitó: si el oráculo pegara
    # el trozo del parche con su contexto, devolvería el comentario y el árbol
    # dejaría de ser el de la celda.
    assert "umbral" not in arreglado


def test_the_oracle_is_loud_when_the_manifest_lost_the_symbol(tmp_path: Path):
    """Si el manifiesto no lo encuentra, esa condición no es medible: el fallo
    tiene que sonar aquí y no a mitad de campaña (§5.4.6)."""
    fuente = tmp_path / "repo"
    fuente.mkdir()
    _, task = delivered(fuente)
    trabajo = transform_repo(fuente, ["A2"], tmp_path / "work")
    manifiesto = manifest_path_for(trabajo)
    contenido = json.loads(manifiesto.read_text(encoding="utf-8"))
    del contenido["symbols"]["pkg.core.clasificar"]
    manifiesto.write_text(json.dumps(contenido), encoding="utf-8")

    with pytest.raises(LookupError, match="pkg.core.clasificar"):
        oracle(trabajo, task)


def test_the_oracle_is_loud_when_the_broken_code_is_not_there(tmp_path: Path):
    """Un símbolo que el manifiesto sitúa pero cuyo cuerpo no es el que el
    parche describe significa que se está midiendo otra cosa. Aplicar el arreglo
    «donde más se parezca» es lo que hace `patch`, y es justo lo que aquí deja
    el árbol con un fallo distinto del que la tarea declara."""
    path, task = delivered(tmp_path)
    path.write_text(ORIGINAL.replace("valor > limite", "valor >= limite"), encoding="utf-8")

    with pytest.raises(ValueError, match="clasificar"):
        oracle(tmp_path, task)


CON_CONSTANTE = '''\
"""Un módulo cuyo fallo está escrito con un nombre que A2 sí cambia.

>>> clasificar(10)
'alto'
"""

LIMITE = 5


def clasificar(valor):
    """Alto o bajo, según el límite del módulo.

    >>> clasificar(1)
    'bajo'
    """
    if valor > LIMITE:
        return "alto"
    return "bajo"
'''


def test_the_oracle_writes_the_names_the_condition_uses(tmp_path: Path):
    """El trozo roto del árbol no está escrito como lo escribió el parche: A2 le
    cambió el nombre a todo lo que la línea nombra. Sin pasar el trozo por el
    diccionario de renombrados, ni se encuentra el fallo ni el arreglo llamaría
    a algo que exista —y un fichero que no compila se lee como un oráculo que
    puntúa 0, o sea como un agente que fracasa—."""
    fuente = tmp_path / "repo"
    (fuente / "pkg").mkdir(parents=True)
    (fuente / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    path = fuente / "pkg" / "core.py"
    path.write_text(CON_CONSTANTE, encoding="utf-8")
    task = inject(fuente, module="pkg.core", symbol="clasificar", kind="invert_condition")
    path.write_text(apply_patch(CON_CONSTANTE, task.patch), encoding="utf-8")
    trabajo = transform_repo(fuente, ["A2"], tmp_path / "work")
    opaco = json.loads(manifest_path_for(trabajo).read_text(encoding="utf-8"))["renames"]["LIMITE"]

    oracle(trabajo, task)

    arreglado = (trabajo / "pkg" / "core.py").read_text(encoding="utf-8")
    assert f"valor > {opaco}" in arreglado
    assert "LIMITE" not in arreglado
    compile(arreglado, "core.py", "exec")


MOVIDO = '''\
"""Clasifica un valor contra el límite del módulo.

>>> clasificar(10)
'alto'
"""

from pkg.util import limpiar

LIMITE = 5


def clasificar(valor):
    """Alto o bajo.

    >>> clasificar(1)
    'bajo'
    """
    if limpiar(valor) > LIMITE:
        return "alto"
    return "bajo"
'''

# La celda más dura de la matriz: el símbolo renombrado (A2), su línea aplastada
# (A3), sin la prosa con la que el hunk se sitúa (A4), anotado (A1), repartido
# entre ficheros (B1), fundido con sus hermanos (B5) y con la jerarquía aplanada
# (B2). Si el oráculo sobrevive a esto, sobrevive a la campaña.
LA_PEOR = ["B1", "B5-500", "A1", "A2", "A4", "A3", "B2"]


def build_moved(root: Path) -> Path:
    pkg = root / "pkg"
    (pkg / "es").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "util.py").write_text("def limpiar(x):\n    return int(x)\n", encoding="utf-8")
    # Hermanos suficientes para que B5 tenga qué fundir y B1 dónde repartir.
    for index in range(8):
        (pkg / f"otro{index}.py").write_text(
            f"CONST_{index} = {index}\n\n\ndef g{index}(x):\n    return x + CONST_{index}\n",
            encoding="utf-8",
        )
    (pkg / "es" / "__init__.py").write_text("", encoding="utf-8")
    path = pkg / "es" / "nif.py"
    path.write_text(MOVIDO, encoding="utf-8")
    return path


def test_the_oracle_follows_the_symbol_to_another_file(tmp_path: Path):
    """B1 reparte definiciones, B5 funde ficheros y B2 aplana el paquete: el
    fichero que el parche nombra no existe en la condición. El oráculo tiene que
    ir donde el manifiesto dice, y no donde el módulo de la tarea se llamaba."""
    fuente = tmp_path / "repo"
    fuente.mkdir()
    path = build_moved(fuente)
    task = inject(fuente, module="pkg.es.nif", symbol="clasificar", kind="invert_condition")
    path.write_text(apply_patch(MOVIDO, task.patch), encoding="utf-8")

    trabajo = transform_repo(fuente, LA_PEOR, tmp_path / "work")

    destino = json.loads(manifest_path_for(trabajo).read_text(encoding="utf-8"))
    ruta = destino["symbols"]["pkg.es.nif.clasificar"]["path"]
    assert ruta != "pkg/es/nif.py", "la condición no movió el símbolo: el test no prueba nada"
    assert not (trabajo / "pkg" / "es").exists()

    relativa, arreglado = repaired_source(trabajo, task)

    assert relativa == ruta
    roto = (trabajo / ruta).read_text(encoding="utf-8")
    assert arreglado != roto
    # El operador vuelve a ser el que la tarea invirtió, y el arreglo está
    # escrito con los nombres de la condición: un fichero que no compila se
    # leería como un oráculo que puntúa 0, o sea como un agente que fracasa.
    assert "<=" not in arreglado.replace(" ", "")
    assert ">" in arreglado
    compile(arreglado, ruta, "exec")


# --- Cómo se leen 0% y 100% -------------------------------------------------
#
# Los nodeids de la tarea se declararon sobre el árbol original, y en la
# condición ni la ruta ni el nombre del módulo siguen siendo esos: B2 aplana y
# B5 funde. Por eso el veredicto se lee contra la corrida del árbol tal y como
# se entrega —que es justo lo que produce el no-op— y no contra la lista.

ENTREGADO = {"t_roto": "failed", "t_sano": "passed", "t_saltado": "skipped"}


def test_the_no_op_does_not_resolve_a_task_that_discriminates():
    """El 0% del no-op no es una convención: sale de que el árbol entregado
    tiene en rojo lo que la tarea rompió y el no-op no lo toca."""
    run = compare_to_delivered("no_op", ENTREGADO, ENTREGADO)

    assert run.resolved is False
    assert run.still_failing == ["t_roto"]


def test_a_no_op_that_resolves_means_the_task_does_not_discriminate():
    """Es la avería que este control existe para atrapar (§5.4.6): si el árbol
    se entrega en verde, la tarea se contaría como resuelta sin tocar nada."""
    verde = {"t_a": "passed", "t_b": "passed"}

    run = compare_to_delivered("no_op", verde, verde)

    assert run.resolved is True


def test_the_oracle_resolves_when_the_reds_go_green():
    run = compare_to_delivered(
        "oracle", ENTREGADO, {**ENTREGADO, "t_roto": "passed"}
    )

    assert run.resolved is True
    assert run.repaired == ["t_roto"]


def test_an_oracle_that_breaks_something_else_does_not_resolve():
    """Arreglar el fallo rompiendo otra cosa no es arreglarlo, y en la tabla
    principal se leería igual que un agente que acertó."""
    run = compare_to_delivered(
        "oracle", ENTREGADO, {"t_roto": "passed", "t_sano": "failed", "t_saltado": "skipped"}
    )

    assert run.resolved is False
    assert run.broken == ["t_sano"]


def test_a_test_that_stops_being_collected_counts_as_broken():
    """Un test que ya no se colecta dejó de demostrar nada. Contarlo como «sigue
    verde» porque no sale en rojo es la forma silenciosa de romper la suite."""
    run = compare_to_delivered(
        "oracle", ENTREGADO, {"t_roto": "passed", "t_saltado": "skipped"}
    )

    assert run.resolved is False
    assert run.broken == ["t_sano"]


def test_what_was_skipped_is_not_a_failure():
    """Un test saltado no es un test roto, y exigirle que pase dejaría al
    oráculo en 0% en cualquier repositorio real."""
    run = compare_to_delivered("oracle", ENTREGADO, {**ENTREGADO, "t_roto": "passed"})

    assert "t_saltado" not in run.still_failing + run.broken


def test_a_test_the_repo_had_in_red_is_not_held_against_the_oracle():
    """No lo rompió la tarea, así que exigirle al oráculo que lo arregle
    convertiría un defecto del repositorio en un circuito que miente."""
    run = compare_to_delivered(
        "oracle",
        {**ENTREGADO, "t_ajeno": "failed"},
        {**ENTREGADO, "t_roto": "passed", "t_ajeno": "failed"},
        already_failing=["t_ajeno"],
    )

    assert run.resolved is True


def test_an_unknown_control_is_refused_before_paying_for_a_suite(tmp_path: Path):
    """Levantar el contenedor y descubrir el nombre mal escrito después cuesta
    una instalación entera, y el resultado se leería como una condición sin
    datos."""
    _, task = delivered(tmp_path)

    with pytest.raises(ValueError, match="oracle"):
        run_oracle("oraculo", tmp_path, task)
