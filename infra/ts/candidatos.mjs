// Funciones candidatas a llevar un fallo inyectado.
//
// Se piden dos cosas, las mismas que en Python: que tengan ramas —una función
// sin condicionales no admite la mitad del catálogo— y que estén en el código de
// producción y no en los tests. Se ordenan por número de ramas porque una
// función con más caminos tiene más probabilidad de estar cubierta por la suite,
// y una mutación que no rompe ningún test no es una tarea.
import { Project, SyntaxKind } from "ts-morph";

const proyecto = new Project({ compilerOptions: { allowJs: false } });
proyecto.addSourceFilesAtPaths([process.argv[2], "!**/node_modules/**"]);

const salida = [];
for (const f of proyecto.getSourceFiles()) {
  const ruta = f.getFilePath();
  if (/\.test\.|\.test-d\.|\/test\//.test(ruta)) continue;
  for (const fn of [...f.getFunctions(), ...f.getDescendantsOfKind(SyntaxKind.MethodDeclaration)]) {
    const nombre = fn.getName?.();
    if (!nombre) continue;
    const ramas = fn.getDescendantsOfKind(SyntaxKind.IfStatement).length
      + fn.getDescendantsOfKind(SyntaxKind.ConditionalExpression).length;
    if (ramas === 0) continue;
    salida.push({ fichero: ruta.split("/repo/")[1] ?? ruta, simbolo: nombre, ramas });
  }
}
salida.sort((a, b) => b.ramas - a.ramas);
console.log(JSON.stringify(salida.slice(0, 40), null, 0));
