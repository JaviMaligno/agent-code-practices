# agent-code-practices

Experimento: qué buenas prácticas de software ayudan realmente a un coding agent.

Fase 0 — perfilado de repos candidatos. El diseño completo vive en el spec del blog:
`personal-website/docs/superpowers/specs/2026-08-14-software-practices-for-coding-agents-design.md`

## Uso

    python -m acp.cli profile candidates/pint --name pint --out out/

`candidates/` y `out/` están fuera del control de versiones a propósito: los clones ocupan
gigas y se borran al terminar cada bloque.
