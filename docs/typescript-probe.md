# Por qué la sonda TypeScript no se puede correr como estaba diseñada

El diseño quería una sonda de cuatro celdas sobre un repositorio TypeScript para
responder una pregunta concreta:

> En Python las anotaciones no las comprueba nadie en ejecución, así que A1 mide
> su valor **como documentación**. En un lenguaje con comprobación estática, los
> tipos son además contrato y herramienta. ¿El hallazgo sobre tipos es un
> artefacto de que en Python son opcionales?

Esa pregunta se volvió urgente al medir que **quitar las anotaciones no hace daño
detectable en Python** (KO-A1, tier bajo: sin efecto distinguible del ruido).

## Lo medido

Un fichero con y sin anotaciones, contra `tsc 5` con la misma configuración:

| | Código de salida |
|---|---|
| Con tipos, `--strict` | 0 |
| **Sin tipos, `--strict`** | **2** — `TS7006: Parameter 'items' implicitly has an 'any' type` |
| Sin tipos, sin `--strict` | 0 |

## Lo que implica

**En TypeScript con `strict`, quitar las anotaciones de tipo no es una
transformación semánticamente equivalente: el programa deja de compilar.** El
criterio que sostiene todo el experimento —el programa hace exactamente lo mismo
antes y después, verificado por su propia suite— no se puede cumplir.

Así que A1 no mide lo mismo en los dos lenguajes, y no por una diferencia de
grado. En Python la anotación es un comentario que resulta legible para
herramientas; quitarla deja el programa idéntico. En TypeScript con `strict` la
anotación es lo que hace que el programa exista. No hay una versión de este
experimento que compare las dos cosas, porque en el segundo caso no queda nada
que medir.

## Qué quedaría por hacer, si alguien quiere

Una versión debilitada sí es medible: quitar **solo las anotaciones que el
compilador puede inferir** —retornos, variables locales con inicializador— y
dejar las de parámetros, que son las que rompen bajo `noImplicitAny`. Sería una
dosis reducida y declarada, igual que en Python A1 no toca los cuerpos de clase
porque allí la anotación declara el atributo en vez de describirlo.

Eso mediría "los tipos que el compilador no necesita", que es una pregunta más
estrecha que la original y exige un transformador nuevo sobre `ts-morph`. No está
hecho.
