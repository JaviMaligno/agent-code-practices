from pathlib import Path

from acp.transforms import a3_format

SOURCE = '''\
import os


def rate(value, factor):
    total = value * factor + 1

    return total


def other(x):
    return x
'''


def write(root: Path, source: str = SOURCE) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(exist_ok=True)
    path = pkg / "core.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_blank_lines_are_gone(tmp_path: Path):
    path = write(tmp_path)

    a3_format.apply(tmp_path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert all(line.strip() for line in lines)


def test_operator_spacing_is_gone(tmp_path: Path):
    path = write(tmp_path)

    a3_format.apply(tmp_path)

    assert "value*factor+1" in path.read_text(encoding="utf-8")


def test_indentation_survives_because_it_is_syntax(tmp_path: Path):
    path = write(tmp_path)

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert any(line.startswith("    ") for line in source.splitlines())


def test_the_code_still_runs(tmp_path: Path):
    path = write(tmp_path)

    a3_format.apply(tmp_path)

    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), "core.py", "exec"), namespace)
    assert namespace["rate"](6, 6) == 37


def test_assignment_spacing_is_gone(tmp_path: Path):
    """El `=` es el operador más frecuente de un fichero de Python. Dejarlo con
    aire deja A3 casi sin efecto sobre el código típico, que son asignaciones y
    llamadas, no aritmética: la condición mediría una dosis mucho menor que la
    nominal."""
    path = write(
        tmp_path,
        "def f(a):\n    total = a * 2\n    total += 1\n    return total\n",
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "total=a*2" in source
    assert "total+=1" in source


def test_keyword_comparisons_keep_the_space_they_need(tmp_path: Path):
    """`a in b` sin espacios sería `ainb`: en estos operadores el espacio es
    sintaxis, igual que la sangría, y LibCST se niega a escribirlos pegados."""
    path = write(tmp_path, "def f(a, b):\n    return a in b and a is not b\n")

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "a in b" in source
    assert "a is not b" in source


def test_comments_survive_because_they_are_A4(tmp_path: Path):
    """Llevarse los comentarios haría que A3 midiera también A4, y entonces
    ninguna de las dos celdas del diseño es atribuible."""
    path = write(
        tmp_path,
        "# la regla viene de la norma\n"
        "X = 1\n"
        "\n"
        "\n"
        "def f(a, b):\n"
        "    # el orden importa\n"
        "    return (a +  # la suma primero\n"
        "            b)\n",
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "# la regla viene de la norma" in source
    assert "# el orden importa" in source
    assert "# la suma primero" in source


def test_strings_are_not_touched(tmp_path: Path):
    """Colapsar espacios dentro de una cadena cambia el programa, y varios
    finalistas comparan mensajes de error literales en sus tests."""
    path = write(tmp_path, 'MESSAGE = "a + b  se queda igual"\n')

    a3_format.apply(tmp_path)

    assert '"a + b  se queda igual"' in path.read_text(encoding="utf-8")


def test_a_split_expression_ends_on_one_line(tmp_path: Path):
    """§4.1 pide «expresiones colapsadas»: una expresión repartida en cuatro
    renglones es exactamente la señal visual que A3 tiene que borrar, igual que
    el aire alrededor de los operadores."""
    path = write(
        tmp_path,
        "def f(a):\n    result = (\n        a\n        + 1\n    )\n    return result\n",
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "result=(a+1)" in source


def test_a_split_dict_and_call_end_on_one_line(tmp_path: Path):
    """El caso típico de un repo real no es aritmética: son literales y
    llamadas abiertas en varias líneas por el formateador."""
    path = write(
        tmp_path,
        'values = {\n    "a": 1,\n    "b": 2,\n}\ntotal = sum(\n    values.values()\n)\n',
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert len(source.splitlines()) == 2
    assert "total=sum(values.values())" in source


def test_a_split_signature_ends_on_one_line(tmp_path: Path):
    """La firma partida por parámetro es la continuación más frecuente de un
    repo con formateador, y es la que el agente lee antes que nada."""
    path = write(
        tmp_path,
        "def rate(\n    value,\n    factor=2,\n):\n    return value * factor\n",
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert source.splitlines()[0] == "def rate(value,factor=2,):"


def test_a_split_condition_ends_on_one_line(tmp_path: Path):
    """La cabecera de un compuesto también es una línea lógica: si no se junta,
    la dosis se queda a medias justo en el sitio donde se decide el flujo."""
    path = write(
        tmp_path,
        "def f(a, b):\n    if (\n        a\n        and b\n    ):\n        return 1\n    return 0\n",
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "if (a and b):" in source


def test_a_backslash_continuation_disappears(tmp_path: Path):
    """La continuación con barra no está entre paréntesis: es espacio a secas
    con un `\\` dentro, y sin tratarla A3 deja intacta la mitad de las
    continuaciones de los repos que no usan formateador."""
    path = write(
        tmp_path,
        'def f(a):\n'
        '    total = 0\n'
        '    del \\\n'
        '        a\n'
        '    assert total == 0, \\\n'
        '        "vacio"\n'
        '    return total\n',
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "\\" not in source
    assert len(source.splitlines()) == 5


def test_a_line_that_would_not_fit_stays_split(tmp_path: Path):
    """§4.1 pone el techo en 400 caracteres. Sin ese tope, un literal de datos
    de mil entradas se convierte en una sola línea de decenas de miles de
    caracteres: eso ya no es «un repo sin formateador», es un fichero que
    ninguna herramienta del agente sabe leer, y la celda mediría otra cosa."""
    entries = "\n".join(f'    "clave_{index:03d}": {index},' for index in range(60))
    path = write(tmp_path, "TABLE = {\n" + entries + "\n}\npair = (\n    1,\n    2,\n)\n")

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert max(len(line) for line in source.splitlines()) <= 400
    # y el tope es local: lo que sí cabe se junta igual.
    assert "pair=(1,2,)" in source


def test_a_comment_inside_the_brackets_keeps_the_lines_split(tmp_path: Path):
    """Juntar las líneas de un literal que lleva un comentario dentro obligaría
    a tirar el comentario, que es A4: antes de mezclar las dos celdas, A3 se
    queda sin juntar esa expresión."""
    path = write(
        tmp_path,
        'values = {\n    # el orden importa\n    "a": 1,\n    "b": 2,\n}\n',
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert "# el orden importa" in source
    assert len(source.splitlines()) > 1


def test_keywords_split_across_lines_keep_the_space_they_need(tmp_path: Path):
    """Al juntar renglones el espacio que separaba dos palabras desaparecería:
    `a if a not in b else b` en cuatro líneas se convertiría en `aifanotinb`."""
    path = write(
        tmp_path,
        "def f(a, b):\n    return (\n        a\n        if a\n        not in b\n        else b\n    )\n",
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    namespace: dict = {}
    exec(compile(source, "core.py", "exec"), namespace)
    assert namespace["f"](1, [2]) == 1
    assert namespace["f"](1, [1]) == [1]


def test_the_program_is_the_same_tree_afterwards(tmp_path: Path):
    """A3 solo mueve espacio, así que el árbol de sintaxis tiene que salir
    idéntico. Es la verificación de equivalencia más fuerte que cabe en un test
    unitario: si al juntar líneas se cuela un cambio de programa, aquí se ve."""
    import ast

    source = (
        "import os\n"
        "\n"
        "\n"
        "class Widget:\n"
        "    def render(\n"
        "        self,\n"
        "        rows,\n"
        "    ):\n"
        "        out = [\n"
        "            row\n"
        "            for row in rows\n"
        "            if row not in (None, '')\n"
        "        ]\n"
        "        with open(\n"
        "            os.devnull\n"
        "        ) as handle:\n"
        "            handle.write(\n"
        "                ','.join(out)\n"
        "            )\n"
        "        return {\n"
        "            'rows': out,\n"
        "            'count': len(\n"
        "                out\n"
        "            ),\n"
        "        }\n"
    )
    path = write(tmp_path, source)

    a3_format.apply(tmp_path)

    transformed = path.read_text(encoding="utf-8")
    assert ast.dump(ast.parse(transformed)) == ast.dump(ast.parse(source))


def test_a_split_signature_with_annotations_ends_on_one_line(tmp_path: Path):
    """A3 corre sola en su celda, o sea sobre código que todavía tiene tipos: la
    anotación de retorno no sabe escribirse suelta (su símbolo es `->` aquí y
    `:` en un parámetro), y medirla sin cuidado reventaba la transformación
    entera en cualquier fichero con firma anotada."""
    path = write(
        tmp_path,
        "def rate(\n    value: int,\n    factor: int = 2,\n) -> int:\n    return value * factor\n",
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert len(source.splitlines()) == 2


def test_the_indentation_counts_against_the_line_budget(tmp_path: Path):
    """El techo de 400 es sobre el renglón que acaba en el fichero, y la sangría
    de un método anidado son ocho caracteres de ese renglón. Medir solo la
    expresión deja pasar líneas por encima del techo justo en el código más
    anidado, que es donde el agente ya lo tiene peor."""
    # 388 caracteres: repartido cabe en el techo (12 de sangría + 388 = 400) y
    # junto no (8 de sangría + `pair=(` + 388 + `)` = 403).
    name = "v" + "x" * 387
    path = write(
        tmp_path,
        "class C:\n    def m(self):\n        pair = (\n            "
        + name
        + "\n        )\n        return pair\n",
    )

    a3_format.apply(tmp_path)

    source = path.read_text(encoding="utf-8")
    compile(source, "core.py", "exec")
    assert max(len(line) for line in source.splitlines()) <= 400
