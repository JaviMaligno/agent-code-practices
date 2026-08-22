"""Cómo se arma la llamada al modelo, según por dónde se le hable.

La campaña del portátil va por la pasarela de la empresa, que solo se alcanza
por VPN. Una campaña en una VM no la alcanza —medido: ni túneles VPN ni
peerings— pero sí alcanza el endpoint público de Azure OpenAI, y la pasarela
enruta `gpt-5.4-mini-kyc-tst` a `azure/gpt-5.4-mini-kyc`: el mismo despliegue y
la misma versión. Así que hablarle directo no cambia el modelo, solo el
transporte.

Se prueba la petición y no la respuesta porque es lo que decide si la llamada
llega: Azure rechaza con 401 lo que la pasarela acepta.
"""

import pytest

from acp.model.client import ModelError, build_request


def test_the_gateway_takes_a_bearer_token_and_its_own_path():
    peticion = build_request(
        [{"role": "user", "content": "hola"}],
        model="gpt-5.4-mini-kyc-tst",
        base="https://litellm.example.com",
        key="k-secreta",
        backend="litellm",
    )

    assert peticion.full_url == "https://litellm.example.com/v1/chat/completions"
    assert peticion.get_header("Authorization") == "Bearer k-secreta"
    assert peticion.get_header("Api-key") is None


def test_azure_takes_an_api_key_header_and_its_v1_surface():
    """Azure OpenAI no acepta `Authorization: Bearer` con una api-key: pide la
    cabecera `api-key`. Y su superficie v1 vive bajo `/openai/v1`."""
    peticion = build_request(
        [{"role": "user", "content": "hola"}],
        model="gpt-5.4-mini-kyc",
        base="https://recurso.openai.azure.com",
        key="k-secreta",
        backend="azure",
    )

    assert peticion.full_url == "https://recurso.openai.azure.com/openai/v1/chat/completions"
    assert peticion.get_header("Api-key") == "k-secreta"
    assert peticion.get_header("Authorization") is None


def test_an_unknown_backend_is_rejected_instead_of_guessed():
    """Adivinar el transporte mandaría la clave al sitio equivocado."""
    with pytest.raises(ModelError):
        build_request(
            [{"role": "user", "content": "hola"}],
            model="m",
            base="https://x",
            key="k",
            backend="vertex",
        )


def test_the_body_carries_the_tools_when_there_are_tools():
    import json

    peticion = build_request(
        [{"role": "user", "content": "hola"}],
        model="m",
        base="https://x",
        key="k",
        backend="litellm",
        tools=[{"type": "function", "function": {"name": "leer"}}],
    )

    cuerpo = json.loads(peticion.data)
    assert cuerpo["tool_choice"] == "auto"
    assert cuerpo["tools"][0]["function"]["name"] == "leer"
    # Sin temperatura: los modelos de razonamiento de esta familia la rechazan.
    assert "temperature" not in cuerpo


def test_a_rate_limit_is_waited_out_not_given_up_on(monkeypatch):
    """Un 429 dice "vuelve luego", no "no puedo". Sin reintento, 544 de 795
    celdas de una tanda se perdieron por saturar el endpoint con 28 procesos:
    cada celda pagaba su árbol, su suite y su oráculo y moría en la primera
    llamada al modelo.

    Se espera y se reintenta, con el retardo creciendo, porque veinte procesos
    reintentando a la vez al mismo ritmo reproducen la avalancha que causó el
    429.
    """
    from acp.model import client

    intentos = []
    esperas = []

    def falso_envio(peticion, timeout):
        intentos.append(1)
        if len(intentos) < 3:
            raise client.RateLimited(retry_after=None)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(client, "_send", falso_envio)
    monkeypatch.setattr(client.time, "sleep", lambda s: esperas.append(s))

    respuesta = client.complete_with_retry(object(), timeout=60)

    assert respuesta["choices"][0]["message"]["content"] == "ok"
    assert len(intentos) == 3
    assert esperas == sorted(esperas), "el retardo tiene que crecer"
    assert len(set(esperas)) > 1, "esperar siempre lo mismo recrea la avalancha"


def test_it_gives_up_eventually_instead_of_hanging(monkeypatch):
    """Reintentar sin fin dejaría una celda colgada para siempre y la campaña
    sin avanzar; el límite convierte eso en un fallo declarado."""
    from acp.model import client

    monkeypatch.setattr(
        client, "_send",
        lambda p, timeout: (_ for _ in ()).throw(client.RateLimited(retry_after=None)),
    )
    monkeypatch.setattr(client.time, "sleep", lambda s: None)

    with pytest.raises(client.ModelError):
        client.complete_with_retry(object(), timeout=60)


def test_it_honours_the_wait_the_server_asks_for(monkeypatch):
    """Azure manda `Retry-After`. Ignorarlo y usar el retardo propio es volver
    antes de tiempo y comerse otro 429."""
    from acp.model import client

    esperas = []
    estado = {"n": 0}

    def falso_envio(peticion, timeout):
        estado["n"] += 1
        if estado["n"] == 1:
            raise client.RateLimited(retry_after=37)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(client, "_send", falso_envio)
    monkeypatch.setattr(client.time, "sleep", lambda s: esperas.append(s))

    client.complete_with_retry(object(), timeout=60)

    assert 37 <= esperas[0] < 45, "debe respetar el Retry-After del servidor"
