"""A1 sobre TypeScript: cada anotación de tipo pasa a ser `any`.

Es la traducción de A1 a un lenguaje donde los tipos se comprueban, y la razón de
que no se borren está medida: bajo `noImplicitAny` un fichero sin anotaciones no
compila (`TS7006`), así que borrarlas no sería una transformación
semánticamente equivalente — no quedaría programa. `any` sí lo es: el compilador
lo acepta, TypeScript borra los tipos al emitir, y el lector pierde exactamente
la información que el experimento mide.

El trabajo lo hace un script de Node porque el árbol es TypeScript y LibCST solo
lee Python. Lo que vive aquí es el enchufe al registro de transformaciones, para
que la campaña no tenga que saber que hay dos lenguajes.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from acp.transforms.base import TransformResult

# El script y su `node_modules` viven juntos en el repo: `ts-morph` no es una
# dependencia de Python y no tiene sentido pedirla en el pyproject.
SCRIPT_DIR = Path(__file__).resolve().parents[3] / "infra" / "ts"
SCRIPT = SCRIPT_DIR / "a1-any.mjs"


def apply(root: Path) -> TransformResult:
    """Convierte a `any` las anotaciones de todo el árbol.

    Devuelve cuántos ficheros cambió, como el resto del catálogo: sin eso la
    dosis de la celda no se puede declarar, y una transformación que no tocó
    nada se confundiría con una que sí — que es como se cuela una celda midiendo
    el árbol original y presentándolo como degradado.
    """
    root = Path(root).resolve()
    if not SCRIPT.exists():
        raise RuntimeError(f"falta el transformador de TypeScript en {SCRIPT}")

    antes = {p: p.read_text(encoding="utf-8", errors="replace")
             for p in _ficheros(root)}

    proceso = subprocess.run(
        ["node", str(SCRIPT), f"{root}/**/*.ts"],
        cwd=SCRIPT_DIR, capture_output=True, text=True, timeout=1800,
    )
    if proceso.returncode != 0:
        raise RuntimeError(
            f"el transformador de TypeScript falló: {proceso.stderr[-600:]}"
        )

    cambiados = sum(
        1 for p, texto in antes.items()
        if p.exists() and p.read_text(encoding="utf-8", errors="replace") != texto
    )
    return TransformResult(files_changed=cambiados)


def _ficheros(root: Path) -> list[Path]:
    return [
        p for p in root.rglob("*.ts")
        if "node_modules" not in p.parts and ".git" not in p.parts
    ]
