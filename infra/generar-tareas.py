"""Propone tareas y se queda solo con las que el árbol intacto puede pagar.

El filtro que faltaba: además de exigir que el fallo rompa unos tests concretos y
pocos, se le pide al agente que lo arregle en el árbol LIMPIO y se descarta la
tarea si le cuesta más de la mitad del presupuesto. Una tarea que ya roza el
techo sin degradar nada no puede medir una degradación.

Cuesta caro —cada candidata paga ensayos completos del agente— y por eso no
estaba. Salió más caro no tenerlo: dos bloques de la campaña se publicaron como
zonas muertas por tareas que este filtro habría rechazado.

Se apoya en `measure_cell`, que es el mismo circuito que usa la campaña. Duplicar
la lógica aquí habría significado validar las tareas con un procedimiento
distinto del que luego las mide.
"""
import ast
import json
import sys
from pathlib import Path

from acp.agent.loop import solve
from acp.campaign import measure_cell, task_prompt
from acp.tasks.inject import inject
from acp.tasks.mutations import mutate
from acp.tasks.validate import (
    BASELINE_TURN_BUDGET, SuiteSession, affordable_for_baseline,
    logical_cases, within_budget,
)

REPO = Path(sys.argv[1]).resolve()
ESTRATO = sys.argv[2] if len(sys.argv) > 2 else "generic"
QUIERO = int(sys.argv[3]) if len(sys.argv) > 3 else 6
MODELO = "gpt-5.4-mini"
ENSAYOS = 3
FORMAS = ("invert_condition", "off_by_one", "drop_none_check", "swap_args")
FORMAS_TS = ("invert_condition", "off_by_one", "drop_null_check", "swap_args")
TRABAJO = Path(sys.argv[4] if len(sys.argv) > 4 else "work-gen").resolve()
# "node" para repositorios TypeScript: cambian el buscador de candidatas, el
# mutador y la sesión de suite; el filtro de coste y el oráculo son los mismos.
LENGUAJE = sys.argv[5] if len(sys.argv) > 5 else "python"
GESTOR = sys.argv[6] if len(sys.argv) > 6 else "bun"
IMAGEN = "node:22" if LENGUAJE == "node" else "python:3.12"
TS = Path(__file__).resolve().parent / "ts"


def _abrir(arbol, tests_from=None):
    if LENGUAJE == "node":
        from acp.node_suite import NodeSuiteSession

        return NodeSuiteSession(repo=arbol, image=IMAGEN, timeout=3600,
                                package_manager=GESTOR)
    return SuiteSession(repo=arbol, image=IMAGEN, timeout=3600, tests_from=tests_from)


def candidatas_ts(root: Path) -> list[tuple[int, str, str]]:
    """Lo mismo que `candidatas`, pero preguntándoselo a ts-morph."""
    import subprocess

    salida = subprocess.run(
        ["node", "candidatos.mjs", f"{root}/src/**/*.ts"],
        cwd=TS, capture_output=True, text=True, timeout=600,
    )
    if salida.returncode != 0:
        raise RuntimeError(f"candidatos.mjs falló: {salida.stderr[-300:]}")
    return [
        (c["ramas"], c["fichero"].split(f"/{root.name}/")[-1], c["simbolo"])
        for c in json.loads(salida.stdout)
    ]


def mutar_ts(root: Path, relativo: str, simbolo: str, forma: str) -> str | None:
    """El fichero completo ya mutado, o None si la forma no aplica."""
    import subprocess

    guion = (
        "import('./mutate.mjs').then(async (m) => {"
        "const fs = await import('node:fs');"
        f"const src = fs.readFileSync({json.dumps(str(root / relativo))}, 'utf8');"
        f"const out = m.mutar(src, {json.dumps(simbolo)}, {json.dumps(forma)});"
        "if (out) process.stdout.write(out); });"
    )
    r = subprocess.run(["node", "-e", guion], cwd=TS,
                       capture_output=True, text=True, timeout=600)
    return r.stdout or None


def candidatas(root: Path) -> list[tuple[int, str, str]]:
    """Funciones con ramas, de las más simples a las más enrevesadas.

    El orden importa y costó una tanda entera en hono: empezando por las más
    complejas salen tareas que nadie resuelve, y eso mide la dificultad de la
    tarea en vez de al agente.
    """
    salida = []
    for py in sorted(root.rglob("*.py")):
        partes = set(py.parts)
        if partes & {"test", "tests", ".git", "build", "docs", "benchmarks"}:
            continue
        try:
            arbol = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        modulo = str(py.relative_to(root))[:-3].replace("/", ".")
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef) and not nodo.name.startswith("_"):
                ramas = sum(1 for n in ast.walk(nodo) if isinstance(n, (ast.If, ast.IfExp)))
                if 1 <= ramas <= 6:
                    salida.append((ramas, modulo, nodo.name))
    salida.sort()
    return salida


print(f"  {REPO.name}: buscando {QUIERO} tareas de estrato '{ESTRATO}'", flush=True)
cands = candidatas_ts(REPO) if LENGUAJE == "node" else candidatas(REPO)
print(f"  {len(cands)} funciones candidatas (1-6 ramas)", flush=True)

TRABAJO.mkdir(parents=True, exist_ok=True)
validas, intentos, caras = [], 0, 0

with _abrir(REPO) as limpia:
    sano = limpia.outcomes()
    print(f"  árbol sano: {len(sano)} tests con veredicto", flush=True)

for ramas, modulo, simbolo in cands:
    if len(validas) >= QUIERO:
        break
    for forma in (FORMAS_TS if LENGUAJE == "node" else FORMAS):
        if len(validas) >= QUIERO:
            break
        try:
            if LENGUAJE == "node":
                mutado = mutar_ts(REPO, modulo, simbolo, forma)
                if not mutado:
                    continue
                from acp.tasks.models import Task as _T

                tarea = _T(task_id="cand", repo=REPO.name, module=modulo,
                           symbol=simbolo, stratum=ESTRATO, patch=mutado,
                           patch_is_full_file=True, fail_to_pass=[])
            else:
                tarea = inject(REPO, module=modulo, symbol=simbolo, kind=forma)
        except Exception:
            continue
        if tarea is None:
            continue
        intentos += 1
        tarea.task_id = f"cand-{intentos}"
        tarea.stratum = ESTRATO

        turnos, medible, casos = [], True, 0
        for i in range(ENSAYOS):
            try:
                registro = measure_cell(
                    REPO, "T0", [], tarea,
                    workdir=TRABAJO / f"{intentos}-{i}",
                    open_session=_abrir,
                    ask_agent=lambda sesion, prompt: solve(sesion, prompt, MODELO),
                    clean=sano,
                )
            except Exception as e:
                print(f"    {modulo}:{simbolo} [{forma}] reventó: {str(e)[:70]}", flush=True)
                medible = False
                break
            if not registro["measurable"]:
                medible = False
                break
            casos = logical_cases(registro.get("fail_to_pass") or [])
            turnos.append(registro.get("turns") or 40)

        if not medible or not turnos:
            continue
        if not within_budget(registro.get("fail_to_pass") or []):
            print(f"    {modulo}:{simbolo} [{forma}] rompe demasiado ({casos} casos)", flush=True)
            continue

        asequible = affordable_for_baseline(turnos)
        caras += not asequible
        print(f"    {modulo}:{simbolo} [{forma}] {casos} casos, turnos {turnos} -> "
              f"{'VALE' if asequible else f'cara (>{BASELINE_TURN_BUDGET})'}", flush=True)
        if not asequible:
            continue

        prefijo = "d" if ESTRATO == "domain" else ""
        tid = f"{REPO.name}-{prefijo}{len(validas)+1:03d}"
        tarea.task_id = tid
        destino = Path(f"tasks/{REPO.name}")
        destino.mkdir(parents=True, exist_ok=True)
        datos = tarea.to_json()
        datos["fail_to_pass"] = registro.get("fail_to_pass") or []
        datos["pass_to_pass"] = (registro.get("pass_to_pass") or [])[:20]
        datos["baseline_turns"] = turnos
        json.dump(datos, open(destino / f"{tid}.json", "w"), indent=2)
        validas.append(tid)
        print(f"      -> {tid}", flush=True)

print(f"\n  {len(validas)} tareas de {intentos} intentos "
      f"({caras} rechazadas por caras): {validas}")
