from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# §3.3.1 parte el set en dos estratos y toda la tabla principal se lee por ese
# corte. Cerrar el vocabulario aquí es lo que impide que un error tipográfico en
# un JSON escrito a mano cree un tercer estrato de una sola tarea, que no
# rompería nada visible pero descuadraría el reparto sin avisar.
STRATA = ("generic", "domain")


@dataclass
class Task:
    """Un parche que rompe una función concreta, más los tests que ese parche
    debe poner en rojo y los que debe dejar intactos (§3.3).

    Las invariantes se comprueban al construir, no solo al leer JSON: las tareas
    genéricas las fabrica el inyector en memoria y una tarea inválida fabricada
    por código cuesta lo mismo de descubrir tarde que una escrita a mano.
    """

    task_id: str
    repo: str
    module: str
    symbol: str
    stratum: str
    patch: str
    fail_to_pass: list[str]
    pass_to_pass: list[str] = field(default_factory=list)
    # Cuántos ficheros hay que leer como mínimo para poder juzgar que esto es un
    # fallo: es la variable que une el estrato con la métrica de localización
    # (§3.3.1). Un fallo genérico se reconoce por su forma, donde está, así que
    # 1 es su valor natural y por eso es el defecto.
    min_files_to_judge: int = 1

    def __post_init__(self) -> None:
        if self.stratum not in STRATA:
            raise ValueError(
                f"stratum {self.stratum!r} no es uno de {STRATA}"
            )
        # Sin tests que distingan arreglado de roto no hay medida (§3.2.1): una
        # tarea así se contaría como resuelta siempre.
        if not self.fail_to_pass:
            raise ValueError(f"la tarea {self.task_id!r} no rompe ningún test")
        # Un fallo de dominio que se juzga leyendo una sola función es un fallo
        # genérico disfrazado, y ese es el modo de fallo más probable al
        # fabricar el estrato (§3.3.1).
        if self.stratum == "domain" and self.min_files_to_judge < 2:
            raise ValueError(
                f"la tarea de dominio {self.task_id!r} se juzga con "
                f"{self.min_files_to_judge} fichero(s)"
            )

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Task:
        return cls(
            task_id=raw["task_id"],
            repo=raw["repo"],
            module=raw["module"],
            symbol=raw["symbol"],
            stratum=raw["stratum"],
            patch=raw["patch"],
            fail_to_pass=list(raw["fail_to_pass"]),
            pass_to_pass=list(raw.get("pass_to_pass", [])),
            min_files_to_judge=raw.get("min_files_to_judge", 1),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "repo": self.repo,
            "module": self.module,
            "symbol": self.symbol,
            "stratum": self.stratum,
            "patch": self.patch,
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
            "min_files_to_judge": self.min_files_to_judge,
        }
