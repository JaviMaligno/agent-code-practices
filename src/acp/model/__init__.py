"""Acceso al modelo a través de la pasarela LiteLLM.

La clave vive fuera del repositorio (`~/.acp-litellm-key`, permisos 600) porque
este repo es público. El código nunca la imprime ni la registra: solo la lee y
la usa en la cabecera de la petición.
"""

from acp.model.client import ModelError, ask, converse

__all__ = ["ask", "converse", "ModelError"]
