"""Pruebas de extremo a extremo: proyectos reales con fallos puestos a propósito."""

import asyncio
import textwrap
from pathlib import Path

import pytest

from forja_mcp import server
from forja_mcp.adapters import detect, find_root
from forja_mcp.adapters.python import PythonAdapter
from forja_mcp.model import TestReport


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0"\n')
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body))
    return tmp_path


def _tests(root: Path, selector: str | None) -> TestReport:
    report = asyncio.run(PythonAdapter().test(root, selector, timeout=60))
    assert report is not None, "este proyecto tiene tests: no debería ser 'no aplica'"
    return report


BROKEN = {
    "calc.py": """\
        import os


        def suma(a: int, b: int) -> int:
            return a - b


        def nombre() -> str:
            return 42
        """,
    "test_calc.py": """\
        from calc import suma


        def test_suma():
            assert suma(2, 3) == 5


        def test_ok():
            assert suma(0, 0) == 0
        """,
}


def test_detect_and_root(tmp_path: Path) -> None:
    root = _project(tmp_path, BROKEN)
    assert find_root(root / "calc.py") == root
    adapter = detect(root)
    assert adapter is not None and adapter.name == "python"


def test_check_finds_type_and_lint_errors(tmp_path: Path) -> None:
    root = _project(tmp_path, BROKEN)
    report = asyncio.run(PythonAdapter().check(root, timeout=60))

    assert not report.ok
    assert all(t.ran for t in report.tools), report.tools

    mypy = [d for d in report.diagnostics if d.tool == "mypy"]
    assert [(d.file, d.line, d.code) for d in mypy] == [("calc.py", 9, "return-value")]

    unused = [d for d in report.diagnostics if d.code == "F401"]
    assert unused and unused[0].file == "calc.py" and unused[0].line == 1
    assert unused[0].fixable


def test_check_merges_ruff_and_mypy_on_same_bug(tmp_path: Path) -> None:
    root = _project(tmp_path, {"main.py": "def total() -> int:\n    factura = 1\n    return facura\n"})
    report = asyncio.run(PythonAdapter().check(root, timeout=60))

    undefined = [d for d in report.diagnostics if d.line == 3]
    assert len(undefined) == 1, undefined
    assert undefined[0].tool == "ruff+mypy"
    assert undefined[0].hint and "did you mean" in undefined[0].hint


def test_check_clean_project_is_ok(tmp_path: Path) -> None:
    root = _project(tmp_path, {"ok.py": "def doble(x: int) -> int:\n    return x * 2\n"})
    report = asyncio.run(PythonAdapter().check(root, timeout=60))
    assert report.ok, report.diagnostics


def test_tests_locate_failure_line(tmp_path: Path) -> None:
    root = _project(tmp_path, BROKEN)
    report = _tests(root, None)

    assert not report.ok
    assert (report.total, report.passed, report.failed) == (2, 1, 1)
    failure = report.failures[0]
    assert failure.test == "test_calc::test_suma"
    assert (failure.file, failure.line) == ("test_calc.py", 5)
    assert "assert -1 == 5" in failure.traceback


def test_import_error_is_reported_not_hidden(tmp_path: Path) -> None:
    root = _project(tmp_path, {"test_roto.py": "import modulo_que_no_existe\n\ndef test_x(): pass\n"})
    report = _tests(root, None)

    assert not report.ok
    assert report.errors == 1
    assert "modulo_que_no_existe" in report.failures[0].traceback


def test_no_tests_is_not_success(tmp_path: Path) -> None:
    root = _project(tmp_path, {"solo.py": "X = 1\n"})
    assert asyncio.run(PythonAdapter().test(root, None, timeout=60)) is None  # no aplica
    data = asyncio.run(server.test(str(root)))  # ...pero el servidor NO lo da por bueno
    assert data["ok"] is False
    assert "no tiene tests" in data["next"] or "no tiene tests" in str(data["tools"])


@pytest.mark.parametrize("selector", ["test_calc.py::test_ok"])
def test_selector_runs_only_that_test(tmp_path: Path, selector: str) -> None:
    root = _project(tmp_path, BROKEN)
    report = _tests(root, selector)
    assert (report.total, report.passed, report.ok) == (1, 1, True)
