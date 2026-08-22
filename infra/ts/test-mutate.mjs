// Comprobaciones del mutador de TypeScript, con el mismo criterio que el de
// Python: la mutación tiene que cambiar el comportamiento y seguir compilando.
// Un fallo que no compila no es una tarea, es un árbol roto.
import { mutar } from "./mutate.mjs";
import assert from "node:assert";

const casos = [
  {
    nombre: "invert_condition",
    antes: "export function check(n: number): boolean {\n  if (n > 10) {\n    return true\n  }\n  return false\n}",
    contiene: "!(n > 10)",
  },
  {
    nombre: "off_by_one",
    antes: "export function check(s: string): boolean {\n  if (s.length === 5) {\n    return true\n  }\n  return false\n}",
    contiene: "=== 6",
  },
  {
    nombre: "drop_null_check",
    antes: "export function name(u: User): string {\n  if (!u) {\n    return ''\n  }\n  return u.name\n}",
    noContiene: "if (!u)",
  },
];

let fallos = 0;
for (const c of casos) {
  const salida = mutar(c.antes, "check", c.nombre) ?? mutar(c.antes, "name", c.nombre);
  if (salida === null) { console.log(`  ${c.nombre}: NO aplicó`); fallos++; continue; }
  if (salida === c.antes) { console.log(`  ${c.nombre}: no cambió nada`); fallos++; continue; }
  if (c.contiene && !salida.includes(c.contiene)) {
    console.log(`  ${c.nombre}: falta ${c.contiene}\n${salida}`); fallos++; continue;
  }
  if (c.noContiene && salida.includes(c.noContiene)) {
    console.log(`  ${c.nombre}: sigue teniendo ${c.noContiene}`); fallos++; continue;
  }
  console.log(`  ${c.nombre}: OK`);
}
process.exit(fallos ? 1 : 0);
