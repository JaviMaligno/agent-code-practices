"""Propone tareas y se queda solo con las que el árbol intacto puede pagar.

El filtro que faltaba: además de exigir que el fallo rompa unos tests concretos y
pocos, se le pide al agente que la resuelva en el árbol LIMPIO y se descarta si
le cuesta más de la mitad del presupuesto. Una tarea que ya roza el techo sin
degradar nada no puede medir una degradación.

Cuesta caro —cada candidata paga un ensayo completo del agente— y por eso no
estaba. Salió más caro no tenerlo: dos bloques de la campaña se publicaron como
zonas muertas por tareas que este filtro habría rechazado.
"""
import json
import sys
from pathlib import Path
from statistics import median

from acp.agent.loop import solve
from acp.campaign import cell_oracle, cell_tree, clean_tree_name
from acp.tasks.inject import inject_fault
from acp.tasks.models import Task
from acp.tasks.mutations import FORMS
from acp.tasks.validate import (
    BASELINE_TURN_BUDGET, SuiteSession, affordable_for_baseline,
    logical_cases, within_budget,
)

REPO = Path(sys.argv[1])
ESTRATO = sys.argv[2] if len(sys.argv) > 2 else "generic"
QUIERO = int(sys.argv[3]) if len(sys.argv) > 3 else 6
MODELO = "gpt-5.4-mini"
ENSAYOS = 3


def candidatas(root: Path):
    """Funciones con ramas, de las más simples a las más enrevesadas.

    El orden importa y costó una tanda entera en hono: empezando por las más
    complejas salen tareas que nadie resuelve, y eso mide la dificultad de la
    tarea en vez de al agente.
    """
    import ast
    salida = []
    for py in sorted(root.rglob("*.py")):
        if any(p in py.parts for p in ("test", "tests", ".git", "build")):
            continue
        try:
            arbol = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.FunctionDef):
                continue
            ramas = sum(1 for n in ast.walk(nodo) if isinstance(n, (ast.If, ast.IfExp)))
            if ramas:
                modulo = str(py.relative_to(root)).replace("/", ".")[:-3]
                salida.append((ramas, modulo, nodo.name))
    salida.sort()
    return salida


print(f"  {REPO.name}: buscando {QUIERO} tareas ({ESTRATO})", flush=True)
cands = candidatas(REPO)
print(f"  {len(cands)} funciones candidatas", flush=True)

validas, intentos, caras = [], 0, 0
with SuiteSession(repo=REPO, image="python:3.12", timeout=3600) as sesion:
    sano = sesion.outcomes()
    print(f"  árbol sano: {len(sano)} tests", flush=True)

    for ramas, modulo, simbolo in cands:
        if len(validas) >= QUIERO:
            break
        for forma in FORMS:
            if len(validas) >= QUIERO:
                break
            try:
                parche = inject_fault(REPO, modulo, simbolo, forma)
            except Exception:
                continue
            if not parche:
                continue
            intentos += 1
            tarea = Task(task_id=f"tmp", repo=REPO.name, module=modulo, symbol=simbolo,
                         stratum=ESTRATO, patch=parche, fail_to_pass=[])
            try:
                with SuiteSession(repo=REPO, image="python:3.12", timeout=3600) as s2:
                    s2.write(modulo.replace(".", "/") + ".py", parche)
                    roto = s2.outcomes()
            except Exception:
                continue
            o = cell_oracle(sano, roto)
            if not o.measurable or not within_budget(o.fail_to_pass):
                continue

            # El ensayo: ¿el árbol limpio la resuelve con margen?
            turnos = []
            for _ in range(ENSAYOS):
                try:
                    with SuiteSession(repo=REPO, image="python:3.12", timeout=3600) as s3:
                        s3.write(modulo.replace(".", "/") + ".py", parche)
                        traza = solve(s3, f"Arregla el fallo. Tests en rojo: {o.fail_to_pass[:6]}",
                                      MODELO, max_turns=40)
                        turnos.append(traza.turns)
                except Exception:
                    turnos.append(40)
            asequible = affordable_for_baseline(turnos)
            caras += not asequible
            print(f"    {modulo}:{simbolo} [{forma}] {logical_cases(o.fail_to_pass)} casos, "
                  f"turnos {turnos} -> {'VALE' if asequible else 'demasiado cara'}", flush=True)
            if not asequible:
                continue
            tid = f"{REPO.name}-{'d' if ESTRATO=='domain' else ''}{len(validas)+1:03d}"
            destino = Path(f"tasks/{REPO.name}"); destino.mkdir(parents=True, exist_ok=True)
            json.dump({"task_id": tid, "repo": REPO.name, "module": modulo, "symbol": simbolo,
                       "stratum": ESTRATO, "patch": parche,
                       "fail_to_pass": o.fail_to_pass, "pass_to_pass": o.pass_to_pass[:20],
                       "min_files_to_judge": 1,
                       "baseline_turns": turnos},
                      open(destino / f"{tid}.json", "w"), indent=2)
            validas.append(tid)
            print(f"      -> {tid}", flush=True)

print(f"\n  {len(validas)} tareas de {intentos} intentos ({caras} rechazadas por caras): {validas}")
