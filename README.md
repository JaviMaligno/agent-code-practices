# agent-code-practices

Experimento: qué buenas prácticas de software ayudan realmente a un coding agent.

Fase 0 — perfilado de repos candidatos. El diseño completo vive en el spec del blog:
`personal-website/docs/superpowers/specs/2026-08-14-software-practices-for-coding-agents-design.md`

## Uso

    python -m acp.cli profile candidates/pint --name pint --out out/

`candidates/` y `out/` están fuera del control de versiones a propósito: los clones ocupan
gigas y se borran al terminar cada bloque.

Un árbol ya transformado por B1, B2 o B5 se perfila con `--no-install-repo`: su estructura
ya no encaja con lo que declara su `pyproject`, así que se instalan las dependencias
declaradas y la suite alcanza el código por ruta (§5.6). Sin ese flag la corrida mediría el
paquete publicado en PyPI en vez del repositorio, y la celda saldría igualmente en verde.

    python -m acp.cli transform candidates/pint --apply B5-2000 --out work/pint-B5-2000

Los puntos de la curva de tamaño (§6.3) dependen del repositorio: `transform` rechaza el
techo que produce el mismo árbol que otro ya pedible —en pint, `B5-10000` repite `B5-2000`—
o que no funde nada —python-stdnum y holidays, donde no hay curva—, y dice cuáles sí tiene.
