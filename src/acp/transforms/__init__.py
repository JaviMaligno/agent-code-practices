from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from acp.transforms import a1_types, a4_docs
from acp.transforms.base import TransformResult

TRANSFORMS: dict[str, Callable[[Path], TransformResult]] = {
    "A1": a1_types.apply,
    "A4": a4_docs.apply,
}
