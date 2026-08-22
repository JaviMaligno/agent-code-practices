# A1 en TypeScript

`a1-any.mjs` es el equivalente de A1 para un repositorio TypeScript: sustituye
cada anotación de tipo por `any` en vez de borrarla.

## Por qué `any` y no borrarla

Borrarla **no es una transformación equivalente**, y está medido: bajo
`noImplicitAny` el programa deja de compilar (`TS7006`), así que no queda
programa que medir. Con `any` sí lo es — es un tipo explícito, el compilador lo
acepta, y TypeScript borra los tipos al emitir, de modo que el runtime no cambia
una coma. El lector pierde exactamente la información que se quiere medir, que es
lo que A1 hace en Python.

Un intento intermedio también falló: quitar solo las anotaciones que el
compilador puede inferir. En **ts-pattern** eso rompe la compilación igualmente,
porque sus retornos son tipos condicionales que no se deducen del cuerpo — ahí la
anotación *es* la lógica. "Inferible" no es una propiedad del lenguaje sino de
cada función.

## Qué suite cuenta para la equivalencia

Solo los **tests de runtime**. Un repositorio TypeScript suele tener dos cosas
distintas bajo `npm test`:

```
hono:  "test": "tsc -p tsconfig.spec.json && vitest --run"
execa: "test": "npm run lint && npm run unit && npm run type"
```

Los type-tests y el `tsc` comprueban los tipos, que es justo lo que la
transformación degrada, así que incluirlos haría la equivalencia imposible por
construcción — no porque el programa cambie, sino porque la suite mide el
tratamiento. Se declara y se excluyen; el comportamiento es lo que tiene que
quedar idéntico.

## Verificado

**hono** (289 ficheros de fuente, `"files": []` en su tsconfig, así que los
ficheros se cargan por ruta y no por configuración):

| | Tests | Pasan | Fallan |
|---|---|---|---|
| original | 4.968 | 4.924 | 0 |
| con `any` (3.185 anotaciones) | 4.968 | 4.924 | 0 |

## Detalle de implementación que cuesta una hora si no se sabe

Cambiar un nodo invalida a sus hermanos en el árbol de ts-morph. Sin recorrer al
revés y comprobar `wasForgotten()`, el recorrido muere en el segundo parámetro
que toca con `Attempted to get information from a node that was removed`.
