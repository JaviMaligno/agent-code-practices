from acp.metrics.coupling import measure


def test_counts_internal_edges_only(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("import os\n", encoding="utf-8")
    (pkg / "api.py").write_text("from pkg import core\nimport json\n", encoding="utf-8")
    (pkg / "cli.py").write_text("from pkg import core\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.internal_modules == 4
    assert result.internal_edges == 2
    assert result.max_fan_in == 2
    assert result.mean_fan_out == 0.5


def test_relative_imports_count_as_internal_edges(tmp_path):
    """El estilo relativo es el dominante en paquetes de librería: pint tiene
    319 imports relativos frente a 133 absolutos. Si no se resuelven, el
    acoplamiento medido es el estilo de import, no el acoplamiento."""
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("import os\n", encoding="utf-8")
    (pkg / "api.py").write_text("from .core import thing\n", encoding="utf-8")
    (pkg / "cli.py").write_text("from . import core\n", encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "deep.py").write_text("from ..core import thing\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.internal_edges == 3
    assert result.max_fan_in == 3
    assert result.mean_fan_out == 0.5  # 3 aristas sobre 6 módulos


def test_relative_and_absolute_styles_measure_the_same_coupling(tmp_path):
    def build(root, api_import):
        pkg = root / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "core.py").write_text("import os\n", encoding="utf-8")
        (pkg / "api.py").write_text(api_import, encoding="utf-8")
        return measure(root)

    relative = build(tmp_path / "rel", "from .core import thing\n")
    absolute = build(tmp_path / "abs", "from pkg.core import thing\n")

    assert relative == absolute


def test_package_init_resolves_its_own_relative_imports(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .core import thing\n", encoding="utf-8")
    (pkg / "core.py").write_text("import os\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.internal_edges == 1
    assert result.max_fan_in == 1


def test_a_file_with_a_bom_still_contributes_its_imports(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "core.py").write_text("import os\n", encoding="utf-8")
    (pkg / "api.py").write_bytes("from pkg import core\n".encode("utf-8-sig"))

    result = measure(tmp_path)

    assert result.internal_edges == 1


def test_a_package_at_the_root_of_the_clone_still_resolves_its_imports(tmp_path):
    """Cuando el propio clon es el paquete, el nombre de módulo del `__init__`
    queda vacío y sus imports relativos se descartaban por no tener ancla: el
    repo se leía como menos acoplado de lo que está."""
    (tmp_path / "__init__.py").write_text(
        "from . import core\nfrom .api import thing\n", encoding="utf-8"
    )
    (tmp_path / "core.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "api.py").write_text("from .core import other\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.internal_edges == 3  # __init__ -> core, __init__ -> api, api -> core
    assert result.max_fan_in == 2


def test_repo_without_internal_imports_has_no_edges(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("import os\n", encoding="utf-8")
    (pkg / "b.py").write_text("import sys\n", encoding="utf-8")

    result = measure(tmp_path)

    assert result.internal_edges == 0
    assert result.max_fan_in == 0
