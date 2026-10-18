"""Mejoras de contexto: forma compacta, línea de código, historial y agrupación por causa."""

import asyncio
from pathlib import Path

from conftest import MakeProject

from forja_mcp import junit, server
from forja_mcp.history import History
from forja_mcp.model import CheckReport, Diagnostic, TestFailure, TestReport, ToolRun

BUG = {
    "calc.py": "def suma(a: int, b: int) -> int:\n    return a - b\n",
    "test_calc.py": "from calc import suma\n\ndef test_suma():\n    assert suma(2, 3) == 5\n",
}


def test_clean_check_is_tiny() -> None:
    report = CheckReport("/p", "python", tools=[ToolRun("ruff", True, 0), ToolRun("mypy", True, 0)])
    assert report.finalize().to_dict() == {"ok": True}


def test_meaningful_zeros_survive() -> None:
    report = TestReport("/p", "python", tools=[ToolRun("pytest", True, 5, "pytest no encontró ningún test")])
    data = report.finalize().to_dict()
    assert data["ok"] is False
    assert data["total"] == 0  # "no hay tests" es información, no ruido
    assert data["tools"] == [{"name": "pytest", "ran": True, "note": "pytest no encontró ningún test"}]


def test_tool_that_did_not_run_is_never_hidden() -> None:
    report = CheckReport("/p", "python", tools=[ToolRun("mypy", False, 2, "config rota")])
    data = report.finalize().to_dict()
    assert data == {"ok": False, "tools": [{"name": "mypy", "exit_code": 2, "note": "config rota"}]}


def test_source_line_is_attached(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n    return float(valor)   \n")
    report = CheckReport(str(tmp_path), "python", tools=[ToolRun("mypy", True)])
    report.diagnostics = [Diagnostic("a.py", 2, 5, "error", "mypy", "return-value", "mal")]
    assert report.finalize().diagnostics[0].source == "return float(valor)"


def test_junit_drops_duplicated_message_and_carets(tmp_path: Path) -> None:
    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<testsuite><testcase classname="t" name="test_x">'
        '<failure message="AssertionError: assert 1 == 2">t.py:3: in test_x\n'
        "    assert 1 == 2\n"
        "           ^^^^^^\n"
        "E   AssertionError: assert 1 == 2</failure></testcase></testsuite>"
    )
    report = TestReport("/p", "python")
    junit.parse(xml, report)

    [f] = report.failures
    assert f.message == ""
    assert "^^^" not in f.traceback
    assert (f.file, f.line) == ("t.py", 3)


def test_failures_with_same_cause_are_grouped() -> None:
    def fail(test: str) -> TestFailure:
        return TestFailure(test, "failure", "", "src/x.py", 57, "src/x.py:57: in f\nE   TypeError: boom")

    report = TestReport("/p", "python", total=3, failed=3, tools=[ToolRun("pytest", True, 1)])
    report.failures = [fail("t::a"), fail("t::b"), TestFailure("t::c", "failure", "otro", "src/y.py", 1)]
    report.finalize()
    assert [f.test for f in report.failures] == ["t::a", "t::c"]
    assert report.failures[0].same_cause == ["t::b"]
    assert report.failed == 3  # los contadores siguen contando tests


def test_history_marks_seen_and_reports_changes() -> None:
    history = History()
    a = Diagnostic("x.py", 1, 1, "error", "mypy", "c", "A")
    b = Diagnostic("x.py", 2, 1, "error", "mypy", "c", "B")
    assert history.compare(("s",), [a, b]) is None  # primera vez: nada que comparar

    # se arregló B, apareció C, y A sigue (aunque ahora en otra línea)
    a2 = Diagnostic("x.py", 5, 1, "error", "mypy", "c", "A")
    c = Diagnostic("x.py", 9, 1, "error", "mypy", "c", "C")
    changes = history.compare(("s",), [a2, c])
    assert changes is not None
    assert (changes.fixed, changes.new, changes.seen) == (1, 1, 1)
    assert (a2.seen, c.seen) == (True, False)


def test_second_call_sends_seen_failures_without_traceback(make_project: MakeProject) -> None:
    root = make_project(BUG)
    first = asyncio.run(server.test(str(root)))
    second = asyncio.run(server.test(str(root)))

    assert "traceback" in first["failures"][0]
    assert "traceback" not in second["failures"][0]
    assert second["failures"][0]["seen"] is True
    assert second["changes"] == {"seen": 1}
    assert asyncio.run(server.test(str(root), full=True))["failures"][0]["traceback"]


def test_gate_runs_both(make_project: MakeProject) -> None:
    root = make_project(BUG)
    data = asyncio.run(server.gate(str(root)))
    assert data["ok"] is False
    assert data["check"]["ok"] is True and "errors" not in data["check"]
    assert data["test"]["failed"] == 1
