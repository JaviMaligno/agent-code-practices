# Por qué la sonda TypeScript no se puede correr, medido

El diseño quería una sonda sobre un repositorio TypeScript para responder algo
concreto:

> En Python las anotaciones no las comprueba nadie en ejecución, así que A1 mide
> su valor **como documentación**. En un lenguaje con comprobación estática, los
> tipos son además contrato y herramienta. ¿El hallazgo sobre tipos es un
> artefacto de que en Python son opcionales?

La pregunta se volvió urgente al medir que **quitar las anotaciones no hace daño
detectable en Python**. Se intentó, y no se puede. Las tres razones son
independientes y cada una bastaría.

## 1. Quitar anotaciones de parámetro no compila

Un fichero con y sin anotaciones, mismo `tsc 5`, misma configuración:

| | Código de salida |
|---|---|
| Con tipos, `--strict` | 0 |
| **Sin tipos, `--strict`** | **2** — `TS7006: Parameter implicitly has an 'any' type` |
| Sin tipos, sin `--strict` | 0 |

## 2. Ni quitando solo las que el compilador puede inferir

La versión debilitada —quitar retornos y variables con inicializador, dejar los
parámetros— se implementó con `ts-morph` y se aplicó a **ts-pattern** (48 ficheros
de test, 453 tests, `strict: true`). Quitó 30 anotaciones de retorno y 26 de
variable, y el resultado **no compila**:

```
src/match.ts(71,7): error TS7053: Element implicitly has an 'any' type because
    expression of type 'string' can't be used to index type '{}'
src/match.ts(104,7): error TS2345: Argument of type '{ matched: boolean; ... }'
    is not assignable to parameter of type 'MatchState<output>'
```

En ts-pattern los retornos **no son inferibles**: son tipos condicionales que el
compilador no puede deducir del cuerpo. La anotación no describe lo que la
función hace, es lo que la función hace. Eso es un caso extremo —una librería
cuyo producto son los tipos— pero muestra que "inferible" no es una propiedad del
lenguaje, sino de cada función.

## 3. Y la suite de un repo TypeScript comprueba los tipos

Lo que remata la cuestión. El criterio de equivalencia de todo el experimento es
que **la suite del repositorio dé un resultado idéntico** antes y después. En los
repos TypeScript reales, la suite incluye el compilador:

```
got:      "test": "xo && tsc --noEmit && ava"
p-queue:  "test": "xo && node --import=tsx/esm --test test/*.ts"
```

Las anotaciones son parte de lo que la suite verifica. Quitarlas no puede dejar
la suite igual: no por un detalle de implementación, sino porque en ese lenguaje
los tipos no son documentación sobre el programa, son programa.

## La conclusión

La pregunta original —¿cuánto vale una anotación de tipo como documentación para
un agente?— **solo tiene sentido en un lenguaje donde las anotaciones no se
comprueban**. En Python se puede medir porque quitarlas deja el programa
idéntico, verificado por su propia suite. En TypeScript no hay nada que medir,
porque no queda programa.

Eso no dice que los tipos importen más o menos en TypeScript. Dice que este
método no puede compararlos, y que el resultado sobre A1 en Python **no se
generaliza** a lenguajes con comprobación estática — no por falta de datos, sino
porque la transformación no existe allí.
