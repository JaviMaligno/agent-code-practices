from pathlib import Path

from acp.transforms import b5_size


def build(root: Path, modules: int = 6) -> None:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for index in range(modules):
        (pkg / f"m{index}.py").write_text(
            f"CONST_{index} = {index}\n"
            "\n"
            "\n"
            f"def f{index}(value):\n"
            f"    return value + CONST_{index}\n",
            encoding="utf-8",
        )


def test_the_modules_end_up_in_fewer_files(tmp_path: Path):
    build(tmp_path)
    antes = len(list((tmp_path / "pkg").glob("*.py")))

    b5_size.apply(tmp_path, target_lines=2000)

    assert len(list((tmp_path / "pkg").glob("*.py"))) < antes


def test_a_smaller_target_leaves_more_files(tmp_path: Path):
    """La curva necesita puntos distintos: si 500 y 10.000 producen el mismo
    árbol, no hay curva que medir (§6.3)."""
    build(tmp_path / "pequeno", modules=12)
    build(tmp_path / "grande", modules=12)

    b5_size.apply(tmp_path / "pequeno", target_lines=8)
    b5_size.apply(tmp_path / "grande", target_lines=10000)

    pequeno = len(list((tmp_path / "pequeno" / "pkg").glob("*.py")))
    grande = len(list((tmp_path / "grande" / "pkg").glob("*.py")))
    assert pequeno > grande


def test_the_code_still_runs(tmp_path: Path):
    build(tmp_path)

    b5_size.apply(tmp_path, target_lines=2000)

    import subprocess
    import sys

    proceso = subprocess.run(
        [sys.executable, "-c", "import pkg; print('ok')"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proceso.returncode == 0, proceso.stderr


def test_two_modules_that_define_the_same_name_are_not_merged(tmp_path: Path):
    """Al juntarlos en un fichero, la segunda definición pisa a la primera y el
    programa cambia en silencio. Preferimos concatenar de menos: la dosis baja se
    declara, un repo roto se lee como un agente que fracasa."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("def validate(x):\n    return x\n", encoding="utf-8")
    (pkg / "b.py").write_text("def validate(x):\n    return x * 2\n", encoding="utf-8")

    b5_size.apply(tmp_path, target_lines=10000)

    fuentes = [p.read_text(encoding="utf-8") for p in (tmp_path / "pkg").glob("*.py")]
    assert not any(fuente.count("def validate") > 1 for fuente in fuentes)


def test_the_curve_is_reachable_from_the_command_line(tmp_path: Path):
    """§6.3 pide cuatro puntos, y el CLI llama a cada transformación con la raíz
    y nada más: sin una entrada por techo de líneas, la curva no se puede pedir
    y B5 queda reducida a una celda."""
    from acp.transforms import TRANSFORMS

    assert {"B5-500", "B5-2000", "B5-10000"} <= set(TRANSFORMS)

    build(tmp_path / "corto", modules=12)
    build(tmp_path / "largo", modules=12)
    TRANSFORMS["B5-500"](tmp_path / "corto")
    TRANSFORMS["B5-10000"](tmp_path / "largo")

    assert len(list((tmp_path / "corto" / "pkg").glob("*.py"))) >= len(
        list((tmp_path / "largo" / "pkg").glob("*.py"))
    )


def test_an_absorbed_symbol_survives_in_the_manifest(tmp_path: Path):
    """El anfitrión recibe definiciones nuevas, así que la n-ésima de su módulo
    ya no es la que era y el emparejamiento por posición publicaría el rango del
    vecino bajo la clave de cada símbolo. Si en cambio se cae del mapa, la
    métrica de localización se queda sin datos y en verde (§5.4.2)."""
    import json

    from acp.cli import manifest_path_for, transform_repo

    source = tmp_path / "repo"
    build(source)

    destination = transform_repo(source, ["A2", "B5-2000"], tmp_path / "work")

    manifest = json.loads(manifest_path_for(destination).read_text(encoding="utf-8"))
    assert "pkg.m3.f3" in manifest["symbols"], sorted(manifest["symbols"])
    sitio = manifest["symbols"]["pkg.m3.f3"]
    assert sitio["path"] == "pkg/m0.py", sitio
    fuente = (destination / "pkg" / "m0.py").read_text(encoding="utf-8").splitlines()
    assert fuente[sitio["start"] - 1].startswith(f"def {sitio['current_name']}(")


def write(root: Path, **ficheros: str) -> None:
    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for nombre, fuente in ficheros.items():
        (pkg / f"{nombre}.py").write_text(fuente, encoding="utf-8")


def run(root: Path, code: str):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True
    )


def test_what_one_module_imported_from_the_other_is_not_imported_any_more(tmp_path: Path):
    """El import interno al grupo apunta a un fichero que deja de existir. Es la
    forma en que la mitad de las fusiones de un repo real romperían: el símbolo
    ya está unas líneas más arriba, así que la sentencia sobra."""
    write(
        tmp_path,
        base="VALOR = 7\n\n\ndef leer():\n    return VALOR\n",
        uso="from .base import leer\n\n\ndef doble():\n    return leer() * 2\n",
    )

    resultado = b5_size.apply(tmp_path, target_lines=2000)

    assert resultado.moves == {"pkg.uso": "pkg.base"}
    fuente = (tmp_path / "pkg" / "base.py").read_text(encoding="utf-8")
    assert "from .base import leer" not in fuente
    proceso = run(tmp_path, "import pkg.base as m; print(m.doble())")
    assert proceso.returncode == 0, proceso.stderr
    assert proceso.stdout.strip() == "14"


def test_an_alias_of_an_internal_import_keeps_being_a_name(tmp_path: Path):
    """`from .base import leer as l` no liga `leer`, liga `l`. Quitar la
    sentencia sin más deja un NameError en la primera llamada."""
    write(
        tmp_path,
        base="def leer():\n    return 7\n",
        uso="from .base import leer as l\n\n\ndef doble():\n    return l() * 2\n",
    )

    b5_size.apply(tmp_path, target_lines=2000)

    proceso = run(tmp_path, "import pkg.base as m; print(m.doble())")
    assert proceso.returncode == 0, proceso.stderr
    assert proceso.stdout.strip() == "14"


def test_a_module_that_someone_in_the_group_imports_goes_first(tmp_path: Path):
    """El fichero fundido se lee de arriba abajo al importarse: si el que usa se
    escribe antes que el usado, su import interno se resuelve contra un nombre
    que todavía no existe y el paquete deja de cargarse."""
    write(
        tmp_path,
        alta="from .zeta import base\n\nVALOR = base() + 1\n",
        zeta="def base():\n    return 1\n",
    )

    resultado = b5_size.apply(tmp_path, target_lines=2000)

    # El orden alfabético pone a `alta` primero, que es justo el que no puede
    # ir primero. Sin ordenar por dependencia la fusión se rechaza y el test
    # pasaría sin fundir nada: dosis perdida que se lee como regla cumplida.
    assert resultado.moves, "no fundió nada: la regla no se está ejerciendo"
    fundido = next(iter(set(resultado.moves.values())))
    proceso = run(tmp_path, f"import {fundido}; print('ok')")
    assert proceso.returncode == 0, proceso.stderr


def test_a_module_that_needs_an_optional_extra_is_not_merged_into_the_core(tmp_path: Path):
    """La misma trampa que B1 midió sobre `pint/matplotlib.py`: fundido con un
    módulo del núcleo, el extra opcional pasa a exigírselo a todo el que importe
    el fichero, y un `import pkg` que funcionaba muere con ModuleNotFoundError."""
    write(
        tmp_path,
        core="def alpha():\n    return 1\n",
        extra="import un_paquete_que_no_esta\n\n\ndef beta():\n    return un_paquete_que_no_esta\n",
    )

    resultado = b5_size.apply(tmp_path, target_lines=2000)

    assert resultado.moves == {}, resultado.moves


def test_two_modules_that_disagree_about_future_annotations_are_not_merged(tmp_path: Path):
    """`from __future__ import annotations` es una propiedad del FICHERO: solo
    puede haber uno y vale para todo lo que lleva dentro. Fundir el que lo tiene
    con el que no se lo impone al segundo, y lo que lee anotaciones en ejecución
    empieza a ver cadenas donde esperaba clases."""
    write(
        tmp_path,
        moderno="from __future__ import annotations\n\n\ndef f(x: int) -> int:\n    return x\n",
        clasico="import typing\n\n\ndef g(x: 'int') -> int:\n    return typing.cast(int, x)\n",
    )

    resultado = b5_size.apply(tmp_path, target_lines=2000)

    assert resultado.moves == {}, resultado.moves


def test_a_merge_that_would_close_an_import_cycle_is_refused(tmp_path: Path):
    """Fundir dos módulos contrae dos nodos del grafo en uno, y eso cierra un
    ciclo en cuanto había un camino entre ellos por fuera del grupo. Un ciclo no
    da una dosis rara: mata el `import`."""
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "arriba.py").write_text("VALOR = 1\n", encoding="utf-8")
    (pkg / "abajo.py").write_text(
        "from .sub.medio import puente\n\nOTRO = puente\n", encoding="utf-8"
    )
    (pkg / "sub" / "medio.py").write_text(
        "from ..arriba import VALOR\n\npuente = VALOR\n", encoding="utf-8"
    )

    resultado = b5_size.apply(tmp_path, target_lines=2000)

    assert resultado.moves == {}, resultado.moves
    proceso = run(tmp_path, "import pkg.arriba, pkg.abajo; print('ok')")
    assert proceso.returncode == 0, proceso.stderr


def test_the_module_a_doctest_lives_in_keeps_running_its_examples(tmp_path: Path):
    """La docstring del absorbido deja de ser la docstring del módulo y pasa a
    ser una cadena suelta en medio del fichero, que doctest no recoge. Sus
    ejemplos dejarían de ejecutarse y la suite daría otro número —que es
    exactamente lo que la comprobación de equivalencia mide—.

    El de al lado, con el mismo par de módulos y sin el ejemplo, sí se funde:
    sin él este test pasaría igual el día que B5 dejara de fundir nada."""
    write(
        tmp_path / "con",
        alfa="def alpha():\n    return 1\n",
        zeta='"""Modulo.\n\n>>> beta()\n2\n"""\n\n\ndef beta():\n    return 2\n',
    )
    write(
        tmp_path / "sin",
        alfa="def alpha():\n    return 1\n",
        zeta='"""Modulo."""\n\n\ndef beta():\n    return 2\n',
    )

    con = b5_size.apply(tmp_path / "con", target_lines=2000)
    sin = b5_size.apply(tmp_path / "sin", target_lines=2000)

    assert sin.moves == {"pkg.zeta": "pkg.alfa"}, sin.moves
    assert con.moves == {}, con.moves
    proceso = run(
        tmp_path / "con",
        "import doctest, pkg.zeta; print(doctest.testmod(pkg.zeta).attempted)",
    )
    assert proceso.returncode == 0, proceso.stderr
    assert proceso.stdout.strip() == "1", "el ejemplo del módulo dejó de recogerse"


def test_two_exported_lists_are_added_instead_of_one_hiding_the_other(tmp_path: Path):
    """Dos `__all__` en un fichero y el segundo pisa al primero: lo que el
    anfitrión prometía desaparece sin un solo error."""
    write(
        tmp_path,
        uno="__all__ = ['alpha']\n\n\ndef alpha():\n    return 1\n",
        dos="__all__ = ['beta']\n\n\ndef beta():\n    return 2\n",
    )

    resultado = b5_size.apply(tmp_path, target_lines=2000)

    assert resultado.moves, "no fundió nada: la regla no se está ejerciendo"
    fundido = next(iter(set(resultado.moves.values())))
    proceso = run(tmp_path, f"import {fundido} as m; print(sorted(m.__all__))")
    assert proceso.returncode == 0, proceso.stderr
    assert proceso.stdout.strip() == "['alpha', 'beta']"


def test_a_module_someone_star_imports_stays_where_it_is(tmp_path: Path):
    """Quien hace `from x import *` se queda con los nombres que x tenga en ese
    momento, y no hay import que reescribir porque el nombre no está escrito en
    ninguna parte. sqlglot tiene doce módulos así."""
    write(
        tmp_path,
        publico="def alpha():\n    return 1\n",
        otro="def beta():\n    return 2\n",
        cliente="from .publico import *\n\n\ndef usa():\n    return alpha()\n",
    )

    resultado = b5_size.apply(tmp_path, target_lines=2000)

    assert "pkg.publico" not in resultado.moves, resultado.moves
    proceso = run(tmp_path, "import pkg.cliente as c; print(c.usa())")
    assert proceso.returncode == 0, proceso.stderr


def test_no_definition_is_lost_and_none_is_renamed(tmp_path: Path):
    """Lo que B5 cambia es el tamaño del fichero y nada más. Perder una
    definición al concatenar no da error: da un AttributeError la primera vez
    que alguien la pide, que es a mitad de la corrida del agente."""
    from acp.symbols import build_symbol_map

    build(tmp_path)
    antes = sorted(sitio.current_name for sitio in build_symbol_map(tmp_path).values())

    resultado = b5_size.apply(tmp_path, target_lines=2000)

    assert resultado.moves, "no fundió nada: el invariante no se está ejerciendo"
    despues = sorted(sitio.current_name for sitio in build_symbol_map(tmp_path).values())
    assert despues == antes


def test_nothing_changes_directory(tmp_path: Path):
    """B5 varía el tamaño sin tocar la organización, y B2 al revés (§4.2). Si
    esta además moviera código entre directorios, ninguna de las dos celdas
    sería atribuible; y de paso dejarían de valer los imports relativos y las
    rutas que se cuentan desde `__file__`."""
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    for nombre, cuerpo in (("uno", "alpha"), ("dos", "beta")):
        (pkg / f"{nombre}.py").write_text(f"def {cuerpo}():\n    return 1\n", encoding="utf-8")
    for nombre, cuerpo in (("tres", "gamma"), ("cuatro", "delta")):
        (pkg / "sub" / f"{nombre}.py").write_text(
            f"def {cuerpo}():\n    return 1\n", encoding="utf-8"
        )

    resultado = b5_size.apply(tmp_path, target_lines=10000)

    assert len(resultado.moves) == 2, resultado.moves
    assert all(
        origen.rsplit(".", 1)[0] == destino.rsplit(".", 1)[0]
        for origen, destino in resultado.moves.items()
    ), resultado.moves
