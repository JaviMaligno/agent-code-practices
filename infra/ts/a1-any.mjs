// A1 para TypeScript: sustituye cada anotación por `any` en vez de borrarla.
//
// Borrarla no vale, y está medido: bajo `noImplicitAny` el programa deja de
// compilar, así que no sería una transformación equivalente. `any` sí lo es: es
// un tipo explícito que el compilador acepta, el runtime no cambia una coma
// —TypeScript borra los tipos al emitir— y el lector pierde exactamente la
// información que se quiere medir. Es la traducción fiel de lo que A1 hace en
// Python, donde la anotación tampoco tiene efecto en ejecución.
//
// Se procesa fichero a fichero y comprobando `wasForgotten()`: cambiar un nodo
// invalida a sus hermanos en el árbol de ts-morph, y sin esa comprobación el
// recorrido muere en el segundo parámetro que toca.
import { Project, SyntaxKind } from "ts-morph";

// Por ruta y no por tsconfig: la configuración de un repo real puede no
// enumerar los fuentes (referencias de proyecto, includes por paquete), y
// entonces el recorrido sale vacío sin decir por qué.
const proyecto = new Project({ compilerOptions: { allowJs: false } });
proyecto.addSourceFilesAtPaths([process.argv[2], "!**/node_modules/**"]);
console.log(`  ficheros cargados: ${proyecto.getSourceFiles().length}`);
let params = 0, retornos = 0, vars = 0;

for (const f of proyecto.getSourceFiles()) {
  if (f.getFilePath().includes("node_modules")) continue;

  // Los parámetros, de atrás hacia adelante y saltando los ya invalidados.
  const ps = f.getDescendantsOfKind(SyntaxKind.Parameter).reverse();
  for (const p of ps) {
    if (p.wasForgotten()) continue;
    try { if (p.getTypeNode()) { p.setType("any"); params++; } } catch {}
  }
  for (const k of [SyntaxKind.FunctionDeclaration, SyntaxKind.MethodDeclaration,
                   SyntaxKind.ArrowFunction, SyntaxKind.FunctionExpression]) {
    for (const fn of f.getDescendantsOfKind(k).reverse()) {
      if (fn.wasForgotten()) continue;
      try { if (fn.getReturnTypeNode()) { fn.setReturnType("any"); retornos++; } } catch {}
    }
  }
  for (const v of f.getDescendantsOfKind(SyntaxKind.VariableDeclaration).reverse()) {
    if (v.wasForgotten()) continue;
    try { if (v.getTypeNode()) { v.setType("any"); vars++; } } catch {}
  }
  f.saveSync();
}
console.log(`  a any: ${params} parametros, ${retornos} retornos, ${vars} variables`);
