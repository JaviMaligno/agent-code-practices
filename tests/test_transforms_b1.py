from pathlib import Path

from acp.transforms import b1_cohesion


def build(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "billing.py").write_text(
        "TAX = 0.21\n"
        "\n"
        "\n"
        "def rate(amount):\n"
        "    return amount * TAX\n"
        "\n"
        "\n"
        "def total(rows):\n"
        "    return sum(rate(row) for row in rows)\n",
        encoding="utf-8",
    )
    (pkg / "report.py").write_text(
        "def render(rows):\n"
        "    return ', '.join(str(row) for row in rows)\n"
        "\n"
        "\n"
        "def header():\n"
        "    return 'informe'\n",
        encoding="utf-8",
    )


def test_the_definitions_end_up_somewhere_else(tmp_path: Path):
    build(tmp_path)

    result = b1_cohesion.apply(tmp_path, seed=1)

    assert result.symbol_moves, "no movió ninguna definición"
    origen = {key.rsplit(".", 1)[0] for key in result.symbol_moves}
    destino = set(result.symbol_moves.values())
    assert origen != destino or any(
        key.rsplit(".", 1)[0] != value for key, value in result.symbol_moves.items()
    )


def test_the_number_of_files_does_not_change(tmp_path: Path):
    """B1 rompe la organización SIN tocar el tamaño; el tamaño es B5. Si las dos
    cosas cambian a la vez, ninguna de las dos celdas es atribuible (§4.2)."""
    build(tmp_path)
    antes = sorted(p.name for p in (tmp_path / "pkg").glob("*.py"))

    b1_cohesion.apply(tmp_path, seed=1)

    assert sorted(p.name for p in (tmp_path / "pkg").glob("*.py")) == antes


def test_the_code_still_runs(tmp_path: Path):
    """Lo que una definición necesita —una constante del módulo, otra función—
    tiene que viajar con ella o importarse en el destino, o el primer uso da
    NameError."""
    build(tmp_path)

    b1_cohesion.apply(tmp_path, seed=1)

    import subprocess
    import sys

    proceso = subprocess.run(
        [sys.executable, "-c",
         "import pkg.billing, pkg.report; "
         "mods = [pkg.billing, pkg.report]; "
         "f = [getattr(m, 'total') for m in mods if hasattr(m, 'total')][0]; "
         "print(f([100]))"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proceso.returncode == 0, proceso.stderr


def test_the_same_seed_produces_the_same_tree(tmp_path: Path):
    """Sin esto, dos corridas de la misma celda no son la misma condición y los
    seeds del 2×2 dejan de ser comparables (§5.4.4)."""
    build(tmp_path / "una")
    build(tmp_path / "otra")

    primera = b1_cohesion.apply(tmp_path / "una", seed=7)
    segunda = b1_cohesion.apply(tmp_path / "otra", seed=7)

    assert primera.symbol_moves == segunda.symbol_moves


def test_a_different_seed_produces_a_different_tree(tmp_path: Path):
    build(tmp_path / "una")
    build(tmp_path / "otra")

    primera = b1_cohesion.apply(tmp_path / "una", seed=1)
    segunda = b1_cohesion.apply(tmp_path / "otra", seed=2)

    assert primera.symbol_moves != segunda.symbol_moves


def test_a_moved_symbol_survives_in_the_manifest_next_to_the_renaming(tmp_path: Path):
    """La clave de `symbol_moves` es la del mapa de identidad: el nombre
    ORIGINAL. B1 solo puede publicarla si ve el árbol antes de que A2 renombre,
    y si no la publica el símbolo se cae del manifiesto sin que nada lo diga
    —exactamente el fallo en verde de la fase 2 (§5.4.2)."""
    import json

    from acp.cli import manifest_path_for, transform_repo

    source = tmp_path / "repo"
    build(source)

    destination = transform_repo(source, ["A2", "B1"], tmp_path / "work")

    manifest = json.loads(manifest_path_for(destination).read_text(encoding="utf-8"))
    movidas = {
        clave
        for clave, sitio in manifest["symbols"].items()
        if sitio["path"] != f"{clave.rsplit('.', 2)[0]}/{clave.rsplit('.', 2)[1]}.py"
    }
    assert movidas, "ningún símbolo movido llegó al manifiesto"


def run(root: Path, code: str):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True
    )


def test_a_relative_import_of_a_moved_symbol_is_redirected(tmp_path: Path):
    """Los puntos de `from ..a import alpha` se cuentan desde el paquete que
    CONTIENE al fichero, no desde el raíz. Contarlos mal no da error de sintaxis
    ni de parseo: el nombre no se encuentra en el diccionario, el import se
    queda apuntando al módulo de antes y el repositorio deja de arrancar
    entero —pint, con la suite de 2.024 tests en cero—."""
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (pkg / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "user.py").write_text(
        "from ..a import alpha\n\n\ndef use():\n    return alpha()\n", encoding="utf-8"
    )

    resultado = b1_cohesion.apply(tmp_path, seed=4)

    assert "pkg.a.alpha" in resultado.symbol_moves, "el caso no se ejerce si nada se mueve"
    proceso = run(tmp_path, "import pkg.sub.user as u; print(u.use())")
    assert proceso.returncode == 0, proceso.stderr
    assert proceso.stdout.strip() == "1"


def test_a_definition_that_shadows_a_builtin_is_followed_when_it_moves(tmp_path: Path):
    """`format` es un builtin y a la vez el nombre de la función del módulo. Si
    se muda y no se sigue, la llamada que queda atrás se resuelve EN SILENCIO
    contra el builtin: no hay NameError, hay otro resultado. Así salieron 43
    doctests de python-stdnum en rojo con el árbol importando perfectamente."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    # `render` se queda clavado en su módulo —está en su `__all__`— para que
    # la llamada que hace a `format` sea de verdad una referencia abandonada.
    (pkg / "a.py").write_text(
        "__all__ = ['render']\n"
        "\n"
        "\n"
        "def format(value):\n"
        "    return '<%s>' % value\n"
        "\n"
        "\n"
        "def render(value):\n"
        "    return format(value)\n",
        encoding="utf-8",
    )
    (pkg / "b.py").write_text("def other():\n    return 0\n", encoding="utf-8")

    resultado = b1_cohesion.apply(tmp_path, seed=2)

    assert "pkg.a.format" in resultado.symbol_moves, "el caso no se ejerce si no se mueve"
    proceso = run(tmp_path, "import pkg.a; print(pkg.a.render(3))")
    assert proceso.returncode == 0, proceso.stderr
    assert proceso.stdout.strip() == "<3>"


def test_a_doctest_of_the_module_keeps_finding_what_moved_away(tmp_path: Path):
    """Un doctest es suite y corre con el espacio de nombres de su módulo, pero
    vive dentro de una cadena: el análisis de nombres libres lo atraviesa sin
    verlo y la definición se muda dejando atrás ejemplos que la llamaban."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text(
        '"""Modulo.\n'
        "\n"
        ">>> compact('1 2')\n"
        "'12'\n"
        '"""\n'
        "\n"
        "\n"
        "def compact(value):\n"
        "    return value.replace(' ', '')\n",
        encoding="utf-8",
    )
    (pkg / "b.py").write_text("def other():\n    return 0\n", encoding="utf-8")

    resultado = b1_cohesion.apply(tmp_path, seed=2)

    assert "pkg.a.compact" in resultado.symbol_moves, "el caso no se ejerce si no se mueve"
    proceso = run(
        tmp_path,
        "import doctest, sys, pkg.a; sys.exit(doctest.testmod(pkg.a).failed)",
    )
    assert proceso.returncode == 0, proceso.stdout + proceso.stderr


def test_what_only_exists_for_the_type_checker_travels_with_its_guard(tmp_path: Path):
    """Un alias declarado bajo `if TYPE_CHECKING` no existe en ejecución.
    Escribirlo desnudo en el destino es un ImportError al cargar —el repositorio
    entero caído, no una dosis más baja— y por eso viaja con la guarda."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    cabecera = (
        "from __future__ import annotations\n"
        "\n"
        "from typing import TYPE_CHECKING\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    from collections.abc import Sequence\n"
        "\n"
        "    Tabla = Sequence[int]\n"
    )
    (pkg / "a.py").write_text(
        cabecera + "\n\ndef checksum(rows: Tabla | None = None) -> int:\n"
        "    return len(rows or ())\n",
        encoding="utf-8",
    )
    (pkg / "b.py").write_text(cabecera + "\n\ndef other() -> int:\n    return 0\n", encoding="utf-8")

    resultado = b1_cohesion.apply(tmp_path, seed=2)

    assert "pkg.a.checksum" in resultado.symbol_moves, "el caso no se ejerce si no se mueve"
    proceso = run(tmp_path, "import pkg.a, pkg.b; print('ok')")
    assert proceso.returncode == 0, proceso.stderr


def test_the_suite_of_the_repository_is_not_redistributed(tmp_path: Path):
    """En un fichero de test, dónde vive una definición ES semántica de pytest:
    una fixture solo la ven los tests de su módulo, y `conftest.py` es el sitio
    del que pytest lee. Repartir ahí dentro dejó la colecta de pint en cero."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (pkg / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    (pkg / "test_uno.py").write_text("def test_uno():\n    assert True\n", encoding="utf-8")
    (pkg / "test_dos.py").write_text("def test_dos():\n    assert True\n", encoding="utf-8")

    resultado = b1_cohesion.apply(tmp_path, seed=1)

    tocados = set(resultado.symbol_moves) | set(resultado.symbol_moves.values())
    assert not any("test_" in nombre for nombre in tocados), resultado.symbol_moves


def test_a_definition_does_not_move_into_a_module_with_heavier_requirements(tmp_path: Path):
    """`pint/matplotlib.py` importa un extra opcional. Mudar ahí dentro una
    clase del núcleo convirtió un `import pint` que funcionaba en un
    ModuleNotFoundError para quien no tuviera el extra."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (pkg / "extra.py").write_text(
        "import un_paquete_que_no_esta\n\n\ndef beta():\n    return un_paquete_que_no_esta\n",
        encoding="utf-8",
    )

    resultado = b1_cohesion.apply(tmp_path, seed=1)

    assert "pkg.extra" not in resultado.symbol_moves.values(), resultado.symbol_moves


def test_the_plan_says_how_much_dose_is_lost_and_why(tmp_path: Path):
    """La dosis perdida es un resultado del experimento: si en un repo real casi
    nada se puede mover, B1 mide mucho menos de lo que el spec supone y eso hay
    que poder decirlo con un número delante, no deducirlo de un contador a cero."""
    build(tmp_path)

    informe = b1_cohesion.plan(tmp_path, seed=1)

    assert informe.candidates == 4
    assert sum(informe.excluded.values()) + len(informe.symbol_moves) == informe.candidates


def test_nothing_moves_into_a_module_that_swaps_itself_in_sys_modules(tmp_path: Path):
    """Lo que enseñó python-stdnum y ningún fixture había enseñado.

    `stdnum/iso9362.py` es un alias de compatibilidad: avisa del renombrado y
    termina con `sys.modules[__name__] = stdnum.bic`. El nombre del módulo deja
    de apuntar a ESE espacio de nombres, así que una definición mudada ahí no
    existe para nadie: B1 movió `to_isin` de `cusip.py` a `iso9362.py`, reescribió
    el import a `from stdnum.iso9362 import to_isin`, y la colecta entera murió
    con `ImportError: cannot import name 'to_isin' from 'stdnum.bic'` —420 tests
    a cero, la celda ilegible—.

    B5 ya se guardaba de esto (`_reaches_into_the_import_system`); B1 no, porque
    el fichero que lo hace no tiene nada raro para un análisis estático: define
    imports y ejecuta una asignación. Un módulo así no puede ni dar ni recibir.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "nuevo.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n", encoding="utf-8"
    )
    (pkg / "otro.py").write_text(
        "def gamma():\n    return 3\n\n\ndef delta():\n    return 4\n", encoding="utf-8"
    )
    (pkg / "viejo.py").write_text(
        "import sys\n"
        "\n"
        "import pkg.nuevo\n"
        "\n"
        "sys.modules[__name__] = pkg.nuevo\n",
        encoding="utf-8",
    )

    resultado = b1_cohesion.apply(tmp_path, seed=1)

    assert "pkg.viejo" not in resultado.symbol_moves.values(), resultado.symbol_moves


def test_a_module_that_swaps_itself_in_sys_modules_keeps_its_own_definitions(
    tmp_path: Path,
):
    """La otra mitad, por simetría: lo que ese módulo defina tampoco se muda.

    Sacarle una definición y reescribir los imports que la buscaban dejaría a
    quien la pidiera por el nombre viejo mirando el espacio de nombres del
    módulo suplantador, que es donde ya no está.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "nuevo.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (pkg / "otro.py").write_text("def gamma():\n    return 3\n", encoding="utf-8")
    (pkg / "viejo.py").write_text(
        "import sys\n"
        "\n"
        "import pkg.nuevo\n"
        "\n"
        "\n"
        "def epsilon():\n"
        "    return 5\n"
        "\n"
        "\n"
        "sys.modules[__name__] = pkg.nuevo\n",
        encoding="utf-8",
    )

    resultado = b1_cohesion.apply(tmp_path, seed=1)

    assert "pkg.viejo.epsilon" not in resultado.symbol_moves, resultado.symbol_moves


def test_a_definition_does_not_move_between_modules_that_disagree_on_future_annotations(
    tmp_path: Path,
):
    """Lo que enseñó sqlglot, y la segunda vez que B5 ya se guardaba y B1 no.

    `sqlglot/errors.py` empieza con `from __future__ import annotations` (PEP
    563), así que sus anotaciones no se evalúan nunca; `sqlglot/trie.py` no lo
    tiene. B1 mudó `ParseError` de una a otra y su propia anotación de retorno
    —`-> ParseError`, que se refiere a la clase que se está definiendo— pasó a
    evaluarse al crear la clase, cuando el nombre todavía no existe: `import
    sqlglot` murió con `NameError: name 'ParseError' is not defined`.

    No basta con mirar los nombres libres de la definición, que es lo que B1
    hacía: aquí el nombre que falta es el SUYO. La regla que sí lo tapa es la que
    B5 ya usaba —los dos ficheros tienen que coincidir en `from __future__`—, y
    se exige en las dos direcciones: al revés, una anotación que se evaluaba deja
    de hacerlo y `__annotations__` empieza a devolver cadenas, que es un cambio
    de comportamiento silencioso y por tanto peor.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "perezoso.py").write_text(
        "from __future__ import annotations\n"
        "\n"
        "\n"
        "class Fallo(Exception):\n"
        "    def clonar(self) -> Fallo:\n"
        "        return self\n",
        encoding="utf-8",
    )
    (pkg / "estricto.py").write_text(
        "def ayuda():\n    return 1\n\n\ndef otra():\n    return 2\n", encoding="utf-8"
    )

    resultado = b1_cohesion.apply(tmp_path, seed=1)

    assert resultado.symbol_moves.get("pkg.perezoso.Fallo") != "pkg.estricto"
    assert resultado.symbol_moves.get("pkg.estricto.ayuda") != "pkg.perezoso"

    import subprocess
    import sys

    proceso = subprocess.run(
        [sys.executable, "-c", "import pkg.perezoso, pkg.estricto; print('ok')"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proceso.returncode == 0, proceso.stderr


def test_a_cycle_the_repository_already_survives_is_not_a_licence_to_add_edges(
    tmp_path: Path,
):
    """La segunda cosa que sqlglot enseñó y que B5 ya había aprendido.

    B1 se daba permiso para añadir aristas DENTRO de un grupo que ya se importaba
    en círculo, con el argumento de que el intérprete ya sobrevive a ese enredo.
    No sobrevive a cualquiera: un círculo se aguanta por el ORDEN en que cada
    fichero termina de cargarse, y mudar una definición cambia ese orden. Sobre
    sqlglot, `import sqlglot` moría con `ImportError: cannot import name
    'Dialect' from partially initialized module`.

    Aquí está el mismo enredo en pequeño: `uno → dos → tres → uno` se aguanta
    porque el que cierra el círculo solo se queda con el módulo a medias y no
    mira dentro. En cuanto `ayuda` se muda de `tres` a `uno`, el `from ... import
    ayuda` de `dos` corre mientras `uno` va por su primera línea. El seed está
    fijado porque este reparto concreto es el que lo enseña; los otros siete que
    se probaron pasaban, y por eso hacía falta buscarlo.

    La tolerancia se puso cuando los imports bajo `if TYPE_CHECKING` contaban
    como aristas y dejaban a pint en dosis cero. Eso está arreglado por otro
    lado —no se ejecutan, no son aristas—, así que hoy la tolerancia no compra
    dosis: quitarla cuesta UN símbolo en sqlglot (230→229) y ninguno en los
    otros tres finalistas.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from pkg.uno import arranca\n", encoding="utf-8")
    (pkg / "uno.py").write_text(
        "import pkg.dos\n\n\ndef arranca():\n    return pkg.dos.dos_usa()\n",
        encoding="utf-8",
    )
    (pkg / "dos.py").write_text(
        "from pkg.tres import ayuda\n\n\ndef dos_usa():\n    return ayuda()\n",
        encoding="utf-8",
    )
    (pkg / "tres.py").write_text(
        "import pkg.uno\n"
        "\n"
        "\n"
        "def ayuda():\n"
        "    return 3\n"
        "\n"
        "\n"
        "def otra():\n"
        "    return pkg.uno.arranca\n",
        encoding="utf-8",
    )
    # Dos hermanos fuera del círculo: sin ellos este test pasaría igual el día
    # que B1 dejara de mover nada, que es la forma de romperlo sin verlo.
    (pkg / "alfa.py").write_text("def alfa():\n    return 1\n", encoding="utf-8")
    (pkg / "beta.py").write_text("def beta():\n    return 2\n", encoding="utf-8")

    resultado = b1_cohesion.apply(tmp_path, seed=3)

    assert resultado.symbol_moves, "no movió nada: el test no distingue nada"

    import subprocess
    import sys

    proceso = subprocess.run(
        [sys.executable, "-c", "import pkg; print(pkg.arranca())"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proceso.returncode == 0, proceso.stderr
    assert proceso.stdout.strip() == "3"


def test_the_boot_script_of_a_package_neither_gives_nor_receives(tmp_path: Path):
    """La tercera que sqlglot enseñó, y la tercera que B5 ya sabía.

    `sqlglot/__main__.py` es lo que corre `python -m sqlglot`: hace `import
    sqlglot` y lee `sqlglot.__version__` en el nivel de módulo. Nadie lo
    importa, así que ese código corre cuando el paquete ya está entero. En
    cuanto B1 le manda una definición, alguien lo importa —el fichero que la
    perdió—, su argparse y su lectura de atributos pasan a correr en mitad de la
    carga, y `import sqlglot` muere con `AttributeError: module 'sqlglot' has no
    attribute '__version__'`.

    Que no reciba tampoco arregla la mitad de que no dé: sacarle una definición
    obligaría a `python -m paquete` a importar el módulo destino, con el orden
    cambiado. Es un fichero que no es parte de la librería y se queda fuera del
    reparto entero.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from pkg.nucleo import trabaja\n\nVERSION = '1'\n", encoding="utf-8"
    )
    (pkg / "nucleo.py").write_text(
        "def trabaja():\n    return 1\n\n\ndef otra():\n    return 2\n", encoding="utf-8"
    )
    (pkg / "util.py").write_text(
        "def ayuda():\n    return 3\n\n\ndef mas():\n    return 4\n", encoding="utf-8"
    )
    (pkg / "__main__.py").write_text(
        "import pkg\n"
        "\n"
        "ETIQUETA = pkg.VERSION\n"
        "\n"
        "\n"
        "def principal():\n"
        "    return ETIQUETA\n",
        encoding="utf-8",
    )

    resultado = b1_cohesion.apply(tmp_path, seed=1)

    assert resultado.symbol_moves, "no movió nada: el test no distingue nada"
    assert "pkg.__main__" not in resultado.symbol_moves.values(), resultado.symbol_moves
    assert not [key for key in resultado.symbol_moves if key.startswith("pkg.__main__.")]

    import subprocess
    import sys

    proceso = subprocess.run(
        [sys.executable, "-c", "import pkg; print(pkg.trabaja())"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proceso.returncode == 0, proceso.stderr
