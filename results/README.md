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
