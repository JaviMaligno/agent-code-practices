# agent-code-practices

Experimento: qué buenas prácticas de software ayudan realmente a un coding agent.
Se degradan repositorios reales con transformaciones que preservan el comportamiento
(familias A y B) y se mide si un agente sigue resolviendo tareas con fallos inyectados.
El diseño completo vive fuera de este repo (spec del blog, ver `README.md`); las
referencias `§x.y` en código y docs apuntan a ese spec.

## Estructura

- `src/acp/` — paquete Python (`acp`).
  - `cli.py` — CLI `acp`: subcomandos `profile`, `table`, `transform`.
  - `campaign.py` — CLI de campaña (`python -m acp.campaign`): corre las celdas tarea × condición × pasada y escribe un JSONL.
  - `transforms/` — una transformación por práctica: `a1_types`, `a1_ts`, `a2_names`, `a3_format`, `a4_docs`, `b1_cohesion`, `b2_hierarchy`, `b3_repo_docs`, `b4_tests`, `b5_size`.
  - `metrics/` — perfilado (acoplamiento, dominio, legibilidad, tipado en runtime, tamaño).
  - `tasks/` — inyección de fallos (`mutations`, `inject`), validación y aislamiento.
  - `agent/` — bucle del agente y sus herramientas; `model/client.py` — cliente del modelo.
  - `suite.py`, `node_suite.py`, `runners.py` — ejecución de la suite del candidato (local o Docker).
  - `equivalence.py`, `oracles.py` — comprobación de que una transformación no cambia el comportamiento.
  - `stats.py`, `contrasts.py`, `tables.py`, `report.py` — análisis de resultados.
- `tasks/<repo>/*.json` — tareas por repositorio candidato (pint, python-stdnum, sqlglot, hono).
- `results/*.jsonl` — datos de campaña versionados (son el resultado publicado; no reescribir).
- `infra/` — scripts de VM para campañas largas (`provision-vm.sh`, `vigilar.sh`, `autodestruir.sh`), `generar-tareas.py` (filtro de tareas contra el árbol limpio) y `infra/ts/` (A1 y mutaciones para TypeScript con `ts-morph`, paquete Node aparte).
- `docs/` — notas de método (p. ej. `typescript-probe.md`).
- `candidates/` (clones) y `out/` (fichas) están en `.gitignore` a propósito: ocupan gigas y se borran al terminar cada bloque.

## Entorno

    python -m venv .venv && source .venv/bin/activate
    pip install --only-binary :all: libcst   # desde wheel: compilarlo pide toolchain de Rust
    pip install -e '.[dev]'

Para `infra/ts/`: `cd infra/ts && npm install`.

## Comandos

    pytest                                   # por defecto excluye los marcados `integration`
    pytest -m integration                    # crea entornos reales y usa la red; `docker` además necesita daemon
    cd infra/ts && npm test                  # tests de mutate.mjs

    acp profile candidates/pint --name pint --out out/
    acp table --out out/
    acp transform candidates/pint --apply B5-2000 --out work/pint-B5-2000
    python -m acp.campaign <clon> --tasks tasks/pint --log results/x.jsonl --workdir work/ --model <modelo>

Opciones de `campaign` relevantes: `--conditions` (p. ej. `T0,T2` o `KO-A1,AB-A1`; sin ella, el 2×2),
`--runs` (el diseño pide 3 en las celdas de titular), `--label`, `--language python|node`,
`--package-manager npm|bun`, `--poor` (dotación sin búsqueda), `--max-turns` (40).

## Modelo

`model/client.py` elige el transporte con `ACP_MODEL_BACKEND` (`litellm` por defecto, o `azure`).
Variables: `LITELLM_PROXY_URL` + `ACP_LITELLM_KEY`, o `AZURE_OPENAI_ENDPOINT` + `ACP_AZURE_KEY`.
El transporte se elige al arrancar una campaña y no se mezcla dentro de una: cambia reintentos,
timeouts y cuota, no el modelo, y debe declararse al publicar.

## Reglas del experimento (gotchas)

- Las transformaciones editan con LibCST sin reescribir formato ni comentarios: si una transformación
  toca algo más que su práctica, el efecto deja de ser atribuible.
- Un árbol transformado por B1, B2 o B5 se perfila con `--no-install-repo`; sin él la suite mide el
  paquete publicado en PyPI en vez del repositorio y la celda sale en verde igualmente.
- `transform` rechaza techos de B5 que repiten otro árbol ya pedible o que no funden nada, y dice cuáles son válidos.
- La procedencia (`--manifest`) nunca va dentro del árbol transformado.
- En TypeScript, A1 sustituye anotaciones por `any` (borrarlas no compila bajo `noImplicitAny`) y la
  equivalencia usa solo tests de runtime, no `tsc` ni type-tests. Ver `infra/ts/README.md` y `docs/typescript-probe.md`.
- `autodestruir.sh` sube resultados y solo borra la VM (y su regla de firewall) si la subida se verifica.

## Estilo

Código, comentarios, docs y mensajes de CLI en español; los comentarios explican el *porqué*
de cada decisión. Los mensajes de commit, en cambio, van en inglés, con prefijo tipo
`feat:`, `fix:`, `test:`, `docs:`, `data:`, `refactor:`, `infra:` y descripción corta.
