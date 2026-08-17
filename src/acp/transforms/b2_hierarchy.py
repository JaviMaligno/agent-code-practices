"""B2 — jerarquía: aplanar los directorios del paquete y renombrar los ficheros.

Lo que se destruye aquí es la señal de dónde mirar (§4.2 del spec): en un repo
con jerarquía, `stdnum/es/nif.py` te dice a la vez el país y el documento sin
abrir nada. Aplanado y renombrado a `stdnum/m17.py`, esa información ya no
existe y el agente tiene que ir a buscarla. Es la celda que se cruza con la
dotación pobre (§5.2): sin grep, encontrar el sitio depende exactamente de lo
que B2 quita.

El directorio del paquete raíz sobrevive (§5.6). Es lo único que mantiene
válidos a la vez la instalación de dependencias, los imports desde fuera y el
comando de test; lo que se aplana es todo lo de dentro.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import libcst as cst

from acp.metrics.size import read_source
from acp.transforms.base import TransformResult, iter_transformable_files


def _package_root(root: Path) -> Path | None:
    """El directorio del paquete, que es lo único que no se aplana.

    Se exige que haya exactamente uno: con dos paquetes de primer nivel no está
    claro cuál es el punto de entrada que hay que conservar, y aplanar el
    equivocado deja el repo sin forma de importarse. Sin candidato claro, B2 no
    hace nada y la celda se declara como no aplicable a ese repo.
    """
    candidates = [
        path
        for path in sorted(root.iterdir())
        if path.is_dir() and (path / "__init__.py").exists()
    ]
    return candidates[0] if len(candidates) == 1 else None


def _module_name(path: Path, root: Path) -> str:
    """El módulo que este fichero es, en la forma en que se importa.

    Se calcula relativo a la raíz del árbol porque es lo que hay en
    `sys.path` cuando la suite corre —el repo se alcanza por ruta, no por
    instalación (§5.6)—, y también es la clave con la que `build_symbol_map`
    nombra los módulos: si las dos formas no coincidieran, el mapa de identidad
    no podría seguir ningún movimiento.
    """
    relative = path.relative_to(root).with_suffix("")
    parts = relative.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def plan_moves(root: Path) -> dict[str, str]:
    """Módulo original → módulo destino, todos colgando del paquete raíz.

    Determinista y por orden alfabético de ruta: la condición tiene que ser la
    misma en dos corridas distintas, o los resultados no se pueden comparar
    entre seeds.

    Los módulos que ya cuelgan del paquete también se renombran: si no, la mitad
    del árbol conserva sus nombres y B2 mide media dosis.
    """
    package = _package_root(root)
    if package is None:
        return {}

    moves: dict[str, str] = {}
    index = 0
    for path in iter_transformable_files(root):
        if package not in path.parents:
            continue
        module = _module_name(path, root)
        # El paquete raíz es el punto de entrada y no se toca (§5.6).
        if module == package.name:
            continue
        moves[module] = f"{package.name}.m{index}"
        index += 1
    return moves


def _dotted(node: cst.BaseExpression) -> str:
    """La forma con puntos de un `a.b.c`, o vacío si no es un nombre con puntos."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr.value}" if base else ""
    return ""


class _RewriteImports(cst.CSTTransformer):
    """Reescribe los imports para que apunten a donde va a estar cada módulo.

    Se hace antes de mover nada: el diccionario de destinos ya está decidido, y
    reescribir primero evita tener que reconstruirlo leyendo un árbol a medio
    mover.
    """

    def __init__(self, moves: dict[str, str]) -> None:
        self.moves = moves

    def leave_ImportFrom(
        self, original: cst.ImportFrom, updated: cst.ImportFrom
    ) -> cst.ImportFrom:
        if updated.module is None:
            return updated
        target = self.moves.get(_dotted(updated.module))
        if target is None:
            return updated
        return updated.with_changes(module=cst.parse_expression(target))


def _rewrite_file(path: Path, moves: dict[str, str]) -> bool:
    source = read_source(path)
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return False
    transformed = module.visit(_RewriteImports(moves)).code
    if transformed == source:
        return False
    path.write_text(transformed, encoding="utf-8")
    return True


def _module_path(root: Path, module: str) -> Path:
    return root / Path(*module.split(".")).with_suffix(".py")


def apply(root: Path) -> TransformResult:
    moves = plan_moves(root)
    if not moves:
        return TransformResult()

    changed = 0
    # Alcance repo-wide, tests del repo incluidos (§4.3.1): un import sin
    # reescribir en la suite se lee como suite en rojo, o sea como fracaso.
    for path in iter_transformable_files(root):
        if _rewrite_file(path, moves):
            changed += 1

    for original, target in moves.items():
        source_path = _module_path(root, original)
        if not source_path.exists():
            # Un paquete es su `__init__.py`, no un fichero con su nombre.
            source_path = root / Path(*original.split(".")) / "__init__.py"
        destination = _module_path(root, target)
        if source_path != destination and source_path.exists():
            shutil.move(str(source_path), str(destination))
            changed += 1

    package = _package_root(root)
    if package is not None:
        # Solo los que quedan vacíos: un directorio con ficheros de datos dentro
        # sigue haciendo falta, porque quien los abre lo hace por ruta.
        for directory in sorted(package.rglob("*"), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()

    return TransformResult(files_changed=changed, moves=moves)
