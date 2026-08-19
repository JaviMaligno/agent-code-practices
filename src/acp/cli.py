from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from acp.metrics import coupling, domain, readability, runtime_typing, size
from acp.models import RepoProfile
from acp.report import comparison_table, render_profile
from acp.suite import run_suite_in_docker, run_suite_in_venv
from acp.symbols import build_symbol_map, relocate_symbols
from acp.transforms import TRANSFORMS
from acp.transforms.base import copy_tree

# Los dos se conservan a propósito (§2 del spec): con contenedor el aislamiento
# es de sistema, y sin él es solo de dependencias, pero está verificado y sirve
# donde Docker no se puede usar.
RUNNERS = {"docker": run_suite_in_docker, "venv": run_suite_in_venv}
DEFAULT_RUNNER = "docker"


def suite_runner(name: str | None):
    """Ejecutor de suites por nombre. Docker por defecto: es el de la campaña."""
    key = name or DEFAULT_RUNNER
    if key not in RUNNERS:
        raise ValueError(f"ejecutor desconocido: {key}. Opciones: {', '.join(RUNNERS)}")
    return RUNNERS[key]


def profile_repo(
    root: Path,
    name: str,
    run_suite: bool = True,
    runner: str | None = None,
    prepare: str | None = None,
) -> RepoProfile:
    profile = RepoProfile(
        name=name,
        size=size.measure(root),
        readability=readability.measure(root),
        runtime_typing=runtime_typing.measure(root),
        coupling=coupling.measure(root),
        domain=domain.measure(root),
    )
    if run_suite:
        run = suite_runner(runner)
        profile.suite = run(root, prepare=prepare) if prepare else run(root)
    return profile


# A3 va la última de la familia A porque es la única que otra puede deshacer: A1
# y A2 reconstruyen nodos (`x: int = 1` sale de A1 como una asignación nueva) y
# LibCST los escribe con el espaciado por defecto, así que cualquier cosa que
# corra después de A3 devuelve parte del formato que A3 había quitado. El resto
# del orden se fija por reproducibilidad: la condición tiene que ser la misma
# aunque los flags lleguen en otro orden.
#
# B3 antes que B4 por una dependencia real, no por gusto: B3 mira la suite para
# decidir si el README es contrato del repo —holidays verifica en sus tests que
# las tablas del README están completas— y B4 se lleva la suite fuera del árbol.
# Al revés, B3 no encuentra ningún test, vacía un README que la suite comprueba,
# y el fallo aparece en la corrida de validación —donde la suite sí existe— en
# vez de en el árbol, que es el sitio donde nadie lo busca.
#
# B2 antes que las dos, y por otra dependencia real: aplana el árbol y reescribe
# los imports de todo el repo, la suite incluida (§4.3.1). B4 se lleva la suite
# fuera del árbol, así que después de B4 ya no hay nada de eso que reescribir y
# los tests guardados se quedan importando rutas que B2 acaba de borrar. El
# fallo aparecería en la corrida de validación —la única que ejecuta esa suite—
# y se leería como un repo roto por el agente en vez de como un orden mal puesto.
# B1 va la PRIMERA, y por una razón que no se ve hasta que se lee el manifiesto.
# Es la única transformación cuya salida se indexa por nombre de símbolo, y la
# clave que el mapa de identidad entiende es el nombre ORIGINAL (§5.4.2). Corrida
# después de A2, B1 solo puede anunciar `pkg.core.f7`, que no es la clave de
# nadie: los símbolos movidos se caen del manifiesto y la métrica de
# localización se queda sin datos, en verde —el fallo exacto de la fase 2—.
# Delante de A2 las claves son las buenas, y `renames` ya le dice a
# `relocate_symbols` por qué nombre preguntar en el destino.
# Y delante de B2 por una segunda razón, esta experimental: B1 reparte dentro de
# cada directorio, así que si B2 aplanara antes, la dosis de B1 dependería de si
# B2 está en la celda y ninguna de las dos sería atribuible (§4.2).
CANONICAL_ORDER = ("B1", "A1", "A2", "A4", "A3", "B2", "B3", "B4")


def _application_order(transform_ids: list[str]) -> list[str]:
    """El orden en que se aplican, que no tiene por qué ser el que se pidió."""
    rank = {name: index for index, name in enumerate(CANONICAL_ORDER)}
    # `sorted` es estable: lo que aún no tenga sitio asignado —la familia B— se
    # queda al final en el orden en que llegó.
    return sorted(transform_ids, key=lambda name: rank.get(name, len(rank)))


def manifest_path_for(destination: Path, manifest: Path | None = None) -> Path:
    """Dónde vive el manifiesto de un árbol transformado: fuera del árbol.

    Por defecto, hermano del árbol y con su nombre delante (`work/` →
    `work.acp-manifest.json`): la procedencia solo sirve si se sabe a qué
    condición pertenece, y el nombre es lo único que las ata cuando la campaña
    acumula decenas de árboles en el mismo directorio.
    """
    if manifest is not None:
        return manifest
    return destination.with_name(f"{destination.name}.acp-manifest.json")


def _reject_manifest_inside_the_tree(destination: Path, manifest: Path) -> None:
    """El manifiesto dentro del árbol es entregarle al agente lo que se le mide.

    Lleva el diccionario completo original→opaco de A2 y el fichero y rango de
    cada símbolo: un `ls` de la raíz le da la clave del renombrado y, de paso,
    la respuesta de la métrica de localización (§5.4.2). Y como el árbol de
    referencia no lo tiene, sería además una diferencia entre condición y
    control que no está en el diseño. Se comprueba antes de copiar: quien se
    equivoque de ruta tiene que enterarse antes de gastar la corrida.
    """
    tree = destination.resolve()
    target = manifest.resolve()
    if target == tree or tree in target.parents:
        raise ValueError(
            f"el manifiesto caería dentro del árbol transformado ({target}): es "
            "procedencia del experimento (§5.4.1), no contenido del repositorio"
        )


def transform_repo(
    source: Path,
    transform_ids: list[str],
    destination: Path,
    manifest: Path | None = None,
) -> Path:
    """Aplica transformaciones sobre una copia y deja constancia de qué se hizo.

    El manifiesto no es decoración: sin procedencia registrada —qué se aplicó y
    dónde acabó cada símbolo— las métricas de localización de la campaña no se
    pueden interpretar (§5.4.1, §5.4.2). Pero vive **fuera** del árbol: lo que
    se copia en `destination` es el repositorio que explora el agente, y ahí
    dentro no entra nada que no estuviera en el original.
    """
    unknown = [name for name in transform_ids if name not in TRANSFORMS]
    if unknown:
        raise ValueError(f"transformación desconocida: {', '.join(unknown)}")

    manifest_destination = manifest_path_for(destination, manifest)
    _reject_manifest_inside_the_tree(destination, manifest_destination)

    symbols = build_symbol_map(source)
    root = copy_tree(source, destination)

    renames: dict[str, str] = {}
    # Los movimientos se acumulan como los renombrados porque el mapa de
    # identidad los necesita: la familia B mueve símbolos entre módulos y sin
    # esto se caerían todos del mapa en cuanto una transformación toque el árbol.
    moves: dict[str, str] = {}
    # Y los movimientos de símbolo suelto aparte, porque describen algo que
    # `moves` no puede: B1 reparte definiciones del mismo módulo entre ficheros
    # distintos. Sin acumularlas aquí, todas se caerían del mapa y en verde.
    symbol_moves: dict[str, str] = {}
    for name in _application_order(transform_ids):
        result = TRANSFORMS[name](root)
        renames.update(result.renames)
        moves.update(result.moves)
        symbol_moves.update(result.symbol_moves)

    # El mapa describe el árbol que el agente va a ver: rangos y nombres se
    # leen del árbol transformado, no se deducen del diccionario de renombrados.
    # `renames` viaja solo para saber por qué nombre preguntar cuando un símbolo
    # viajó solo y A2 ya lo había renombrado; lo que se publica sale del código.
    symbols = relocate_symbols(
        symbols, root, moves, symbol_moves=symbol_moves, renames=renames
    )
    # El contenido, separado del sitio donde va: `manifest` ya es la ruta.
    provenance = {
        # Lo que se pidió, en el orden en que se pidió: el manifiesto es la
        # procedencia de la condición, no la traza de la implementación.
        "applied": transform_ids,
        "renames": renames,
        "symbols": {key: asdict(location) for key, location in symbols.items()},
    }
    # `--manifest` puede apuntar a un directorio de procedencia que aún no
    # existe; el árbol ya está escrito y perderlo por un mkdir sería absurdo.
    manifest_destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_destination.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile_parser = subparsers.add_parser("profile", help="perfila un repo candidato")
    profile_parser.add_argument("path", type=Path)
    profile_parser.add_argument("--name", required=True)
    profile_parser.add_argument("--out", type=Path, default=Path("out"))
    profile_parser.add_argument("--no-suite", action="store_true")
    profile_parser.add_argument(
        "--runner", choices=sorted(RUNNERS), default=DEFAULT_RUNNER,
        help="dónde se ejecuta la suite del candidato",
    )
    profile_parser.add_argument(
        "--prepare", default=None,
        help="paso de build propio del repo que su suite necesita, p. ej. generar traducciones",
    )

    table_parser = subparsers.add_parser("table", help="tabla comparativa de las fichas existentes")
    table_parser.add_argument("--out", type=Path, default=Path("out"))

    transform_parser = subparsers.add_parser("transform", help="transforma una copia del repo")
    transform_parser.add_argument("path", type=Path)
    transform_parser.add_argument("--apply", required=True, help="p. ej. A1,A4")
    transform_parser.add_argument("--out", type=Path, required=True)
    transform_parser.add_argument(
        "--manifest", type=Path, default=None,
        help="dónde se escribe la procedencia; por defecto, hermano del árbol. "
             "Nunca dentro de él: es material del experimento, no del repositorio",
    )

    args = parser.parse_args(argv)

    if args.command == "transform":
        # Aquí `--out` no es un directorio de informes sino el árbol destino, y
        # la copia exige que no exista: crearlo antes la deja sin sitio.
        destination = transform_repo(
            args.path, args.apply.split(","), args.out, manifest=args.manifest
        )
        # Se anuncian los dos porque son dos artefactos separados a propósito:
        # quien recoja la corrida tiene que saber dónde quedó la procedencia.
        print(f"escrito {destination}")
        print(f"escrito {manifest_path_for(args.out, args.manifest)}")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)

    if args.command == "profile":
        profile = profile_repo(
            args.path, name=args.name, run_suite=not args.no_suite, runner=args.runner,
            prepare=args.prepare,
        )
        destination = args.out / f"{args.name}.md"
        destination.write_text(render_profile(profile), encoding="utf-8")
        (args.out / f"{args.name}.json").write_text(
            json.dumps(profile.to_flat_dict(), indent=2), encoding="utf-8"
        )
        print(f"escrito {destination}")
        return 0

    if args.command == "table":
        profiles = []
        for path in sorted(args.out.glob("*.json")):
            flat = json.loads(path.read_text(encoding="utf-8"))
            profiles.append(_profile_from_flat(flat))
        destination = args.out / "comparison.md"
        destination.write_text(comparison_table(profiles), encoding="utf-8")
        print(f"escrito {destination}")
        return 0

    return 1


def _profile_from_flat(flat: dict) -> RepoProfile:
    from acp.models import (
        CouplingMetrics,
        DomainMetrics,
        ReadabilityMetrics,
        RuntimeTypingMetrics,
        SizeMetrics,
        SuiteMetrics,
    )

    def group(prefix: str) -> dict:
        return {
            key.split(".", 1)[1]: value
            for key, value in flat.items()
            if key.startswith(f"{prefix}.")
        }

    return RepoProfile(
        name=flat["name"],
        size=SizeMetrics(**group("size")),
        readability=ReadabilityMetrics(**group("readability")),
        runtime_typing=RuntimeTypingMetrics(**group("runtime_typing")),
        coupling=CouplingMetrics(**group("coupling")),
        domain=DomainMetrics(**group("domain")),
        suite=SuiteMetrics(**group("suite")),
    )


if __name__ == "__main__":
    raise SystemExit(main())
