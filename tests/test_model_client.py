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
