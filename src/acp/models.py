from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any


@dataclass
class SizeMetrics:
    python_files: int
    code_lines: int
    max_depth: int
    mean_depth: float


@dataclass
class ReadabilityMetrics:
    comment_ratio: float
    docstring_ratio: float
    annotated_function_ratio: float
    has_readme: bool
    has_docs_dir: bool


@dataclass
class RuntimeTypingMetrics:
    uses_runtime_typing: bool = False
    evidence: list[str] = field(default_factory=list)


@dataclass
class CouplingMetrics:
    internal_modules: int = 0
    internal_edges: int = 0
    mean_fan_out: float = 0.0
    max_fan_in: int = 0


@dataclass
class DomainMetrics:
    complex_functions: int = 0
    domain_candidate_functions: int = 0
    domain_density: float = 0.0
    samples: list[str] = field(default_factory=list)


@dataclass
class SuiteMetrics:
    ran: bool = False
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    seconds: float = 0.0
    # Preparación del entorno. Separada del resultado a propósito: un repo que no
    # se deja instalar no es lo mismo que un repo cuya suite está en rojo, y
    # confundirlos descarta candidatos por una razón que no es suya.
    install_ok: bool = False
    install_strategy: str = ""
    install_seconds: float = 0.0
    install_error: str = ""
    collect_ok: bool = False
    timed_out: bool = False


@dataclass
class RepoProfile:
    name: str
    size: SizeMetrics
    readability: ReadabilityMetrics
    runtime_typing: RuntimeTypingMetrics = field(default_factory=RuntimeTypingMetrics)
    coupling: CouplingMetrics = field(default_factory=CouplingMetrics)
    domain: DomainMetrics = field(default_factory=DomainMetrics)
    suite: SuiteMetrics = field(default_factory=SuiteMetrics)

    def to_flat_dict(self) -> dict[str, Any]:
        flat: dict[str, Any] = {"name": self.name}
        for f in fields(self):
            value = getattr(self, f.name)
            if is_dataclass(value):
                for key, inner in asdict(value).items():
                    flat[f"{f.name}.{key}"] = inner
        return flat
