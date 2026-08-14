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
