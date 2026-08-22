// Inyección de fallos en TypeScript: el equivalente del catálogo de Python.
//
// Las mismas formas que en `acp/tasks/mutations.py`, por la misma razón: una
// tarea no se busca, se fabrica, y solo cuenta si rompe unos tests concretos y
// no otros. Que las formas coincidan es lo que permite comparar lo que pasa en
// los dos lenguajes en vez de comparar dos catálogos distintos.
//
// La mutación tiene que seguir compilando. Un fallo que no compila no es una
// tarea difícil, es un árbol roto — y se leería como un agente que fracasa, que
// es el modo de fallo del que todo el diseño se defiende.

import { Project, SyntaxKind, Node } from "ts-morph";

/** Devuelve el fuente con el fallo inyectado, o `null` si esa forma no aplica. */
export function mutar(fuente, simbolo, forma) {
  const proyecto = new Project({ useInMemoryFileSystem: true });
  const f = proyecto.createSourceFile("m.ts", fuente);

  const objetivo =
    f.getFunction(simbolo) ??
    f.getVariableDeclaration(simbolo) ??
    f.getDescendantsOfKind(SyntaxKind.MethodDeclaration).find((m) => m.getName() === simbolo);
  if (!objetivo) return null;

  const aplicada = FORMAS[forma]?.(objetivo);
  if (!aplicada) return null;
  const salida = f.getFullText();
  return salida === fuente ? null : salida;
}

const FORMAS = {
  // Niega la primera condición de un `if`: el camino se toma justo al revés.
  invert_condition(nodo) {
    const si = nodo.getFirstDescendantByKind(SyntaxKind.IfStatement);
    if (!si) return false;
    const cond = si.getExpression();
    cond.replaceWithText(`!(${cond.getText()})`);
    return true;
  },

  // Mueve un límite numérico en uno. Se elige el primer literal de una
  // comparación, que es donde un off-by-one es plausible y no evidente.
  off_by_one(nodo) {
    for (const bin of nodo.getDescendantsOfKind(SyntaxKind.BinaryExpression)) {
      const op = bin.getOperatorToken().getText();
      if (!["===", "!==", "==", "!=", "<", ">", "<=", ">="].includes(op)) continue;
      for (const lado of [bin.getRight(), bin.getLeft()]) {
        if (Node.isNumericLiteral(lado)) {
          lado.replaceWithText(String(Number(lado.getText()) + 1));
          return true;
        }
      }
    }
    return false;
  },

  // Quita una guarda contra nulo. En TypeScript esto pasa la compilación cuando
  // el tipo lo permite, y estalla en ejecución — que es exactamente el fallo que
  // los tipos deberían haber evitado.
  drop_null_check(nodo) {
    for (const si of nodo.getDescendantsOfKind(SyntaxKind.IfStatement)) {
      const texto = si.getExpression().getText();
      const esGuarda =
        /^!\w[\w.]*$/.test(texto) ||
        /(===|!==|==|!=)\s*(null|undefined)/.test(texto) ||
        /^\w[\w.]*\s*(===|!==)\s*undefined$/.test(texto);
      if (!esGuarda) continue;
      if (si.getElseStatement()) continue; // con `else` quitarlo cambia dos caminos
      si.remove();
      return true;
    }
    return false;
  },

  // Intercambia los dos primeros argumentos de una llamada. Solo si sus textos
  // difieren: intercambiar dos iguales no cambiaría el comportamiento.
  swap_args(nodo) {
    for (const llamada of nodo.getDescendantsOfKind(SyntaxKind.CallExpression)) {
      const args = llamada.getArguments();
      if (args.length < 2) continue;
      const [a, b] = [args[0].getText(), args[1].getText()];
      if (a === b) continue;
      args[0].replaceWithText(b);
      llamada.getArguments()[1].replaceWithText(a);
      return true;
    }
    return false;
  },
};

export const FORMAS_DISPONIBLES = Object.keys(FORMAS);
