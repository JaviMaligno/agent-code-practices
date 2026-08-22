# Resultados de la campaña

Una línea por celda medida, en JSON. Cada celda es una corrida del agente sobre
un árbol concreto: repositorio, condición, tarea y número de pasada.

## Cómo leer un registro

- `condition` — `T0` sin tocar, `T1` familia A, `T2` familia B, `T3` ambas;
  `KO-<práctica>` quita esa práctica del código intacto, `AB-<práctica>` la
  devuelve al código totalmente degradado, `C-<n>` es un punto de la curva de
  tamaño.
- `measurable` — **si es `false`, la celda no dice nada del agente** y no entra
  en ninguna tasa: o el fallo inyectado no puso en rojo ningún test de ese árbol,
  o la suite no llegó a arrancar. El motivo está en `why`.
- `solved` — el oráculo derivado de ese árbol, no los nodeids que la tarea trae
  del original: las degradaciones mueven los tests.
- `turns` — turnos del agente, con techo en 40. **La mediana de este campo dice
  más que `solved`**: la degradación se paga en turnos, y el fallo aparece cuando
  el presupuesto no llega.

## Qué hay

| Fichero | Contenido |
|---|---|
| `campana-T[0-3].jsonl` | 2×2 en python-stdnum, tier bajo, 3 pasadas |
| `campana-python-stdnum-T*-alto.jsonl` | lo mismo con el tier alto |
| `campana-pint-T[0-3]{,-dom}.jsonl` | 2×2 en pint, genéricas y dominio |
| `campana-{ko,ab}-*-{bajo,alto}.jsonl` | desglose de las 8 prácticas, dos tiers |
| `campana-curva-{pint,sqlglot}.jsonl` | curva de tamaño |
| `campana-sqlglot-T0.jsonl` | baseline de sqlglot |

## Baselines que no discriminan

Tres bloques no se pueden interpretar, y están aquí para que se vea por qué:
el **tier alto** de python-stdnum resuelve 18/18 sin tocar nada (techo), el
**dominio de pint** resuelve 1/6 y **sqlglot** 0/3 (suelo). Sin margen no hay
caída que medir.

## La sonda TypeScript (`campana-hono-*.jsonl`)

24 celdas sobre hono: 4 tareas × 3 pasadas × 2 condiciones (T0 y `KO-A1-ts`, que
sustituye cada anotación de tipo por `any`).

**No interpretable, y se publica igual.** La baseline salió 1/12 (8%), pegada al
suelo, así que no hay hueco del que caer: el 5/12 de la condición degradada no
dice que quitar los tipos ayude, dice que la baseline no discriminaba.

La misma baseline había dado 2/4 en una sola pasada, y llegué a atribuirlo al
modelo: el gateway lo llama `gpt-5.4-mini-kyc-tst` y Azure `gpt-5.4-mini`. Es el
mismo despliegue. Lo que pasó es **varianza**: las dos tareas que allí salieron
resueltas dan 0/3 y 1/3 aquí, con los mismos turnos (10-20) en ambos sitios. El
agente trabaja igual; estas tareas simplemente se resuelven una de cada ocho o
diez veces, y cuatro celdas no bastan para verlo.

Que es, por tercera vez en esta campaña, el mismo error: una lectura sobre cuatro
o seis celdas que doce desmienten.
