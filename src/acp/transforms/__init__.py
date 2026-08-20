from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

from acp.transforms import (
    a1_types,
    a2_names,
    a3_format,
    a4_docs,
    b1_cohesion,
    b2_hierarchy,
    b3_repo_docs,
    b4_tests,
    b5_size,
)
from acp.transforms.base import TransformResult

TRANSFORMS: dict[str, Callable[[Path], TransformResult]] = {
    "A1": a1_types.apply,
    "A2": a2_names.apply,
    "A3": a3_format.apply,
    "A4": a4_docs.apply,
    "B1": b1_cohesion.apply,
    "B2": b2_hierarchy.apply,
    "B3": b3_repo_docs.apply,
    "B4": b4_tests.apply,
    "B5": b5_size.apply,
}

# B5 no es una celda sino una curva (§6.3): el mismo código con un techo de
# líneas distinto es un punto distinto, y el original es el cuarto. El CLI llama
# a cada transformación con la raíz y nada más, así que el techo tiene que estar
# en el nombre —es lo único que viaja hasta ahí— y de paso queda escrito en el
# manifiesto, que es donde hay que poder leer a qué punto pertenece un árbol.
TRANSFORMS.update(
    {f"B5-{ceiling}": partial(b5_size.apply, target_lines=ceiling) for ceiling in b5_size.CURVE}
)
