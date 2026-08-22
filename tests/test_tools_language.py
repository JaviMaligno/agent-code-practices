"""Qué ficheros ve el agente al buscar.

La búsqueda estaba fijada a `--include='*.py'`, así que en un repositorio
TypeScript no encontraba nada: el agente gastaba siete u ocho turnos buscando,
no leía un solo fichero y se rendía. Cuatro celdas de la sonda salieron como
"no lo arregló" cuando el agente nunca vio el código.
"""

import pytest

from acp.agent.tools import search_includes


def test_python_repositories_search_python_files():
    assert search_includes("python") == ["*.py"]


def test_node_repositories_search_the_files_their_code_lives_in():
    incluye = search_includes("node")

    assert "*.ts" in incluye
    assert "*.js" in incluye
    # `.tsx` y `.mjs` existen en repos reales y omitirlos deja al agente ciego a
    # parte del árbol sin que nada lo indique.
    assert "*.tsx" in incluye
    assert "*.mjs" in incluye


def test_an_unknown_language_is_rejected_rather_than_searching_nothing():
    """Devolver una lista vacía haría que el agente no encuentre nada y eso se
    lee como un agente incapaz, que es el error que este arreglo corrige."""
    with pytest.raises(ValueError):
        search_includes("rust")
