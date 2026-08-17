from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from acp.transforms import (
    a1_types,
    a2_names,
    a3_format,
    a4_docs,
    b2_hierarchy,
    b3_repo_docs,
    b4_tests,
)
from acp.transforms.base import TransformResult

TRANSFORMS: dict[str, Callable[[Path], TransformResult]] = {
    "A1": a1_types.apply,
    "A2": a2_names.apply,
    "A3": a3_format.apply,
    "A4": a4_docs.apply,
    "B2": b2_hierarchy.apply,
    "B3": b3_repo_docs.apply,
    "B4": b4_tests.apply,
}
