from __future__ import annotations

import argparse
from pathlib import Path

from acp.metrics import coupling, domain, readability, runtime_typing, size
from acp.models import RepoProfile
from acp.report import comparison_table, render_profile
from acp.suite import run_suite_in_docker


def profile_repo(root: Path, name: str, run_suite: bool = True) -> RepoProfile:
    profile = RepoProfile(
        name=name,
        size=size.measure(root),
        readability=readability.measure(root),
        runtime_typing=runtime_typing.measure(root),
        coupling=coupling.measure(root),
        domain=domain.measure(root),
    )
    if run_suite:
        profile.suite = run_suite_in_docker(root)
    return profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile_parser = subparsers.add_parser("profile", help="perfila un repo candidato")
    profile_parser.add_argument("path", type=Path)
    profile_parser.add_argument("--name", required=True)
    profile_parser.add_argument("--out", type=Path, default=Path("out"))
    profile_parser.add_argument("--no-suite", action="store_true")

    table_parser = subparsers.add_parser("table", help="tabla comparativa de las fichas existentes")
    table_parser.add_argument("--out", type=Path, default=Path("out"))

    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.command == "profile":
        profile = profile_repo(args.path, name=args.name, run_suite=not args.no_suite)
        destination = args.out / f"{args.name}.md"
        destination.write_text(render_profile(profile), encoding="utf-8")
        import json

        (args.out / f"{args.name}.json").write_text(
            json.dumps(profile.to_flat_dict(), indent=2), encoding="utf-8"
        )
        print(f"escrito {destination}")
        return 0

    if args.command == "table":
        import json

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
