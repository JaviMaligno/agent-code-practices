from acp.metrics.domain import cyclomatic_complexity, measure
import ast


def first_function(source: str) -> ast.FunctionDef:
    return ast.parse(source).body[0]


def test_complexity_counts_branches():
    node = first_function(
        "def f(a, b):\n"
        "    if a and b:\n"
        "        for x in a:\n"
        "            pass\n"
        "    return a\n"
    )
    assert cyclomatic_complexity(node) == 4


def test_match_scores_one_point_per_case():
    """Un match es una cadena de ramas: si puntúa 1, el código moderno sale
    subestimado y el umbral deja de ser comparable entre repos."""
    node = first_function(
        "def f(x):\n"
        "    match x:\n"
        "        case 1:\n"
        "            return 'a'\n"
        "        case 2:\n"
        "            return 'b'\n"
        "        case _:\n"
        "            return 'c'\n"
    )
    assert cyclomatic_complexity(node) == 4


def test_comprehension_for_clauses_count_as_branches():
    """Una comprehension anidada es un bucle dentro de otro: contar solo sus
    `ifs` le da la misma puntuación que a una expresión sin ramas."""
    node = first_function("def f(rows):\n    return [x for row in rows for x in row]\n")
    assert cyclomatic_complexity(node) == 3


def test_nested_function_branches_belong_only_to_the_nested_function():
    """`measure` ya cuenta la anidada por su cuenta: sumar sus ramas también a la
    exterior sobreestima justo los repos que usan closures."""
    node = first_function(
        "def outer(rows):\n"
        "    def inner(x):\n"
        "        if x:\n"
        "            return 1\n"
        "        return 0\n"
        "    return inner(rows)\n"
    )
    assert cyclomatic_complexity(node) == 1


def test_flat_function_has_complexity_one():
    node = first_function("def f(a):\n    return a + 1\n")
    assert cyclomatic_complexity(node) == 1


def test_domain_candidates_need_branches_and_internal_calls(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "rules.py").write_text("def rate(x):\n    return x\n", encoding="utf-8")
    (pkg / "billing.py").write_text(
        "from pkg import rules\n"
        "import json\n"
        "\n"
        "\n"
        "def total(items, region, premium):\n"
        "    if region == 'eu' and premium:\n"
        "        base = rules.rate(items)\n"
        "    else:\n"
        "        base = rules.rate(items) * 2\n"
        "    return base\n"
        "\n"
        "\n"
        "def dump(data):\n"
        "    return json.dumps(data)\n",
        encoding="utf-8",
    )

    result = measure(tmp_path)

    assert result.domain_candidate_functions == 1
    assert "pkg.billing.total" in result.samples
    assert "pkg.billing.dump" not in result.samples


def test_src_layout_does_not_collapse_domain_density(tmp_path):
    """dateutil y py-moneyed usan src/<paquete>/: con parts[0] == 'src' ningún
    import interno casa y la densidad sale 0,000 por el layout, no por el código."""
    pkg = tmp_path / "src" / "mylib"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "rules.py").write_text("def rate(x):\n    return x\n", encoding="utf-8")
    (pkg / "billing.py").write_text(
        "from mylib import rules\n"
        "\n"
        "\n"
        "def total(items, region, premium):\n"
        "    if region == 'eu' and premium:\n"
        "        return rules.rate(items)\n"
        "    return rules.rate(items) * 2\n",
        encoding="utf-8",
    )

    result = measure(tmp_path)

    assert result.domain_candidate_functions == 1
    assert result.domain_density > 0


def test_calls_to_functions_defined_in_the_same_file_count_as_internal(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "billing.py").write_text(
        "def _rate(x):\n"
        "    return x * 2\n"
        "\n"
        "\n"
        "def total(rows, premium):\n"
        "    out = 0\n"
        "    for row in rows:\n"
        "        if row > 0 and premium:\n"
        "            out += _rate(row)\n"
        "    return out\n",
        encoding="utf-8",
    )

    result = measure(tmp_path)

    assert "pkg.billing.total" in result.samples


def test_method_calls_on_self_count_as_internal(tmp_path):
    """holidays concentra su dominio en self._add_holiday(...): sin esto sale 0."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "calendar_.py").write_text(
        "class Calendar:\n"
        "    def _helper(self, x):\n"
        "        return x\n"
        "\n"
        "    def populate(self, rows, premium):\n"
        "        out = 0\n"
        "        for row in rows:\n"
        "            if row and premium:\n"
        "                out += self._helper(row)\n"
        "        return out\n",
        encoding="utf-8",
    )

    result = measure(tmp_path)

    assert "pkg.calendar_.populate" in result.samples
