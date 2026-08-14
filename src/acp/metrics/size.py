from __future__ import annotations

from pathlib import Path

from acp.models import SizeMetrics

EXCLUDED_DIRS = {
    "tests", "test", "testing", "docs", "doc", "examples", "example",
    "vendor", "third_party", "build", "dist", ".git", ".venv", "venv",
    "__pycache__", "node_modules", "site-packages",
}


def iter_source_files(root: Path) -> list[Path]:
    """Ficheros .py del repo, excluidos tests, vendorizados y artefactos."""
    found: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        parts = set(path.relative_to(root).parts[:-1])
        if parts & EXCLUDED_DIRS:
            continue
        found.append(path)
    return found


def _code_lines(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def measure(root: Path) -> SizeMetrics:
    files = iter_source_files(root)
    if not files:
        return SizeMetrics(python_files=0, code_lines=0, max_depth=0, mean_depth=0.0)
    depths = [len(path.relative_to(root).parts) - 1 for path in files]
    return SizeMetrics(
        python_files=len(files),
        code_lines=sum(_code_lines(path) for path in files),
        max_depth=max(depths),
        mean_depth=sum(depths) / len(depths),
    )
