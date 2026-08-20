"""El grafo de imports entre módulos, para las dos transformaciones que lo tocan.

Vive aparte porque B1 y B5 se hacen la MISMA pregunta por razones distintas y
con la misma consecuencia si se contesta mal: un ciclo de imports de nivel de
módulo no degrada nada, mata el `import`, y un repositorio que no arranca se lee
exactamente igual que un agente que fracasa (§11). B1 pregunta antes de mudar una
definición a otro fichero; B5, antes de fundir dos ficheros en uno. Dos copias de
esto podrían discrepar, y el día que discreparan la celda se publicaría con un
ciclo que nadie vio venir.
"""

from __future__ import annotations

from collections.abc import Iterable


def components(edges: Iterable[tuple[str, str]]) -> dict[str, int]:
    """Componentes fuertemente conexas, por Kosaraju: nodo → etiqueta.

    Dos nodos comparten etiqueta cuando se alcanzan el uno al otro, o sea cuando
    ya se importaban en círculo antes de que nadie tocara nada. Esa distinción es
    la que decide la dosis: un repo puede importarse en círculo y sobrevivir —con
    el import al final del fichero, dentro de un `try`, o en la forma `import x`
    que Python resuelve contra el módulo a medias—, y sin tolerar lo que ya
    estaba, un solo ciclo de partida deja el grafo cíclico para siempre y la
    transformación rechaza TODOS sus movimientos.

    Los vecinos se recorren ordenados: las etiquetas deciden qué movimientos se
    aceptan, así que un recorrido que dependiera del orden de inserción daría
    árboles distintos en dos corridas de la misma celda (§5.4.4).
    """
    forward: dict[str, set[str]] = {}
    backward: dict[str, set[str]] = {}
    nodes: set[str] = set()
    for origin, target in edges:
        forward.setdefault(origin, set()).add(target)
        backward.setdefault(target, set()).add(origin)
        nodes.update((origin, target))

    order: list[str] = []
    seen: set[str] = set()
    for start in sorted(nodes):
        if start in seen:
            continue
        # Iterativo y no recursivo: un repo grande desborda la pila.
        stack = [(start, iter(sorted(forward.get(start, ()))))]
        seen.add(start)
        while stack:
            node, pending = stack[-1]
            following = next(pending, None)
            if following is None:
                order.append(node)
                stack.pop()
            elif following not in seen:
                seen.add(following)
                stack.append((following, iter(sorted(forward.get(following, ())))))

    labels: dict[str, int] = {}
    label = 0
    for start in reversed(order):
        if start in labels:
            continue
        stack = [start]
        labels[start] = label
        while stack:
            node = stack.pop()
            for previous in sorted(backward.get(node, ())):
                if previous not in labels:
                    labels[previous] = label
                    stack.append(previous)
        label += 1
    return labels
