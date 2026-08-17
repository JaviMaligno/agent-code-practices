from acp.metrics.runtime_typing import measure


def write(tmp_path, source: str):
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")


def test_flags_pydantic(tmp_path):
    write(tmp_path, "from pydantic import BaseModel\n\n\nclass M(BaseModel):\n    x: int\n")
    result = measure(tmp_path)
    assert result.uses_runtime_typing is True
    assert any("pydantic" in item for item in result.evidence)


def test_flags_get_type_hints(tmp_path):
    write(tmp_path, "import typing\n\n\ndef f(o):\n    return typing.get_type_hints(o)\n")
    result = measure(tmp_path)
    assert result.uses_runtime_typing is True


def test_a_file_with_a_bom_is_still_screened(tmp_path):
    """Aquí saltarse un fichero no encoge una métrica: deja pasar un repo que
    debía quedar excluido, y A1 dejaría de ser semánticamente equivalente."""
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "a.py").write_bytes(
        "from pydantic import BaseModel\n".encode("utf-8-sig")
    )

    result = measure(tmp_path)

    assert result.uses_runtime_typing is True


def test_flags_singledispatch(tmp_path):
    """Es stdlib, así que no lo delata ningún import de tercero: `register`
    elige la implementación leyendo la anotación del primer argumento."""
    write(
        tmp_path,
        "from functools import singledispatch\n"
        "\n"
        "\n"
        "@singledispatch\n"
        "def render(value):\n"
        "    return str(value)\n"
        "\n"
        "\n"
        "@render.register\n"
        "def _(value: int):\n"
        "    return f'{value:d}'\n",
    )

    result = measure(tmp_path)

    assert result.uses_runtime_typing is True
    assert any("singledispatch" in item for item in result.evidence)


def test_flags_singledispatchmethod_qualified(tmp_path):
    write(
        tmp_path,
        "import functools\n"
        "\n"
        "\n"
        "class R:\n"
        "    @functools.singledispatchmethod\n"
        "    def render(self, value):\n"
        "        return str(value)\n",
    )

    result = measure(tmp_path)

    assert result.uses_runtime_typing is True


def test_evidence_locates_the_file_by_its_path(tmp_path):
    """Con 360 módulos hay varios `models.py`: el nombre suelto no permite ir a
    comprobar la exclusión, que es para lo único que sirve la evidencia."""
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "sub" / "models.py").write_text(
        "from pydantic import BaseModel\n", encoding="utf-8"
    )

    result = measure(tmp_path)

    assert any("pkg/sub/models.py" in item for item in result.evidence)


def test_counts_how_many_files_are_affected(tmp_path):
    """Un booleano de repo iguala un uso marginal con uno estructural, y la
    decisión de excluir el candidato depende justo de esa diferencia."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("from pydantic import BaseModel\n", encoding="utf-8")
    (pkg / "b.py").write_text(
        "import pydantic\nfrom pydantic import Field\n", encoding="utf-8"
    )
    (pkg / "c.py").write_text("def f(a: int) -> int:\n    return a\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.affected_files == 2
    assert result.total_files == 3


def test_plain_annotations_are_clean(tmp_path):
    write(tmp_path, "def f(a: int) -> int:\n    return a\n")
    result = measure(tmp_path)
    assert result.uses_runtime_typing is False
    assert result.evidence == []


def test_the_suite_is_screened_too_because_the_transforms_rewrite_it(tmp_path):
    """El criterio de exclusión §3.2.4 existe para una sola cosa: no dejar entrar
    un repo donde A1 no sea semánticamente equivalente. Y A1 reescribe la suite
    del repo (§4.3.1, `iter_transformable_files`), así que un `@singledispatch`
    que solo vive en `tests/` rompe el árbol transformado exactamente igual.
    Cribar solo `iter_source_files` deja pasar al candidato y el fallo aparece
    en el pre-flight §3.6.3, que es el sitio caro de descubrirlo."""
    suite = tmp_path / "tests"
    suite.mkdir()
    (suite / "test_dispatch.py").write_text(
        "from functools import singledispatch\n"
        "\n"
        "\n"
        "@singledispatch\n"
        "def render(value):\n"
        "    return str(value)\n"
        "\n"
        "\n"
        "@render.register\n"
        "def _(value: int):\n"
        "    return f'{value:d}'\n",
        encoding="utf-8",
    )

    result = measure(tmp_path)

    assert result.uses_runtime_typing is True
    assert any("tests/test_dispatch.py" in item for item in result.evidence)


def test_what_no_transform_will_ever_touch_is_not_screened(tmp_path):
    """El criterio no puede ampliarse a todo el disco: `.venv` es la dependencia
    ajena instalada, ninguna transformación la reescribe, y casi cualquier
    entorno trae pydantic dentro. Excluiría a todos los candidatos por algo que
    no es suyo."""
    installed = tmp_path / ".venv" / "lib"
    installed.mkdir(parents=True)
    (installed / "m.py").write_text("from pydantic import BaseModel\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.uses_runtime_typing is False
