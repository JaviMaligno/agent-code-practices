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
