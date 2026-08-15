from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from acp.transforms.base import TransformResult

TRANSFORMS: dict[str, Callable[[Path], TransformResult]] = {}
