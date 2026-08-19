"""Qué necesita una definición para vivir en otro fichero.

B1 reparte definiciones entre ficheros y B5 concatena módulos: las dos mueven
definiciones, no ficheros, y una definición no es autónoma. Usa imports,
constantes del módulo y otras definiciones, así que moverla sin llevarse eso
—o sin importarlo en el destino— da `NameError` en el primer uso. Y un repo
roto se lee igual que un agente que fracasa: la celda mediría el daño de la
transformación, no la práctica que la transformación quiere quitar (§11).

Aquí no se decide nada, solo se mira: `free_names` dice qué nombres necesita un
nodo de fuera de sí mismo y `module_bindings` dice qué nombres pone su módulo y
de dónde salen. Cruzar las dos cosas —y decidir qué se importa, qué viaja y qué
no se mueve— es de quien mueve.
"""

from __future__ import annotations

import ast

# Nodos que abren un ámbito propio de comprehension: el destino del `for` vive
# dentro y no se ve desde fuera (en Python 3, a diferencia de Python 2).
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def free_names(node: ast.AST) -> set[str]:
    """Nombres que `node` necesita de un ámbito que no es suyo.

    Es lo que habría que importar o llevarse para que la definición siga
    funcionando en otro fichero. Dos decisiones deliberadas:

    - **Los builtins no se filtran.** Quien mueve sabe que `len` no hay que
      importarlo; quien escribe el informe quiere verlos. Filtrar aquí es
      perder información que no se puede recuperar; filtrar fuera es una línea.
    - **El propio nombre de la definición cuenta como libre.** `def total(...)`
      liga `total` en el ámbito de FUERA, no dentro, así que una función
      recursiva pide `total` a su módulo. Quien mueve la definición ya sabe que
      ese nombre lo aporta ella misma y lo resta; al revés —callarlo— no habría
      forma de distinguirlo de un nombre que sí falta.
    """
    encontrados: set[str] = set()
    _visit(node, [], encontrados)  # cadena vacía: todo lo de fuera es libre
    return encontrados


def module_bindings(tree: ast.Module) -> dict[str, str]:
    """Nombre → qué lo define en el módulo: `"import"`, `"assign"` o `"def"`.

    Sirve para responder a la pregunta que se hace quien mueve una definición:
    este nombre libre, ¿lo pone el módulo del que la saco? Si es un import, al
    destino se le copia el import; si es una asignación o una definición, hay
    que importarlo del sitio donde acabe.
    """
    bindings: dict[str, str] = {}
    for statement in tree.body:
        _classify(statement, bindings)
    return bindings


# --- ámbitos ---------------------------------------------------------------
#
# La cadena de ámbitos es una lista de (clase de ámbito, nombres ligados), del
# más externo al más interno. Un nombre es libre cuando no está en ninguno de
# los ámbitos que se ven desde donde se lee.

_FUNCTION = "function"
_CLASS = "class"

Chain = list[tuple[str, set[str]]]


def _resolved(name: str, chain: Chain) -> bool:
    for index in range(len(chain) - 1, -1, -1):
        kind, ligados = chain[index]
        # El ámbito de una clase solo lo ve el código que está DIRECTAMENTE en
        # su cuerpo. Un método salta por encima y busca en el módulo —por eso
        # dentro de un método se escribe `self.TAX` y no `TAX`—, así que tratar
        # los atributos de clase como visibles haría que B1 moviera la clase sin
        # llevarse el `TAX` del módulo y el método reventara al usarse.
        if kind == _CLASS and index != len(chain) - 1:
            continue
        if name in ligados:
            return True
    return False


def _visit(node: ast.AST, chain: Chain, free: set[str]) -> None:
    if isinstance(node, ast.Name):
        # Solo la lectura pide algo de fuera: escribir liga, y lo ligado ya se
        # recogió al abrir el ámbito.
        if isinstance(node.ctx, ast.Load) and not _resolved(node.id, chain):
            free.add(node.id)
        return

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _visit_function(node, chain, free)
        return

    if isinstance(node, ast.ClassDef):
        _visit_class(node, chain, free)
        return

    if isinstance(node, ast.Lambda):
        _visit_lambda(node, chain, free)
        return

    if isinstance(node, _COMPREHENSIONS):
        _visit_comprehension(node, chain, free)
        return

    for child in ast.iter_child_nodes(node):
        _visit(child, chain, free)


def _visit_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef, chain: Chain, free: set[str]
) -> None:
    for decorator in node.decorator_list:
        # El decorador se aplica DESPUÉS de crear la función, o sea fuera hasta
        # de sus parámetros de tipo.
        _visit(decorator, chain, free)
    chain = _with_type_params(node, chain)

    # Los valores por defecto y las anotaciones se evalúan en el ámbito de
    # FUERA de la función: si se filtraran con sus locales, un `def f(x=TAX)`
    # dejaría de pedir `TAX` en cuanto la función tuviera una local llamada así.
    _visit_signature_outside(node, chain, free)

    ligados, globales = _scope_bindings(node.body)
    ligados.update(_parameters(node.args))
    # Un `global X` no liga nada aquí dentro: dice que X vive en el módulo, y
    # además que esta definición lo LEE o lo ESCRIBE. Es la dependencia más
    # fuerte que puede tener —arrastra estado, no solo un nombre— y B1 la
    # necesita ver para sacar la definición del reparto en vez de romperla.
    free.update(globales)

    interior = chain + [(_FUNCTION, ligados)]
    for statement in node.body:
        _visit(statement, interior, free)


def _visit_class(node: ast.ClassDef, chain: Chain, free: set[str]) -> None:
    # Decoradores, bases y `metaclass=` se evalúan fuera de la clase.
    for decorator in node.decorator_list:
        _visit(decorator, chain, free)
    chain = _with_type_params(node, chain)
    for base in node.bases:
        _visit(base, chain, free)
    for keyword in node.keywords:
        _visit(keyword.value, chain, free)

    ligados, globales = _scope_bindings(node.body)
    free.update(globales)

    interior = chain + [(_CLASS, ligados)]
    for statement in node.body:
        _visit(statement, interior, free)


def _with_type_params(node: ast.AST, chain: Chain) -> Chain:
    """Los parámetros de tipo de PEP 695 (`def f[T](...)`) los liga la propia
    definición, en un ámbito que envuelve a la firma y al cuerpo.

    Sin esto, `T` sale como nombre libre y quien mueva la definición escribiría
    un import de un nombre que no existe en ningún módulo: eso no es dosis
    corta, es un ImportError al cargar —el repo entero se lee como un agente
    que fracasa—.
    """
    # `type_params` no existe antes de Python 3.12 y el proyecto admite 3.11.
    parametros = getattr(node, "type_params", None)
    if not parametros:
        return chain
    return chain + [(_FUNCTION, {parametro.name for parametro in parametros})]


def _visit_lambda(node: ast.Lambda, chain: Chain, free: set[str]) -> None:
    for default in [*node.args.defaults, *node.args.kw_defaults]:
        if default is not None:
            _visit(default, chain, free)
    _visit(node.body, chain + [(_FUNCTION, set(_parameters(node.args)))], free)


def _visit_comprehension(node: ast.AST, chain: Chain, free: set[str]) -> None:
    generators = node.generators  # type: ignore[attr-defined]
    ligados: set[str] = set()
    # Una comprehension abre ámbito de FUNCIÓN, no de clase: por eso dentro de
    # un cuerpo de clase no ve los atributos que la rodean.
    interior: Chain = chain + [(_FUNCTION, ligados)]
    for index, generator in enumerate(generators):
        # El primer iterable se evalúa en el ámbito de fuera; los siguientes ya
        # ven los destinos de los `for` anteriores.
        _visit(generator.iter, chain if index == 0 else interior, free)
        ligados.update(_target_names(generator.target))
        for condition in generator.ifs:
            _visit(condition, interior, free)
    for campo in ("elt", "key", "value"):
        parte = getattr(node, campo, None)
        if parte is not None:
            _visit(parte, interior, free)


def _visit_signature_outside(
    node: ast.FunctionDef | ast.AsyncFunctionDef, chain: Chain, free: set[str]
) -> None:
    for decorator in node.decorator_list:
        _visit(decorator, chain, free)
    for default in [*node.args.defaults, *node.args.kw_defaults]:
        if default is not None:
            _visit(default, chain, free)
    for argumento in _all_args(node.args):
        if argumento.annotation is not None:
            _visit(argumento.annotation, chain, free)
    if node.returns is not None:
        _visit(node.returns, chain, free)


def _all_args(args: ast.arguments) -> list[ast.arg]:
    todos = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    for extra in (args.vararg, args.kwarg):
        if extra is not None:
            todos.append(extra)
    return todos


def _parameters(args: ast.arguments) -> set[str]:
    return {argumento.arg for argumento in _all_args(args)}


def _scope_bindings(body: list[ast.stmt]) -> tuple[set[str], set[str]]:
    """Nombres que liga un bloque en SU ámbito, y los que declara `global`.

    En Python el ámbito no es secuencial: una función que asigna `x` en la
    última línea tiene `x` local desde la primera. Por eso los ligados se
    recogen de una pasada antes de mirar las lecturas.
    """
    ligados: set[str] = set()
    declarados: set[str] = set()
    globales: set[str] = set()
    for statement in body:
        _collect_bindings(statement, ligados, declarados, globales)
    # `global` y `nonlocal` dicen que el nombre NO es de este ámbito, por mucho
    # que se le asigne aquí; la diferencia entre los dos es a quién se lo pide.
    return ligados - declarados, globales


def _collect_bindings(
    node: ast.AST, ligados: set[str], declarados: set[str], globales: set[str]
) -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        # El nombre sí liga; el cuerpo es otro ámbito y no se entra.
        ligados.add(node.name)
        return

    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            ligados.add((alias.asname or alias.name).split(".")[0])
        return

    if isinstance(node, ast.Global):
        declarados.update(node.names)
        globales.update(node.names)
        return

    if isinstance(node, ast.Nonlocal):
        # El nombre está en la función de fuera, así que viaja con ella: no se
        # le pide nada al módulo.
        declarados.update(node.names)
        return

    if isinstance(node, ast.Assign):
        for target in node.targets:
            ligados.update(_target_names(target))
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        ligados.update(_target_names(node.target))
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        ligados.update(_target_names(node.target))
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            if item.optional_vars is not None:
                ligados.update(_target_names(item.optional_vars))
    elif isinstance(node, ast.ExceptHandler):
        if node.name is not None:
            ligados.add(node.name)
    elif isinstance(node, ast.NamedExpr):
        ligados.update(_target_names(node.target))
    elif isinstance(node, (ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
        # Un patrón captura en una local: `case [primero, *resto]` liga los dos
        # nombres. El patrón de clase de `case Punto(x=x)` es otra cosa —ahí
        # `Punto` sí se busca fuera— y por eso solo se ligan estos tres nodos.
        nombre = node.name if not isinstance(node, ast.MatchMapping) else node.rest
        if nombre is not None:
            ligados.add(nombre)

    for child in ast.iter_child_nodes(node):
        _collect_bindings(child, ligados, declarados, globales)


def _target_names(target: ast.AST) -> set[str]:
    """Nombres que liga un destino de asignación.

    Un destino puede ser una tupla, una estrella o un `d[k]`: de los dos
    primeros salen nombres, del último no —ahí `d` y `k` se LEEN—.
    """
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        nombres: set[str] = set()
        for elemento in target.elts:
            nombres.update(_target_names(elemento))
        return nombres
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return set()


def _classify(statement: ast.stmt, bindings: dict[str, str]) -> None:
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        for alias in statement.names:
            bindings[(alias.asname or alias.name).split(".")[0]] = "import"
    elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        bindings[statement.name] = "def"
    elif isinstance(statement, ast.Assign):
        for target in statement.targets:
            for nombre in _target_names(target):
                bindings[nombre] = "assign"
    elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
        for nombre in _target_names(statement.target):
            bindings[nombre] = "assign"
