"""Una sola llamada al modelo, sin dependencias externas.

No se usa un SDK a propósito: la única operación que el experimento necesita es
`chat/completions`, y una dependencia menos es una versión menos que congelar
durante la campaña (§5.4.4 pide versiones fijas mientras corre).
"""

from __future__ import annotations

import json
import random
import time
import os
import urllib.error
import urllib.request
from pathlib import Path

# La clave fuera del repositorio: este repo es público y la pasarela es de la
# empresa. El fichero se lee en cada llamada, así que rotarla no exige reiniciar.
KEY_FILE = Path.home() / ".acp-litellm-key"
DEFAULT_BASE = "https://litellm.infra.skyc.cloud"

# La misma llamada por dos transportes. La pasarela de la empresa solo se alcanza
# por VPN —una VM en la nube no la ve: medido, ni túneles ni peerings— pero sí
# alcanza el endpoint público de Azure OpenAI, y la pasarela enruta
# `gpt-5.4-mini-kyc-tst` a `azure/gpt-5.4-mini-kyc`. Mismo despliegue y misma
# versión, así que hablarle directo **no cambia el modelo**, solo el transporte.
#
# Lo que sí cambia son los defectos de cada capa (reintentos, timeouts), por eso
# el transporte no se mezcla dentro de una misma campaña: se elige al arrancarla.
AZURE_KEY_FILE = Path.home() / ".acp-azure-key"
BACKENDS = ("litellm", "azure")


class ModelError(RuntimeError):
    """La pasarela no contestó lo que se le pidió."""


def build_request(
    messages: list[dict],
    *,
    model: str,
    base: str,
    key: str,
    backend: str = "litellm",
    tools: list[dict] | None = None,
    max_tokens: int = 4000,
) -> urllib.request.Request:
    """La petición HTTP de un turno, armada para el transporte que toque.

    Azure OpenAI no acepta `Authorization: Bearer` con una api-key —pide la
    cabecera `api-key`— y su superficie v1 vive bajo `/openai/v1`. Un transporte
    desconocido se rechaza en vez de adivinarse: adivinar mandaría la clave al
    sitio equivocado.
    """
    if backend not in BACKENDS:
        raise ModelError(f"transporte {backend!r} no es uno de {BACKENDS}")

    cuerpo: dict = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if tools:
        cuerpo["tools"] = tools
        cuerpo["tool_choice"] = "auto"

    base = base.rstrip("/")
    if backend == "azure":
        url = f"{base}/openai/v1/chat/completions"
        cabeceras = {"api-key": key, "Content-Type": "application/json"}
    else:
        url = f"{base}/v1/chat/completions"
        cabeceras = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    return urllib.request.Request(
        url, data=json.dumps(cuerpo).encode("utf-8"), headers=cabeceras
    )


def _credentials() -> tuple[str, str, str]:
    """Dónde hablar, con qué clave y por qué transporte.

    El transporte se elige con `ACP_MODEL_BACKEND` y por defecto es la pasarela,
    que es por donde corrió la campaña del portátil.
    """
    backend = os.environ.get("ACP_MODEL_BACKEND", "litellm")
    if backend not in BACKENDS:
        raise ModelError(f"ACP_MODEL_BACKEND={backend!r} no es uno de {BACKENDS}")

    if backend == "azure":
        base = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        if not base:
            raise ModelError("sin AZURE_OPENAI_ENDPOINT para el transporte azure")
        key = os.environ.get("ACP_AZURE_KEY")
        if not key and AZURE_KEY_FILE.exists():
            key = AZURE_KEY_FILE.read_text(encoding="utf-8").strip()
        if not key:
            raise ModelError(f"sin clave azure: ni ACP_AZURE_KEY ni {AZURE_KEY_FILE}")
        return base, key, backend

    base = os.environ.get("LITELLM_PROXY_URL", DEFAULT_BASE).rstrip("/")
    key = os.environ.get("ACP_LITELLM_KEY")
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise ModelError(
            f"sin clave de inferencia: ni ACP_LITELLM_KEY ni {KEY_FILE}"
        )
    return base, key, backend


class RateLimited(Exception):
    """El servidor pide esperar. Es un "vuelve luego", no un "no puedo".

    Se distingue de `ModelError` porque el tratamiento es opuesto: un error de
    permisos o de modelo hay que declararlo y parar, y un 429 hay que esperarlo.
    Confundirlos costó 544 de 795 celdas de una tanda, cada una con su árbol, su
    suite y su oráculo ya pagados antes de morir en la primera llamada.
    """

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("el endpoint pide esperar")
        self.retry_after = retry_after


# Cuántas veces se insiste antes de declarar el fallo. Sin límite, una celda
# quedaría colgada para siempre y la campaña sin avanzar.
MAX_REINTENTOS = 6
ESPERA_BASE = 4.0


def _send(peticion, timeout: int) -> dict:
    """Una llamada, sin reintentos: la capa que sabe de HTTP y de nada más."""
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return json.loads(respuesta.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 429:
            cabecera = error.headers.get("Retry-After") if error.headers else None
            try:
                espera = float(cabecera) if cabecera else None
            except (TypeError, ValueError):
                espera = None
            raise RateLimited(retry_after=espera) from error
        # El cuerpo del error dice qué modelo o permiso falta; la clave no
        # aparece en él, así que se puede propagar.
        raise ModelError(f"{error.code}: {error.read()[:300]!r}") from error
    except OSError as error:
        raise ModelError(str(error)) from error


def complete_with_retry(peticion, timeout: int) -> dict:
    """Insiste mientras el endpoint diga que espere.

    El retardo crece y lleva una parte aleatoria: veinte procesos reintentando a
    la vez y al mismo ritmo reproducen exactamente la avalancha que provocó el
    429. Si el servidor manda `Retry-After` se respeta, porque volver antes de
    tiempo solo gasta otro intento.
    """
    for intento in range(MAX_REINTENTOS):
        try:
            return _send(peticion, timeout)
        except RateLimited as limite:
            if intento == MAX_REINTENTOS - 1:
                raise ModelError(
                    f"429 tras {MAX_REINTENTOS} intentos: el endpoint sigue saturado"
                ) from limite
            propia = ESPERA_BASE * (2 ** intento)
            espera = max(limite.retry_after or 0.0, propia)
            espera += random.uniform(0, min(espera, 8.0))
            time.sleep(espera)
    raise ModelError("reintentos agotados")


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
    base, key, backend = _credentials()
    mensajes = []
    if system:
        mensajes.append({"role": "system", "content": system})
    mensajes.append({"role": "user", "content": prompt})

    peticion = build_request(
        mensajes, model=model, base=base, key=key, backend=backend,
        max_tokens=max_tokens,
    )
    cuerpo = complete_with_retry(peticion, timeout)

    try:
        return cuerpo["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as error:
        raise ModelError(f"respuesta inesperada: {str(cuerpo)[:200]}") from error


def converse(
    messages: list[dict],
    model: str,
    *,
    tools: list[dict] | None = None,
    max_tokens: int = 4000,
    timeout: int = 300,
) -> dict:
    """Un turno de conversación con herramientas, devuelto tal cual.

    Devuelve el mensaje del modelo sin interpretar —con sus `tool_calls` si los
    hay— porque quien decide qué hacer con ellos es el bucle del agente, y el
    registro de la campaña necesita el mensaje entero, no un resumen (§5.4.1).
    """
    base, key, backend = _credentials()
    peticion = build_request(
        messages, model=model, base=base, key=key, backend=backend,
        tools=tools, max_tokens=max_tokens,
    )
    datos = complete_with_retry(peticion, timeout)

    try:
        eleccion = datos["choices"][0]
    except (KeyError, IndexError) as error:
        raise ModelError(f"respuesta inesperada: {str(datos)[:200]}") from error
    return {
        "message": eleccion["message"],
        "finish_reason": eleccion.get("finish_reason"),
        "usage": datos.get("usage", {}),
    }
