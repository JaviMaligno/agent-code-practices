"""Una sola llamada al modelo, sin dependencias externas.

No se usa un SDK a propósito: la única operación que el experimento necesita es
`chat/completions`, y una dependencia menos es una versión menos que congelar
durante la campaña (§5.4.4 pide versiones fijas mientras corre).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

# La clave fuera del repositorio: este repo es público y la pasarela es de la
# empresa. El fichero se lee en cada llamada, así que rotarla no exige reiniciar.
KEY_FILE = Path.home() / ".acp-litellm-key"
DEFAULT_BASE = "https://litellm.infra.skyc.cloud"


class ModelError(RuntimeError):
    """La pasarela no contestó lo que se le pidió."""


def _credentials() -> tuple[str, str]:
    base = os.environ.get("LITELLM_PROXY_URL", DEFAULT_BASE).rstrip("/")
    key = os.environ.get("ACP_LITELLM_KEY")
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise ModelError(
            f"sin clave de inferencia: ni ACP_LITELLM_KEY ni {KEY_FILE}"
        )
    return base, key


def ask(
    prompt: str,
    model: str,
    *,
    system: str | None = None,
    max_tokens: int = 2000,
    timeout: int = 180,
) -> str:
    """Lo que el modelo responde a un mensaje, como texto.

    Sin temperatura: los modelos de razonamiento de esta familia la rechazan, y
    lo que el experimento necesita para reproducir es el seed y la versión
    congelada (§5.4.4), no un valor de muestreo que la pasarela puede ignorar.
    """
    base, key = _credentials()
    mensajes = []
    if system:
        mensajes.append({"role": "system", "content": system})
    mensajes.append({"role": "user", "content": prompt})

    peticion = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": mensajes,
            "max_completion_tokens": max_tokens,
        }).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            cuerpo = json.loads(respuesta.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # El cuerpo del error dice qué modelo o permiso falta; la clave no
        # aparece en él, así que se puede propagar.
        raise ModelError(f"{error.code}: {error.read()[:300]!r}") from error
    except OSError as error:
        raise ModelError(str(error)) from error

    try:
        return cuerpo["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as error:
        raise ModelError(f"respuesta inesperada: {str(cuerpo)[:200]}") from error
