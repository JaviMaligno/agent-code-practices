"""A1 sobre TypeScript, enchufada al mismo registro que las de Python.

La transformación la hace un script de Node porque el árbol es TypeScript y
LibCST no lo lee. Lo que se comprueba aquí es que se comporta como una
transformación del catálogo: recibe un árbol, lo cambia, y dice qué hizo.
"""

import shutil
from pathlib import Path

import pytest

from acp.transforms.a1_ts import apply


def build(root: Path) -> Path:
    src = root / "src"
    src.mkdir(parents=True)
    (src / "core.ts").write_text(
        "export function rate(value: number): number {\n"
        "  const doble: number = value * 2\n"
        "  return doble\n"
        "}\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.skipif(shutil.which("node") is None, reason="necesita node")
def test_annotations_become_any_instead_of_disappearing(tmp_path: Path):
    """Borrarlas rompería la compilación bajo `strict` y la transformación
    dejaría de ser equivalente; `any` la mantiene compilando y el runtime
    idéntico, porque TypeScript borra los tipos al emitir."""
    root = build(tmp_path / "repo")

    apply(root)

    salida = (root / "src" / "core.ts").read_text(encoding="utf-8")
    assert ": any" in salida
    assert ": number" not in salida


@pytest.mark.skipif(shutil.which("node") is None, reason="necesita node")
def test_it_reports_what_it_touched(tmp_path: Path):
    """El resto de la campaña espera un `TransformResult`: sin él, la dosis de
    la celda no se puede declarar y una transformación que no hizo nada se
    confunde con una que sí."""
    root = build(tmp_path / "repo")

    resultado = apply(root)

    assert resultado.files_changed >= 1
