from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from acp.metrics import coupling, domain, readability, runtime_typing, size
from acp.models import RepoProfile
from acp.report import comparison_table, render_profile
from acp.suite import run_suite_in_docker, run_suite_in_venv
from acp.symbols import apply_renames, build_symbol_map
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


def transform_repo(source: Path, transform_ids: list[str], destination: Path) -> Path:
    """Aplica transformaciones sobre una copia y deja constancia de qué se hizo.

    El manifiesto no es decoración: sin procedencia registrada —qué se aplicó y
    dónde acabó cada símbolo— las métricas de localización de la campaña no se
    pueden interpretar (§5.4.1, §5.4.2).
    """
    unknown = [name for name in transform_ids if name not in TRANSFORMS]
    if unknown:
        raise ValueError(f"transformación desconocida: {', '.join(unknown)}")

    symbols = build_symbol_map(source)
    root = copy_tree(source, destination)

    renames: dict[str, str] = {}
    for name in transform_ids:
        result = TRANSFORMS[name](root)
        renames.update(result.renames)

    symbols = apply_renames(symbols, renames)
    manifest = {
        "applied": transform_ids,
        "renames": renames,
        "symbols": {key: asdict(location) for key, location in symbols.items()},
    }
    (root / "acp-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
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

    args = parser.parse_args(argv)

    if args.command == "transform":
        # Aquí `--out` no es un directorio de informes sino el árbol destino, y
        # la copia exige que no exista: crearlo antes la deja sin sitio.
        destination = transform_repo(args.path, args.apply.split(","), args.out)
        print(f"escrito {destination}")
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
