"""B2 — jerarquía: aplanar los directorios del paquete y renombrar los ficheros.

Lo que se destruye aquí es la señal de dónde mirar (§4.2 del spec): en un repo
con jerarquía, `stdnum/es/nif.py` te dice a la vez el país y el documento sin
abrir nada. Aplanado y renombrado a `stdnum/m17.py`, esa información ya no
existe y el agente tiene que ir a buscarla. Es la celda que se cruza con la
dotación pobre (§5.2): sin grep, encontrar el sitio depende exactamente de lo
que B2 quita.

El directorio del paquete raíz sobrevive (§5.6). Es lo único que mantiene
válidos a la vez la instalación de dependencias, los imports desde fuera y el
comando de test; lo que se aplana es todo lo de dentro.
"""

from __future__ import annotations

import configparser
import re
import shutil
import tomllib
from pathlib import Path

import libcst as cst

from acp.metrics.size import SOURCE_DIR, is_excluded_dir, is_test_file
from acp.metrics.size import module_name as _module_name
from acp.metrics.size import read_source
from acp.transforms.base import (
    PYTEST_CONFIG_FILES,
    TransformResult,
    iter_transformable_files,
)
from acp.transforms.doctests import DOCTEST_PROMPT, doctest_files, rewrite_examples


def _package_root(root: Path) -> Path | None:
    """El directorio del paquete, que es lo único que no se aplana.

    Se exige que haya exactamente un candidato: con dos paquetes de primer nivel
    no está claro cuál es el punto de entrada que hay que conservar, y aplanar el
    equivocado deja el repo sin forma de importarse. Sin candidato claro, B2 no
    hace nada y la celda se declara como no aplicable a ese repo.

    Candidato no es "directorio con `__init__.py`": la suite y las utilidades
    del repositorio también lo tienen. Medido sobre el sustrato, contarlas
    dejaba sin paquete raíz a los dos repos que empaquetan sus tests —sqlglot
    (`benchmarks/`, `sqlglot/`, `tests/`) y holidays (`holidays/`, `scripts/`,
    `tests/`)—, o sea árbol idéntico, celda en verde y dosis cero, que es el
    fallo más caro que declara este módulo. Con el criterio bueno, sqlglot pasa
    de 0 a 104 módulos movidos; holidays sigue a cero, pero ya por la otra
    guarda y con razón —su propia suite construye los nombres de módulo con
    `f"holidays.{prefix}.{module_name}"`, así que 313 de sus 329 módulos son
    inalcanzables por ruta—, y esa diferencia es justo la que había que poder
    ver. Quién es código del repo y quién no ya lo decide `acp.metrics.size`
    para las métricas de fase 0, y es la misma pregunta: se reutiliza su
    criterio en vez de inventar otro, porque dos respuestas distintas a la misma
    pregunta es justo lo que produjo esto.
    """
    package = _single_package_in(root)
    if package is not None:
        return package
    # Layout `src/`: ahí no hay NINGÚN candidato en la raíz —el paquete cuelga
    # de un directorio que no es paquete—, así que el criterio de arriba no
    # devolvía nada y B2 se volvía un no-op silencioso en una de las dos formas
    # de repo más comunes que hay. Solo se mira cuando la raíz no ofrece
    # ninguno: con dos candidatos arriba el repo es ambiguo, y elegir el de
    # `src/` sería adivinar en vez de declararlo no aplicable.
    source = root / SOURCE_DIR
    if source.is_dir() and not (source / "__init__.py").exists():
        return _single_package_in(source)
    return None


def _single_package_in(directory: Path) -> Path | None:
    """El único directorio de `directory` que es código del repo, si es uno."""
    candidates = [
        path
        for path in sorted(directory.iterdir())
        if path.is_dir()
        and (path / "__init__.py").exists()
        and not is_excluded_dir(path.name)
    ]
    return candidates[0] if len(candidates) == 1 else None


# El nombre del módulo se calcula relativo a la raíz del árbol porque es lo que
# hay en `sys.path` cuando la suite corre —el repo se alcanza por ruta, no por
# instalación (§5.6)—, y se comparte con `build_symbol_map`: es la clave con la
# que el mapa de identidad sigue los movimientos que se anuncian aquí, así que
# las dos formas tienen que ser la misma función, no dos que se parezcan.

DYNAMIC_IMPORTERS = ("__import__", "import_module")


def _literal_head(node: cst.BaseExpression, module: str = "") -> str | None:
    """La parte fija de un nombre de módulo que se termina de construir al correr.

    None cuando el nombre es literal entero —ahí no hay nada dinámico— o cuando
    no se puede leer nada fijo. Una cadena vacía significa «podría ser
    cualquiera», y eso no es evidencia de que alcance a este repo: pint importa
    clases de terceros con `import_module(module_name)`, y tratar eso como una
    amenaza dejaría sin aplicar B2 al único finalista con jerarquía profunda.

    `module` es el nombre del fichero que se está leyendo, y hace falta por un
    hueco que sí es fijo aunque no lo parezca: `f"{__name__}.{name}"`. Eso no es
    «cualquier módulo», es este módulo hablando de sus propios hijos —la
    evidencia más fuerte que hay de que aquí el árbol de directorios es la tabla
    de búsqueda—. Medido sobre sqlglot, cuyo `sqlglot/optimizer/__init__.py` lo
    usa como fallback de `__getattr__`: leído como hueco cualquiera, B2 lo
    aplanaba, `__name__` pasaba a ser `sqlglot.m66`, el submódulo construido no
    existía y el `__getattr__` se llamaba a sí mismo hasta el RecursionError.
    1.225 tests a 0 en la colecta, medido en contenedor.
    """
    if isinstance(node, cst.BinaryOperation) and isinstance(node.left, cst.SimpleString):
        text = node.left.raw_value
        if isinstance(node.operator, cst.Modulo):
            return text.split("%")[0]
        if isinstance(node.operator, cst.Add):
            return text
    if (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and node.func.attr.value == "format"
        and isinstance(node.func.value, cst.SimpleString)
    ):
        return node.func.value.raw_value.split("{")[0]
    if isinstance(node, cst.FormattedString):
        head = ""
        for part in node.parts:
            if isinstance(part, cst.FormattedStringText):
                head += part.value
                continue
            if module and _is_own_module_name(part):
                head += module
                continue
            return head
        return None
    return None


def _is_own_module_name(part: cst.BaseFormattedStringContent) -> bool:
    """Si este hueco de la f-string es `{__name__}`, o sea el módulo mismo."""
    return (
        isinstance(part, cst.FormattedStringExpression)
        and isinstance(part.expression, cst.Name)
        and part.expression.value == "__name__"
    )


class _CollectComputedPrefixes(cst.CSTVisitor):
    def __init__(self, module: str = "") -> None:
        self.prefixes: set[str] = set()
        # El módulo que se está leyendo: es lo que vale `__name__` cuando corra.
        self.module = module

    def visit_Call(self, node: cst.Call) -> None:
        name = node.func.attr.value if isinstance(node.func, cst.Attribute) else None
        if isinstance(node.func, cst.Name):
            name = node.func.value
        if name not in DYNAMIC_IMPORTERS or not node.args:
            return
        head = _literal_head(node.args[0].value, self.module)
        # La cadena vacía es «cualquier módulo»: sin prefijo fijo no hay nada
        # que diga que esta llamada alcanza a este repo.
        if head:
            self.prefixes.add(head)


def computed_module_prefixes(root: Path) -> set[str]:
    """Prefijos desde los que el repo arma nombres de módulo en ejecución.

    Es público a propósito: un `moves` vacío tiene dos causas —no hay un paquete
    raíz claro, o el repo se busca a sí mismo por nombre construido— y la dosis
    real de una condición se declara con datos, no deduciéndola de un contador
    que marca cero.
    """
    found: set[str] = set()
    for path in iter_transformable_files(root):
        try:
            module = cst.parse_module(read_source(path))
        except cst.ParserSyntaxError:
            continue
        collector = _CollectComputedPrefixes(_module_name(path, root))
        module.visit(collector)
        found.update(collector.prefixes)
    return found


# Una ruta con puntos escrita dentro de un texto. Se busca así, y no partiendo
# por espacios, porque lo que interesa es la ruta aunque venga pegada a otra
# cosa: `<class 'sqlglot.expressions.query.Table'>` la trae entre comillas y
# corchetes angulares.
_DOTTED_IN_TEXT = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")


# Lo que en un fichero de test es una AFIRMACIÓN y no maquinaria: la sentencia
# `assert` y las llamadas de la familia `assert*` —`assertEqual` de unittest,
# `assert_called_once_with` de mock—. Es donde la suite dice «esto tiene que ser
# verdad», que es lo único que la campaña lee como resultado.
_CLAIMING_CALL = re.compile(r"assert\w*")


def _is_a_claim(node: cst.Call) -> bool:
    if isinstance(node.func, cst.Attribute):
        return bool(_CLAIMING_CALL.fullmatch(node.func.attr.value))
    return isinstance(node.func, cst.Name) and bool(_CLAIMING_CALL.fullmatch(node.func.value))


class _CollectTextMentions(cst.CSTVisitor):
    """Rutas con puntos que la suite escribe y nadie puede reescribir por ella.

    Son dos, y las dos atan al módulo a su sitio:

    - **Dentro de una frase**: `<class 'sqlglot.expressions.query.Table'>`. Ahí
      nadie reescribe —hacerlo sería B3 dentro de B2— y el texto se quedaría
      nombrando un módulo que se movió.
    - **Dentro de una aserción**, aunque la cadena sea exactamente la ruta.
      Fuera de una aserción esa cadena es maquinaria —el objetivo de un `patch`,
      el nombre que decide qué se colecta— y `_module_reference` la reescribe
      porque §4.3.1 obliga: sin eso la suite no llega al código y no compila.
      Dentro de una aserción es el ORÁCULO, y reescribirla mueve la expectativa
      con el programa: el test pasa porque se cambió el test, y la equivalencia
      «la suite da el mismo resultado» se vuelve una tautología. Reproducido:
      B5 absorbió un módulo y reescribió `assert who_now() == 'pkg.zzz_named'`
      al nombre del anfitrión, con la suite en verde antes y después mientras el
      valor observable había cambiado.

      La salida no es dejar la cadena quieta —sería la suite en rojo por algo
      que causó la transformación, o sea un repo roto que se lee como un agente
      que fracasa (§5.6)—: es no mover el módulo, que es exactamente lo que ya
      se hace cuando la ruta viene dentro de una frase. La dosis baja se declara.

    Es la misma línea que `rewrite_examples` ya traza en los doctests: reescribe
    el código del ejemplo —que es maquinaria— y nunca su salida esperada, que es
    lo que el ejemplo afirma.

    **Fuera de alcance, declarado (§11)**: la expectativa que no está escrita en
    la aserción sino guardada antes (`esperado = 'pkg.mod'` … `assert x ==
    esperado`) y la que viaja en un `match=` de `pytest.raises`. Atarlas a su
    aserción exige seguir el dato y no la sintaxis, y hoy ninguna de las dos
    tiene dosis.

    El censo que sostiene la línea, contado sobre las suites de los cuatro
    finalistas —cadenas que son exactamente una ruta de módulo del propio repo—:

        pint            6   todas objetivo de un `patch`
        sqlglot        22   18 `patch`, 1 `startswith`, 1 la comparación que
                            decide qué se colecta, 2 dentro de una aserción
        python-stdnum   0
        holidays       11   9 `patch`, 2 el cargador de entidades

    O sea 35 de maquinaria y 2 de oráculo, cero guardadas en una variable y cero
    en un `match=`. Las dos de oráculo son
    `mock_dict.get.assert_called_once_with("sqlglot.dialects", [])`, y ese no es
    un módulo que B2 mueva —lo sujeta el nombre construido al correr—, así que
    la regla no cambia hoy ni un árbol: es el guardarraíl de que no lo haga
    mañana.
    """

    def __init__(self) -> None:
        self.mentions: set[str] = set()
        # Anidamiento y no un booleano: `self.assertEqual(...)` puede vivir
        # dentro de un `assert`, y salir de la de dentro no sale de la de fuera.
        self._claims = 0

    def visit_Assert(self, node: cst.Assert) -> None:
        self._claims += 1

    def leave_Assert(self, node: cst.Assert) -> None:
        self._claims -= 1

    def visit_Call(self, node: cst.Call) -> None:
        if _is_a_claim(node):
            self._claims += 1

    def leave_Call(self, node: cst.Call) -> None:
        if _is_a_claim(node):
            self._claims -= 1

    def visit_SimpleString(self, node: cst.SimpleString) -> None:
        text = node.raw_value
        if _DOTTED_IN_TEXT.fullmatch(text):
            if self._claims:
                self.mentions.add(text)
            return
        self.mentions.update(_DOTTED_IN_TEXT.findall(text))


def modules_named_by_the_suite(root: Path) -> set[str]:
    """Rutas de módulo que la suite escribe dentro de un texto o de una aserción.

    Es público por lo mismo que `computed_module_prefixes`: es una de las causas
    por las que un módulo no se mueve, y la dosis real se declara con datos.

    Solo cuenta lo que escribe la SUITE, y la diferencia no es de gusto. La
    suite es el oráculo: si compara un mensaje que lleva dentro el `repr` de una
    clase —sqlglot espera `<class 'sqlglot.expressions.query.Table'>`—, mover el
    módulo cambia el `__module__` de la clase y con él el veredicto; medido, 7
    tests en rojo donde el baseline no tenía ninguno. Una frase del código
    fuente que menciona un módulo no la compara nadie, y tratarla igual costaría
    la celda entera de pint: escribe rutas de módulo dentro de textos en 57 de
    sus 67 módulos movibles.
    """
    found: set[str] = set()
    for path in iter_transformable_files(root):
        if not is_test_file(path, root):
            continue
        try:
            module = cst.parse_module(read_source(path))
        except cst.ParserSyntaxError:
            continue
        collector = _CollectTextMentions()
        module.visit(collector)
        found.update(collector.mentions)
    return found


def _outside_the_symbol_map(path: Path, root: Path) -> bool:
    """Si mover este fichero sería un movimiento que nadie puede acreditar.

    Es lo que el repositorio guarda DENTRO del paquete sin ser ni el código que
    se estudia ni la suite: `pkg/tools/`, `pkg/scripts/`, `pkg/benchmarks/`,
    `pkg/examples/`, `pkg/docs/`. `acp.metrics.size` ya los deja fuera de la
    muestra y de las métricas, y por tanto también del mapa de identidad; el
    mismo criterio es el que decide en `_package_root` quién puede ser el
    paquete raíz. Moverlos igual dejaba en la raíz del paquete un fichero con
    nombre opaco, indistinguible del código del repo y sin una sola entrada en
    el mapa que dijera de dónde salió.

    §5.4.2 mide la localización proyectando lo que el agente lee sobre ese mapa,
    así que un fichero legible que no está en él no se puede proyectar. La otra
    salida —meter su símbolo en el mapa— cambiaría la población de la que salen
    las tareas y sobre la que se midió la fase 0: sería cambiar el experimento
    para tapar un agujero de contabilidad. Y no se pierde dosis: ninguna tarea
    apunta ahí.

    La suite es lo contrario y por eso se pregunta antes: sus ficheros tampoco
    están en el mapa —son el oráculo, no el objetivo—, pero §4.3.1 los
    transforma con el resto del árbol, y pint tiene los suyos dentro del
    paquete. Esos sí se mueven.
    """
    if is_test_file(path, root):
        return False
    return any(is_excluded_dir(part) for part in path.relative_to(root).parts[:-1])


def plan_moves(root: Path) -> dict[str, str]:
    """Módulo original → módulo destino, todos colgando del paquete raíz.

    Determinista y por orden alfabético de ruta: la condición tiene que ser la
    misma en dos corridas distintas, o los resultados no se pueden comparar
    entre seeds.

    Los módulos que ya cuelgan del paquete también se renombran: si no, la mitad
    del árbol conserva sus nombres y B2 mide media dosis.
    """
    package = _package_root(root)
    if package is None:
        return {}

    # Un módulo al que el repo llega por un nombre que no existe hasta que corre
    # no se puede mover: no hay import que reescribir, porque no hay import.
    # python-stdnum despacha por código de país con `__import__('stdnum.%s' % cc)`
    # y su árbol de directorios *es* la tabla de búsqueda; medido, aplanarlo deja
    # 10 tests en rojo. Es el criterio de §4.3.3 —lo indecidible queda fuera— y
    # la política que dejó escrita la fase 1: se saca del diccionario lo que
    # rompa y se declara la dosis real.
    unreachable = computed_module_prefixes(root)
    pinned = modules_named_by_the_suite(root)
    scoped = _conftest_scopes(package)

    moves: dict[str, str] = {}
    index = 0
    for path in iter_transformable_files(root):
        if package not in path.parents:
            continue
        module = _module_name(path, root)
        # El paquete raíz es el punto de entrada y no se toca (§5.6).
        if module == package.name:
            continue
        if any(module.startswith(prefix) for prefix in unreachable):
            continue
        # Y tampoco el paquete DEL QUE cuelgan esos nombres. Sus hijos se quedan
        # donde están, así que llevarse el `__init__.py` a la raíz deja el
        # directorio convertido en un paquete de espacio de nombres, sin nada de
        # lo que ese fichero definía, mientras la cadena que se construye al
        # correr sigue apuntando ahí. Es la forma de `sqlglot/dialects/`.
        if any(prefix.startswith(f"{module}.") for prefix in unreachable):
            continue
        # Lo que la suite nombra dentro de un texto suyo o afirma dentro de
        # una aserción: mover el módulo cambiaría lo que el programa imprime y
        # ella compara, y reescribir la aserción para que cuadre convertiría la
        # verificación en una tautología.
        if any(name == module or name.startswith(f"{module}.") for name in pinned):
            continue
        if any(directory in path.parents for directory in scoped):
            continue
        if _outside_the_symbol_map(path, root):
            continue
        if _locates_itself(path):
            continue
        moves[module] = f"{package.name}.{_opaque_name(path, index)}"
        index += 1
    return moves


# Lo que un módulo usa para saber dónde está él mismo. `__name__` no entra: casi
# siempre es identidad —el nombre del logger— y excluir por él dejaría a B2 sin
# aplicar en casi todo el árbol.
SELF_LOCATING = ("__file__", "__package__")


def _locates_itself(path: Path) -> bool:
    """Si el módulo resuelve rutas contando desde su propia posición.

    pint carga su registro de unidades con
    `Path(__file__).parent.parent.parent / "default_en.txt"`: los tres saltos son
    la profundidad del fichero, o sea justo lo que B2 cambia. Movido a la raíz
    del paquete, la cuenta se sale del árbol. Medido sobre el clon: 2.024 tests
    pasan a 623 con 1.289 errores, todos de ahí. Ajustar los saltos sería
    adivinar —hay muchas formas de escribir esa cuenta y equivocarse rompe en
    silencio—, así que el módulo se queda donde está y se declara la dosis.
    """
    source = read_source(path)
    return any(marker in source for marker in SELF_LOCATING)


def _conftest_scopes(package: Path) -> set[Path]:
    """Directorios cuya posición es una declaración para el ejecutor de tests.

    Un `conftest.py` no es un módulo cualquiera: pytest lo busca por nombre
    exacto y su directorio decide qué tests ven sus fixtures. Moverlo cambia ese
    alcance y renombrarlo lo hace invisible, así que ni él ni lo que cuelga de
    su directorio se aplanan. pint tiene dos dentro del paquete.
    """
    return {path.parent for path in package.rglob("conftest.py")}


def _opaque_name(path: Path, index: int) -> str:
    """El nombre nuevo del módulo, opaco pero todavía colectable.

    pint tiene sus 35 ficheros de test dentro del paquete, y pytest los colecta
    por el prefijo del nombre: renombrarlos a `mN.py` no los esconde, los saca
    de la suite —cero tests, ningún fallo—. Se conserva solo lo que la
    herramienta lee, igual que A2 no renombra las funciones de test y A4 no
    borra los comentarios que lee una herramienta. Lo que decía de qué trata el
    fichero se pierde igual, que es la dosis de B2.
    """
    if path.name.startswith("test_"):
        return f"test_m{index}"
    if path.stem.endswith("_test"):
        return f"m{index}_test"
    return f"m{index}"


def _dotted(node: cst.BaseExpression) -> str:
    """La forma con puntos de un `a.b.c`, o vacío si no es un nombre con puntos."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr.value}" if base else ""
    return ""


def _moved_dotted(text: str, moves: dict[str, str]) -> str | None:
    """La misma ruta con puntos, apuntando a donde fue a parar su módulo.

    None cuando no hay ningún módulo movido dentro. Se busca el prefijo **más
    largo** que sea un módulo: en `pkg.es.nif.validate` lo que se sustituye es
    `pkg.es.nif` —el fichero— y el `.validate` de la cola se queda como está,
    porque es un nombre definido dentro, no una ruta.

    Vive suelta porque la misma pregunta se hace desde dos sitios que no
    comparten contexto: dentro de un `.py` (`_module_reference`) y en los
    ficheros de empaquetado (`_rewrite_entry_points`), que no son Python.
    """
    parts = text.split(".")
    if not all(part.isidentifier() for part in parts):
        return None
    for cut in range(len(parts), 0, -1):
        target = moves.get(".".join(parts[:cut]))
        if target is not None:
            return ".".join([target, *parts[cut:]])
    return None


def _containing_package(path: Path, root: Path) -> str:
    """El paquete al que pertenece el fichero, en forma de módulo con puntos.

    Es lo que hace falta para resolver un import relativo: `from ..util import
    clean` no significa nada sin saber desde dónde se cuenta.

    Se pregunta por el `__init__.py` del directorio en vez de recortar la ruta a
    mano para que la respuesta la dé la MISMA función que nombra los módulos: en
    layout `src/` el paquete de `src/pkg/es/nif.py` es `pkg.es`, y contarlo por
    partes desde la raíz daría `src.pkg.es`, o sea un import relativo que se
    resuelve fuera del paquete y no se reescribe.
    """
    # El `__init__` no está *en* su paquete: es su paquete.
    return _module_name(path.parent / "__init__.py", root)


class _RewriteImports(cst.CSTTransformer):
    """Reescribe los imports para que apunten a donde va a estar cada módulo.

    Se hace antes de mover nada: el diccionario de destinos ya está decidido, y
    reescribir primero evita tener que reconstruirlo leyendo un árbol a medio
    mover.

    Los alias locales se conservan (`from pkg.es import nif` sale como
    `from pkg import m3 as nif`) por dos razones. La primera es que sin ellos el
    repo no arranca: el nombre corto está usado en el cuerpo del fichero. La
    segunda es que ahí no está la dosis de B2. Lo que B2 destruye es la señal de
    **qué fichero abrir** —el árbol ya no dice dónde está nada—; el nombre con
    el que un fichero ya abierto llama a lo que importa es materia de A2, y las
    dos condiciones se miden por separado y se pueden cruzar.
    """

    def __init__(
        self,
        moves: dict[str, str],
        package: str,
        current: str,
        stationary: frozenset[str] = frozenset(),
    ) -> None:
        self.moves = moves
        self.package = package
        # Módulos del paquete que NO se mueven: hay que poder distinguirlos de
        # un nombre cualquiera, porque se siguen alcanzando por su ruta de antes.
        self.stationary = stationary
        # El paquete desde el que se cuentan los puntos de un import relativo.
        self.current = current
        # Los imports relativos solo se resuelven dentro del paquete: un
        # `from . import x` en un directorio de tests de la raíz sigue siendo
        # válido después, porque ese fichero no se mueve.
        self.inside = current == package or current.startswith(f"{package}.")

    # Los hijos de un import no se visitan: la ruta de módulo se reescribe
    # entera aquí, con el contexto del import, y dejar que `leave_Attribute`
    # la tocara antes haría que la búsqueda en el diccionario ya no encontrara
    # nada.
    def visit_Import(self, node: cst.Import) -> bool:
        return False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        return False

    def leave_SimpleString(
        self, original: cst.SimpleString, updated: cst.SimpleString
    ) -> cst.SimpleString:
        """Las dos cosas que puede haber dentro de una cadena y hay que seguir.

        Una ruta de módulo escrita a mano —ver `_module_reference`—, y los
        ejemplos de doctest. Un doctest no es documentación: es suite.
        `stdnum/__init__.py` importa `stdnum.isbn` desde un ejemplo de su propia
        docstring, y el paquete raíz no se mueve pero el módulo que importa sí.
        Dejar el ejemplo atrás convierte un test en un fallo, y la condición se
        leería como un repositorio roto.
        """
        rewritten_path = self._module_reference(updated.raw_value)
        if rewritten_path is not None:
            return updated.with_changes(
                value=f"{updated.prefix}{updated.quote}{rewritten_path}{updated.quote}"
            )
        if DOCTEST_PROMPT not in updated.value:
            return updated
        # Se opera sobre el literal entero, comillas incluidas: los prompts van
        # por dentro, así que los escapes quedan intactos.
        rewritten = rewrite_examples(updated.value, self.rewrite_snippet)
        return updated if rewritten == updated.value else updated.with_changes(value=rewritten)

    def _module_reference(self, text: str) -> str | None:
        """La misma cadena con la ruta de módulo actualizada, o None si no lo es.

        Cubre dos formas de la misma cosa. La cadena que es exactamente un
        módulo: `stdnum/gs1_128.py` guarda las suyas en un diccionario y se las
        pasa a `__import__`. Y la que es un módulo más un atributo suyo: pint
        parchea con `patch("pint.compat.upcast_type_names")`, que `mock` resuelve
        importando `pint.compat`. Las dos resuelven estáticamente, así que
        §4.3.3 no las excluye —excluye lo indecidible— y es el criterio con el
        que A2 ya sigue las cadenas de `__all__`.

        Se exige que la cadena entera sea una cadena de identificadores con
        puntos, y se busca el prefijo de módulo **más largo**: una frase que
        menciona el módulo es documentación, y reescribirla sería B3 colándose
        dentro de B2.

        Aquí sí se exigen dos partes como mínimo: dentro de un `.py`, una
        palabra suelta que coincidiera con un módulo casi nunca es una ruta.
        """
        if len(text.split(".")) < 2:
            return None
        return _moved_dotted(text, self.moves)

    def rewrite_snippet(self, code: str) -> str | None:
        """El mismo reescrito sobre un trozo suelto, o None si no cuela.

        LibCST valida al construir el nodo, y esa excepción no es de parseo: sin
        capturarla, un ejemplo raro dejaría el fichero a medio transformar.
        """
        try:
            module = cst.parse_module(code)
            return module.visit(
                _RewriteImports(self.moves, self.package, self.current, self.stationary)
            ).code
        except (cst.ParserSyntaxError, cst.CSTValidationError):
            return None

    def leave_Attribute(
        self, original: cst.Attribute, updated: cst.Attribute
    ) -> cst.BaseExpression:
        """`stdnum.bic` usado como expresión, no dentro de un import.

        Es lo que deja un `import stdnum.bic` sin alias: lo que queda ligado es
        `stdnum`, y el módulo se nombra después por su ruta entera. Reescribir
        solo la sentencia de import dejaría todos esos usos apuntando a un
        módulo que ya no existe. Se resuelve de dentro afuera, así que en
        `pkg.es.nif.validate` la cadena que se sustituye es `pkg.es.nif` y el
        `.validate` de fuera se queda donde está.

        La consulta se hace sobre el nodo **original** porque LibCST resuelve de
        dentro afuera y el hijo ya viene sustituido: en `pkg.es.nif`, el `pkg.es`
        de dentro también es un módulo que se movió, y preguntando por el nodo
        ya reescrito la cadena entera dejaría de encontrarse. Preguntando por el
        original gana siempre la coincidencia más larga, que es la correcta: el
        módulo es el fichero, no el directorio que lo contenía.
        """
        target = self.moves.get(_dotted(original))
        return cst.parse_expression(target) if target else updated

    def leave_Import(self, original: cst.Import, updated: cst.Import) -> cst.Import:
        names = [
            alias.with_changes(name=cst.parse_expression(self.moves[dotted]))
            if (dotted := _dotted(alias.name)) in self.moves
            else alias
            for alias in updated.names
        ]
        return updated.with_changes(names=names)

    def _absolute_base(self, node: cst.ImportFrom) -> str | None:
        """De dónde importa esta sentencia, en absoluto, o None si no se sabe."""
        tail = _dotted(node.module) if node.module is not None else ""
        if not node.relative:
            return tail or None
        if not self.inside:
            return None
        parts = self.current.split(".")
        # Un punto es el paquete propio; cada punto de más sube uno.
        kept = len(parts) - (len(node.relative) - 1)
        if kept < 1:
            return None
        base = parts[:kept]
        return ".".join([*base, *tail.split(".")]) if tail else ".".join(base)

    def leave_ImportFrom(self, original: cst.ImportFrom, updated: cst.ImportFrom):
        base = self._absolute_base(updated)
        if base is None:
            return updated
        # Fuera del paquete no hay nada que reescribir, y un import relativo de
        # un fichero que no se mueve sigue siendo correcto tal cual.
        if not (base == self.package or base.startswith(f"{self.package}.")):
            return updated

        if isinstance(updated.names, cst.ImportStar):
            return _absolute_import_from(updated, self.moves.get(base, base))

        # Cada nombre de la lista puede tener que venir de un sitio distinto.
        # Un submódulo que se movió cuelga ahora del paquete raíz. Uno que NO se
        # movió sigue colgando de su directorio, así que hay que seguir yendo a
        # buscarlo por la ruta de antes: rebasarlo al destino del padre lo manda
        # a buscar un submódulo dentro de un fichero plano. Y un nombre normal
        # —algo definido en el `__init__`— viene de donde fuera ese `__init__`.
        moved, stationary, kept = [], [], []
        for alias in updated.names:
            full = f"{base}.{alias.name.value}"
            target = self.moves.get(full)
            if target is not None:
                moved.append(
                    alias.with_changes(
                        name=cst.Name(target.split(".")[-1]),
                        asname=alias.asname or cst.AsName(name=cst.Name(alias.name.value)),
                        comma=cst.MaybeSentinel.DEFAULT,
                    )
                )
            elif full in self.stationary:
                stationary.append(alias)
            else:
                kept.append(alias)

        # Nada que repartir: solo cambia de dónde viene. Se dejan los nombres
        # exactamente como estaban, comas y saltos de línea incluidos. Rehacer
        # la lista aplastaría un `from x import (\n  a,\n  b)` en una sola
        # línea, que es formato —o sea A3— colándose dentro de B2, y dentro de
        # un doctest cambiar el número de líneas invalida el ejemplo entero.
        if not moved and not stationary:
            return _absolute_import_from(updated, self.moves.get(base, base))

        statements = []
        if moved:
            statements.append(_absolute_import_from(updated, self.package, moved))
        if stationary:
            statements.append(_absolute_import_from(updated, base, stationary))
        if kept:
            statements.append(_absolute_import_from(updated, self.moves.get(base, base), kept))
        if len(statements) == 1:
            return statements[0]
        return cst.FlattenSentinel(statements)


def _absolute_import_from(node: cst.ImportFrom, base: str, names=None) -> cst.ImportFrom:
    """El mismo import, en forma absoluta y apuntando a `base`.

    Siempre absoluto: al aplanar, todos los ficheros pasan a colgar del paquete
    raíz, así que cualquier import relativo de más de un punto se saldría del
    paquete. El nombre del paquete raíz es lo único que sigue siendo válido
    (§5.6), y por eso es la referencia desde la que se reescribe todo.
    """
    changes = {"module": cst.parse_expression(base), "relative": []}
    if names is not None:
        # La última no lleva coma: `with_changes` no la quita sola.
        changes["names"] = [
            *[alias.with_changes(comma=cst.MaybeSentinel.DEFAULT) for alias in names[:-1]],
            names[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT),
        ]
    return node.with_changes(**changes)


def stationary_modules(root: Path, moves: dict[str, str]) -> frozenset[str]:
    """Los módulos del repo que B2 deja donde están.

    Su ruta sigue siendo la buena, así que hay que poder reconocerlos: son la
    diferencia entre `from pkg.m2 import loader` —que busca un submódulo dentro
    de un fichero plano— y `from pkg.deep.inner import loader`, que es donde
    `loader` sigue estando.
    """
    package = _package_root(root)
    if package is None:
        return frozenset()
    return frozenset(
        _module_name(path, root)
        for path in iter_transformable_files(root)
        if package in path.parents and _module_name(path, root) not in moves
    )


def _rewrite_file(
    path: Path, root: Path, moves: dict[str, str], package: str, stationary: frozenset[str]
) -> bool:
    source = read_source(path)
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return False
    rewriter = _RewriteImports(moves, package, _containing_package(path, root), stationary)
    transformed = module.visit(rewriter).code
    if transformed == source:
        return False
    path.write_text(transformed, encoding="utf-8")
    return True


def _module_path(base: Path, module: str) -> Path:
    """El fichero de un módulo, contando desde donde empieza su nombre.

    `base` es el directorio que contiene al paquete raíz —la raíz del árbol, o
    `src/` en un layout `src/`—: es el inverso exacto de `module_name`, y
    tomarlo de la raíz del árbol mandaría a `pkg/m0.py` un fichero que tiene que
    acabar en `src/pkg/m0.py`.
    """
    return base / Path(*module.split(".")).with_suffix(".py")


def _rewrite_configured_paths(root: Path, moves: dict[str, str]) -> int:
    """Las rutas de fichero que la configuración de la suite nombra.

    Un import roto se ve: falla un test. Una ruta rota en la configuración no,
    y es peor. python-stdnum ignora `stdnum/iso9362.py` por ruta —es un módulo
    que se sustituye a sí mismo en `sys.modules`—; al aplanar, esa ruta deja de
    existir, el `--ignore` no tapa nada, pytest lo colecta y la corrida entera
    muere en la colecta. Medido: 413 tests pasan a 0 sin que falle ninguno, y la
    condición se leería como un repositorio que el agente destrozó.
    """
    # Se sustituye la cola de la ruta, no la ruta entera desde la raíz del
    # árbol, y por eso vale igual en layout `src/`: el módulo `pkg.broken` es
    # `pkg/broken.py` dentro de `--ignore=src/pkg/broken.py`. Anclarlo al
    # prefijo `src/` no añadía nada —medido por mutación: ningún test cambia—
    # y se perdía la escritura relativa al propio `src/`.
    replacements = {
        "/".join(original.split(".")) + ".py": "/".join(target.split(".")) + ".py"
        for original, target in moves.items()
    }
    changed = 0
    for name in PYTEST_CONFIG_FILES:
        path = root / name
        if not path.exists():
            continue
        source = read_source(path)
        transformed = source
        for old, new in replacements.items():
            transformed = transformed.replace(old, new)
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return changed


# Donde se declara el empaquetado. Es el otro sitio, además de los imports, en
# el que el repo escribe el nombre de uno de sus módulos.
PACKAGING_FILES = ("pyproject.toml", "setup.cfg")

# El módulo de un entry point: lo que va delante de los dos puntos, o el valor
# entero si no los hay (`pkg.plugin`). Los extras (`pkg.mod:main [cli]`) quedan
# fuera del grupo, que es lo que se quiere: solo se toca el módulo.
_ENTRY_POINT_MODULE = re.compile(r"[A-Za-z_]\w*(?:\.\w+)*")


def _toml_entry_points(text: str) -> set[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return set()
    project = data.get("project") or {}
    groups = (project.get("entry-points") or {}).values()
    poetry = (data.get("tool") or {}).get("poetry") or {}
    tables = [project.get("scripts"), project.get("gui-scripts"), poetry.get("scripts"), *groups]
    return {
        value
        for table in tables
        if isinstance(table, dict)
        for value in table.values()
        if isinstance(value, str)
    }


def _cfg_entry_points(text: str) -> set[str]:
    # Sin interpolación: un `%` en cualquier otra sección de setup.cfg haría
    # estallar la lectura, y aquí solo se viene a mirar una sección.
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return set()
    values: set[str] = set()
    for section in parser.sections():
        if section != "options.entry_points":
            continue
        # Cada grupo es un valor multilínea con un `nombre = módulo:objeto`
        # por línea.
        for raw in parser[section].values():
            for line in (raw or "").splitlines():
                _, separator, target = line.partition("=")
                if separator and target.strip():
                    values.add(target.strip())
    return values


def _rewrite_entry_points(root: Path, moves: dict[str, str]) -> int:
    """Los módulos que el empaquetado nombra con puntos, no con barras.

    Es el mismo agujero que `_rewrite_configured_paths` tapa para las rutas de
    fichero, en la otra forma y en los mismos ficheros. pint declara
    `pint-convert = "pint.pint_convert:main"`; B2 mueve ese módulo a
    `pint/m61.py` y el script que instala pip queda con un `from
    pint.pint_convert import main` que ya no resuelve. Medido sobre el clon:
    `pip install -e .` va bien y `pint-convert 1m` muere con ModuleNotFoundError.

    Ningún test lo ve —la suite no ejecuta entry points—, así que sin esto la
    celda se declara equivalente con la interfaz pública del repo rota, que es
    justo lo que la comparación de suites no puede detectar.

    Se reescribe solo lo que el fichero **declara** como entry point: se lee con
    el parser del formato y se sustituye el valor exacto. La descripción del
    proyecto puede mencionar un módulo, y reescribir eso sería B3 dentro de B2.
    """
    readers = {"pyproject.toml": _toml_entry_points, "setup.cfg": _cfg_entry_points}
    changed = 0
    for name in PACKAGING_FILES:
        path = root / name
        if not path.exists():
            continue
        source = read_source(path)
        values = readers[name](source)
        transformed = "".join(
            _rewrite_declared_line(line, moves, values)
            for line in source.splitlines(keepends=True)
        )
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1
    return changed


def _written_value(tail: str) -> str:
    """El valor tal cual queda escrito a la derecha del `=`, sin comillas.

    Es lo que hay que comparar con lo que el parser declaró: el valor de un
    entry point se escribe entrecomillado en TOML y desnudo en `setup.cfg`, y la
    misma cadena tiene que reconocerse en los dos sitios.
    """
    written = tail.strip()
    if written[:1] in ('"', "'"):
        quote, rest = written[0], written[1:]
        end = rest.find(quote)
        return rest if end == -1 else rest[:end]
    return written


def _rewrite_declared_line(line: str, moves: dict[str, str], values: set[str]) -> str:
    """Una línea `clave = valor` del fichero de empaquetado.

    Se toca solo si el valor entero —lo que hay a la derecha del último `=`— es
    exactamente uno de los que el parser declaró como entry point. La alternativa
    que había, un `replace` de esa cadena sobre el fichero entero, no distingue
    la declaración de la prosa: una `description` que repita `pkg.cli:main` se
    reescribe igual, que es justo lo que el docstring de `_rewrite_entry_points`
    promete no hacer. Comparar el valor entero en su posición deja fuera tanto la
    mención dentro de una frase como la frase que sea idéntica al valor pero esté
    en otra clave.
    """
    body, newline = (line[:-1], line[-1:]) if line.endswith("\n") else (line, "")
    head, separator, tail = body.rpartition("=")
    if not separator:
        return line
    written = _written_value(tail)
    if written not in values:
        return line
    match = _ENTRY_POINT_MODULE.match(written)
    if match is None:
        return line
    moved = _moved_dotted(match.group(), moves)
    if moved is None:
        return line
    return head + separator + tail.replace(match.group(), moved, 1) + newline


# El tercer sitio donde un repo declara sus entry points, y el único que es
# Python: `setup.py` los pasa como argumento de `setup()`. No está en
# `PACKAGING_FILES` porque no se lee con un parser de formato — se lee con el
# mismo libcst que el resto del árbol.
SETUP_SCRIPT = "setup.py"
ENTRY_POINTS_KEYWORD = "entry_points"

# Un bloque de entry points trae una declaración por línea, y dentro de un
# literal de Python el salto puede estar escrito (`\n`) en vez de ser real.
_ENTRY_POINT_BREAK = re.compile(r"(\\n|\n)")


def _rewrite_entry_point_block(text: str, moves: dict[str, str]) -> str:
    """Un literal de `entry_points`, sea una línea o un bloque entero.

    Se trabaja sobre el texto **crudo** del literal, comillas y escapes
    incluidos, porque es lo que hay que devolverle a libcst sin tocar la forma en
    que estaba escrito. Lo único que se sustituye dentro es el módulo del valor.
    """
    return "".join(
        piece if _ENTRY_POINT_BREAK.fullmatch(piece) else _rewrite_entry_point_line(piece, moves)
        for piece in _ENTRY_POINT_BREAK.split(text)
    )


def _rewrite_entry_point_line(line: str, moves: dict[str, str]) -> str:
    """`nombre = módulo:objeto`, con lo que sobre de comillas a los lados."""
    head, separator, tail = line.rpartition("=")
    if not separator:
        return line
    written = tail.strip().lstrip("\"'")
    match = _ENTRY_POINT_MODULE.match(written)
    if match is None:
        return line
    moved = _moved_dotted(match.group(), moves)
    if moved is None:
        return line
    return head + separator + tail.replace(match.group(), moved, 1)


class _RewriteEntryPointStrings(cst.CSTTransformer):
    """Las cadenas que cuelgan del argumento `entry_points` de `setup()`.

    Qué es una declaración y qué es prosa lo decide aquí la posición, no un
    parser: dentro de ese argumento todo valor es un entry point, y fuera no se
    mira nada. Es la misma regla que en `pyproject.toml` —solo el valor
    declarado—, expresada con lo único que hay en un fichero de Python.

    El argumento admite las tres escrituras que usan los repos (diccionario de
    listas, diccionario de cadenas y bloque con formato ini en una sola cadena),
    y las tres se reducen a lo mismo: cada literal de cadena que cuelga de ahí
    lleva cero o más líneas `nombre = módulo:objeto`.
    """

    def __init__(self, moves: dict[str, str]) -> None:
        self.moves = moves
        self.inside = 0

    def visit_Arg(self, node: cst.Arg) -> None:
        if node.keyword is not None and node.keyword.value == ENTRY_POINTS_KEYWORD:
            self.inside += 1

    def leave_Arg(self, original: cst.Arg, updated: cst.Arg) -> cst.Arg:
        if original.keyword is not None and original.keyword.value == ENTRY_POINTS_KEYWORD:
            self.inside -= 1
        return updated

    def leave_SimpleString(
        self, original: cst.SimpleString, updated: cst.SimpleString
    ) -> cst.BaseExpression:
        if not self.inside:
            return updated
        rewritten = _rewrite_entry_point_block(updated.value, self.moves)
        return updated if rewritten == updated.value else updated.with_changes(value=rewritten)


def _rewrite_setup_script(root: Path, moves: dict[str, str]) -> int:
    """Los entry points declarados en `setup.py`.

    `_rewrite_entry_points` cubre los dos ficheros de configuración; este es el
    mismo agujero en el fichero que los declara ejecutando código. Medido sobre
    un fixture con `entry_points={'console_scripts': [...]}`: `pip install -e .`
    sale bien —el `setup.py` se instala igual de roto— y el script que escribe
    pip muere con `ModuleNotFoundError` al importar el módulo de antes. Ningún
    test del repo lo ve, porque una suite no ejecuta sus entry points.
    """
    path = root / SETUP_SCRIPT
    if not path.exists():
        return 0
    source = read_source(path)
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return 0
    transformed = module.visit(_RewriteEntryPointStrings(moves)).code
    if transformed == source:
        return 0
    path.write_text(transformed, encoding="utf-8")
    return 1


# La otra cosa que el empaquetado nombra con puntos: la lista estática de
# paquetes. `packages` es la clave en los tres ficheros —`[tool.setuptools]` en
# pyproject, `[options]` en setup.cfg y el argumento de `setup()`—, y las tres
# se leen igual de mal después de aplanar.
PACKAGE_LIST_KEY = "packages"
_SECTION_HEADER = re.compile(r"\s*\[\[?([^\]]*)\]")
_PACKAGE_LIST_LINE = re.compile(rf"\s*{PACKAGE_LIST_KEY}\s*=")


def _surviving_packages(base: Path, names: list[str]) -> list[str]:
    """De los nombres declarados, los que todavía son un directorio del árbol.

    Es exactamente lo que setuptools comprueba —`build_py.check_package` falla
    con `package directory 'pkg/plugins' does not exist`—, y por eso se pregunta
    por el directorio y no por el `__init__.py`: un directorio que sobrevive
    porque guarda ficheros de datos ya no es un paquete importable, pero
    declararlo no rompe la instalación y quitarlo cambiaría lo que se empaqueta.

    Lo que no se parece a un paquete nombrado con puntos se deja como está: no
    es algo que B2 haya movido, así que no es asunto suyo decidir si sobra. Ahí
    entra el `packages = find:` de setup.cfg, que no nombra nada porque lo
    resuelve setuptools al construir, y sigue resolviendo bien tras aplanar.
    """
    return [
        name
        for name in names
        if not all(part.isidentifier() for part in name.split("."))
        or (base / Path(*name.split("."))).is_dir()
    ]


def _prune_toml_package_list(root: Path, base: Path) -> int:
    """`[tool.setuptools] packages = [...]` en pyproject.toml."""
    path = root / "pyproject.toml"
    if not path.exists():
        return 0
    source = read_source(path)
    try:
        data = tomllib.loads(source)
    except tomllib.TOMLDecodeError:
        return 0
    setuptools = (data.get("tool") or {}).get("setuptools") or {}
    declared = setuptools.get(PACKAGE_LIST_KEY)
    # Una tabla en vez de una lista es la forma `find`, que se resuelve al
    # construir y sigue resolviendo bien con el árbol aplanado.
    if not isinstance(declared, list) or not all(isinstance(name, str) for name in declared):
        return 0
    kept = _surviving_packages(base, declared)
    if kept == declared:
        return 0
    lines = source.splitlines(keepends=True)
    section = ""
    for index, line in enumerate(lines):
        header = _SECTION_HEADER.match(line)
        if header is not None:
            section = header.group(1).strip()
            continue
        if section != "tool.setuptools" or not _PACKAGE_LIST_LINE.match(line):
            continue
        end = index
        while end < len(lines) - 1 and "]" not in lines[end]:
            end += 1
        indent = line[: len(line) - len(line.lstrip())]
        rendered = ", ".join(f'"{name}"' for name in kept)
        lines[index : end + 1] = [f"{indent}{PACKAGE_LIST_KEY} = [{rendered}]\n"]
        path.write_text("".join(lines), encoding="utf-8")
        return 1
    return 0


def _prune_cfg_package_list(root: Path, base: Path) -> int:
    """`[options] packages = ...` en setup.cfg, en sus dos escrituras."""
    path = root / "setup.cfg"
    if not path.exists():
        return 0
    source = read_source(path)
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(source)
    except configparser.Error:
        return 0
    declared = parser.get("options", PACKAGE_LIST_KEY, fallback=None)
    if declared is None:
        return 0
    names = [name.strip() for name in re.split(r"[,\n]", declared) if name.strip()]
    kept = _surviving_packages(base, names)
    if kept == names:
        return 0
    one_per_line = declared.startswith("\n")
    lines = source.splitlines(keepends=True)
    section = ""
    for index, line in enumerate(lines):
        header = _SECTION_HEADER.match(line)
        if header is not None:
            section = header.group(1).strip()
            continue
        if section != "options" or not _PACKAGE_LIST_LINE.match(line):
            continue
        # El valor multilínea sigue en las líneas indentadas de debajo.
        end = index + 1
        while end < len(lines) and lines[end][:1].isspace() and lines[end].strip():
            end += 1
        if one_per_line:
            lines[index:end] = [f"{PACKAGE_LIST_KEY} =\n", *(f"    {name}\n" for name in kept)]
        else:
            lines[index:end] = [f"{PACKAGE_LIST_KEY} = " + ", ".join(kept) + "\n"]
        path.write_text("".join(lines), encoding="utf-8")
        return 1
    return 0


class _PrunePackageList(cst.CSTTransformer):
    """`packages=[...]` como argumento de `setup()`.

    Aquí la lista no se queda obsoleta, que sería lo esperable: la reescritura
    de cadenas la sigue, así que `pkg.plugins` sale como `pkg.m0` y declara como
    paquete lo que ahora es un módulo. Roto igual —`package directory 'pkg/m0'
    does not exist`— y por el mismo sitio, así que se poda con el mismo
    criterio.
    """

    def __init__(self, base: Path) -> None:
        self.base = base

    def leave_Arg(self, original: cst.Arg, updated: cst.Arg) -> cst.Arg:
        if original.keyword is None or original.keyword.value != PACKAGE_LIST_KEY:
            return updated
        if not isinstance(updated.value, cst.List):
            return updated
        elements = [
            element
            for element in updated.value.elements
            if not isinstance(element.value, cst.SimpleString)
            or not isinstance(name := element.value.evaluated_value, str)
            or _surviving_packages(self.base, [name])
        ]
        if len(elements) == len(updated.value.elements):
            return updated
        if elements:
            # La coma del último elemento es la que lleva pegado el formato del
            # cierre: sin heredarla, podar el último deja el corchete colgando
            # con la sangría del elemento que ya no está.
            elements[-1] = elements[-1].with_changes(comma=updated.value.elements[-1].comma)
        return updated.with_changes(value=updated.value.with_changes(elements=elements))


def _prune_setup_script_package_list(root: Path, base: Path) -> int:
    path = root / SETUP_SCRIPT
    if not path.exists():
        return 0
    source = read_source(path)
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return 0
    transformed = module.visit(_PrunePackageList(base)).code
    if transformed == source:
        return 0
    path.write_text(transformed, encoding="utf-8")
    return 1


def _prune_declared_packages(root: Path, base: Path) -> int:
    """Los paquetes que el empaquetado declara y que ya no existen.

    Un import roto se ve y una ruta rota en la configuración de la suite no
    —eso es `_rewrite_configured_paths`—; esto es un tercer escalón: el árbol
    transformado ni siquiera se instala, y con eso se cae también el arreglo de
    los entry points, que es el que necesita que la instalación llegue a
    ocurrir. Medido con pip sobre el fixture: `error: package directory 'pkg/es'
    does not exist`, `metadata-generation-failed`.

    Va al final de `apply` a propósito: la pregunta es si el directorio existe
    todavía, y eso solo se sabe cuando los ficheros ya se movieron y los
    directorios que quedaron vacíos ya se borraron.
    """
    return (
        _prune_toml_package_list(root, base)
        + _prune_cfg_package_list(root, base)
        + _prune_setup_script_package_list(root, base)
    )


def _drop_stale_bytecode(package: Path) -> None:
    """El bytecode compilado del árbol de antes, que es la dosis de B2 al revés.

    Un `__pycache__` guarda un fichero por módulo, nombrado como el módulo. Tras
    aplanar, esos nombres son exactamente los que B2 acaba de destruir, y siguen
    ahí en dos sitios que ningún borrado de vacíos alcanza: dentro de los
    directorios originales —que precisamente por eso no quedan vacíos y
    sobreviven enteros con su jerarquía— y dentro del paquete raíz, que sobrevive
    por diseño (§5.6). Medido sobre el clon de pint compilado: 12 directorios sin
    un solo .py dentro se salvan del borrado y `pint/__pycache__` conserva
    `pint_convert`, `registry_helpers`, `babel_names`... Un `ls -R` reconstruye
    el árbol que la condición dice haber quitado, o sea una celda con dosis cero
    que se mide en verde: no mide la transformación, no mide nada.

    Borrarlo es además lo correcto de por sí: un `.pyc` en `__pycache__` solo lo
    usa Python si su `.py` sigue al lado, y el de un módulo movido ya no lo está.

    Va aquí y no solo en `copy_tree` a propósito. El filtro de la copia protege
    la única entrada que hay **hoy**; `apply` recibe un árbol y no sabe quién lo
    preparó ni qué se importó dentro desde entonces —`run_suite_in_venv` corre
    pytest con cwd en el repo—, y el modo de fallo del que se protege es el peor
    del experimento: la celda no revienta, se lee como éxito con la dosis a cero.
    Dos guardarraíles para eso no es redundancia.
    """
    for cache in sorted(package.rglob("__pycache__"), reverse=True):
        if cache.is_dir():
            shutil.rmtree(cache)
    # El formato antiguo, junto al fuente: no lo hay en un repo moderno, pero
    # nombra al módulo igual de bien.
    for compiled in package.rglob("*.py[co]"):
        compiled.unlink()


def apply(root: Path) -> TransformResult:
    moves = plan_moves(root)
    if not moves:
        return TransformResult()

    package = _package_root(root)
    assert package is not None  # `plan_moves` ya devolvió vacío si no lo había
    # Desde dónde se cuentan los nombres de módulo, que es también donde hay que
    # ir a buscar y dejar los ficheros: la raíz del árbol, o `src/` si el
    # paquete cuelga de ahí.
    base = package.parent
    changed = 0
    # Los ficheros de doctest no son .py y no los recoge `iter_transformable_files`,
    # pero la suite del repo los ejecuta: en python-stdnum son 234 líneas de
    # ejemplo importando por ruta de módulo, o sea 234 fallos si se quedan atrás.
    changed += _rewrite_configured_paths(root, moves)
    changed += _rewrite_entry_points(root, moves)
    changed += _rewrite_setup_script(root, moves)

    stationary = stationary_modules(root, moves)
    rewriter = _RewriteImports(moves, package.name, "", stationary)
    for path in doctest_files(root):
        source = read_source(path)
        # El fichero entero es texto de doctest: no hay módulo que parsear.
        transformed = rewrite_examples(source, rewriter.rewrite_snippet)
        if transformed != source:
            path.write_text(transformed, encoding="utf-8")
            changed += 1

    # Alcance repo-wide, tests del repo incluidos (§4.3.1): un import sin
    # reescribir en la suite se lee como suite en rojo, o sea como fracaso.
    for path in iter_transformable_files(root):
        if _rewrite_file(path, root, moves, package.name, stationary):
            changed += 1

    for original, target in moves.items():
        source_path = _module_path(base, original)
        if not source_path.exists():
            # Un paquete es su `__init__.py`, no un fichero con su nombre.
            source_path = base / Path(*original.split(".")) / "__init__.py"
        destination = _module_path(base, target)
        if source_path != destination and source_path.exists():
            shutil.move(str(source_path), str(destination))
            changed += 1

    package = _package_root(root)
    if package is not None:
        _drop_stale_bytecode(package)
        # Solo los que quedan vacíos: un directorio con ficheros de datos dentro
        # sigue haciendo falta, porque quien los abre lo hace por ruta.
        for directory in sorted(package.rglob("*"), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()

    changed += _prune_declared_packages(root, base)

    return TransformResult(files_changed=changed, moves=moves)
