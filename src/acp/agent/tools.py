"""Las herramientas que el agente puede usar dentro del contenedor.

La dotación es la variable del eje de tooling (§5.2): la RICA incluye búsqueda
por contenido y la POBRE no. Todo lo demás es igual, porque lo que se compara es
qué pasa cuando encontrar el sitio depende de los nombres de fichero y de la
jerarquía — que es lo que B2 destruye.

Cada lectura se registra con el RANGO de líneas que devolvió: la métrica de
localización se proyecta sobre eso (§5.4.2), y sin los rangos solo se sabría qué
ficheros abrió, que es la definición que se rompe en cuanto B5 lo concatena todo
en uno.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field

MAX_OUTPUT = 8000


@dataclass
class ToolCall:
    """Una invocación y lo que devolvió, para la traza (§5.4.1)."""

    name: str
    arguments: dict
    output: str
    # Fichero -> rangos de líneas que el agente llegó a VER. Las lecturas
    # parciales y los resultados de grep cuentan: lo que importa es qué región
    # tuvo delante, no qué fichero abrió entero.
    seen: dict[str, list[tuple[int, int]]] = field(default_factory=dict)


SEARCH_INCLUDES = {
    "python": ["*.py"],
    # `.tsx` y `.mjs` aparecen en repositorios reales; omitirlos dejaría al
    # agente ciego a parte del árbol sin que nada lo indique.
    "node": ["*.ts", "*.tsx", "*.js", "*.mjs", "*.cjs"],
}


def search_includes(language: str) -> list[str]:
    """En qué ficheros busca el agente, según el lenguaje del repositorio.

    Devolver una lista vacía haría que no encontrara nada, y eso se lee como un
    agente incapaz en vez de como una herramienta mal configurada: por eso un
    lenguaje desconocido es un error y no un silencio.
    """
    try:
        return list(SEARCH_INCLUDES[language])
    except KeyError:
        raise ValueError(
            f"lenguaje {language!r} sin patrones de búsqueda: {sorted(SEARCH_INCLUDES)}"
        ) from None


class Toolbox:
    """Ejecuta las herramientas dentro del contenedor del árbol."""

    def __init__(self, session, *, grep: bool = True, language: str = "python") -> None:
        self.session = session
        self.grep_enabled = grep
        # El lenguaje decide en qué ficheros busca `search`. Con el valor fijado
        # a Python, en un repositorio TypeScript el agente no encontraba nada.
        self.language = language
        self.calls: list[ToolCall] = []

    # -- descripción para el modelo -------------------------------------------

    def schema(self) -> list[dict]:
        herramientas = [
            {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "description": "Lista el contenido de un directorio del repositorio.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": (
                        "Lee un fichero. Devuelve las líneas numeradas. "
                        "Usa start/end para leer solo una parte."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "start": {"type": "integer"},
                            "end": {"type": "integer"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": (
                        "Sustituye un texto exacto por otro dentro de un fichero. "
                        "El texto original debe aparecer una sola vez."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old": {"type": "string"},
                            "new": {"type": "string"},
                        },
                        "required": ["path", "old", "new"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_tests",
                    "description": "Ejecuta la suite de tests del repositorio.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
        if self.grep_enabled:
            herramientas.insert(0, {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Busca un texto en todo el repositorio.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            })
        return herramientas

    # -- ejecución -------------------------------------------------------------

    def call(self, name: str, arguments: dict) -> str:
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return f"herramienta desconocida: {name}"
        try:
            salida, vistos = handler(**arguments)
        except TypeError as error:
            salida, vistos = f"argumentos inválidos: {error}", {}
        except Exception as error:  # noqa: BLE001 - el agente tiene que poder seguir
            salida, vistos = f"error: {type(error).__name__}: {error}", {}
        salida = salida[:MAX_OUTPUT]
        self.calls.append(ToolCall(name=name, arguments=arguments, output=salida, seen=vistos))
        return salida

    def _shell(self, command: str) -> str:
        code, output, _ = self.session.run(command)
        return output

    def _list_dir(self, path: str = ".") -> tuple[str, dict]:
        return self._shell(f"ls -la {shlex.quote(path)}"), {}

    def _search(self, query: str) -> tuple[str, dict]:
        # Los `--include` van por lenguaje: fijados a '*.py' dejaban al agente
        # ciego en un repositorio TypeScript, donde gastaba siete u ocho turnos
        # buscando, no leía un solo fichero y se rendía. Cuatro celdas de la
        # sonda salieron como "no lo arregló" sin que el agente viera el código.
        includes = " ".join(f"--include='{x}'" for x in search_includes(self.language))
        excluye = "--exclude-dir=node_modules --exclude-dir=.git"
        salida = self._shell(
            f"grep -rn {includes} {excluye} {shlex.quote(query)} . | head -60"
        )
        # Cada acierto de grep enseña una línea concreta: cuenta como vista.
        vistos: dict[str, list[tuple[int, int]]] = {}
        for linea in salida.splitlines():
            partes = linea.split(":", 2)
            if len(partes) >= 2 and partes[1].isdigit():
                fichero = partes[0].removeprefix("./")
                numero = int(partes[1])
                vistos.setdefault(fichero, []).append((numero, numero))
        return salida, vistos

    def _read_file(self, path: str, start: int = 1, end: int = 0) -> tuple[str, dict]:
        final = end or start + 200
        salida = self._shell(
            f"sed -n '{start},{final}p' {shlex.quote(path)} | nl -ba -v {start}"
        )
        return salida, {path.removeprefix("./"): [(start, final)]}

    def _edit_file(self, path: str, old: str, new: str) -> tuple[str, dict]:
        actual = self._shell(f"cat {shlex.quote(path)}")
        if actual.count(old) != 1:
            return (
                f"el texto aparece {actual.count(old)} veces: tiene que aparecer una",
                {},
            )
        self.session.write(path, actual.replace(old, new, 1))
        return "editado", {}

    def _run_tests(self) -> tuple[str, dict]:
        return self._shell("python -m pytest -q 2>&1 | tail -20"), {}
