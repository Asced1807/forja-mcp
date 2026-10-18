"""Reglas del formato único: agrupación de diagnósticos repetidos."""

from forja_mcp.model import CheckReport, Diagnostic, Severity, ToolRun


def _diag(
    tool: str,
    code: str,
    message: str,
    column: int = 5,
    severity: Severity = "error",
    hint: str | None = None,
    fixable: bool = False,
) -> Diagnostic:
    return Diagnostic("a.py", 10, column, severity, tool, code, message, hint, fixable)


def _report(*diags: Diagnostic) -> CheckReport:
    report = CheckReport(project="/p", adapter="python", tools=[ToolRun("ruff", True), ToolRun("mypy", True)])
    report.diagnostics = list(diags)
    return report.finalize()


def test_same_spot_different_tools_are_merged() -> None:
    report = _report(
        _diag("ruff", "F821", "Undefined name `facura`"),
        _diag("mypy", "name-defined", 'Name "facura" is not defined; did you mean "factura"?'),
    )
    assert report.errors == 1
    [merged] = report.diagnostics
    assert merged.tool == "ruff+mypy"
    assert merged.code == "F821/name-defined"
    assert merged.message == "Undefined name `facura`"
    assert merged.hint == 'mypy: Name "facura" is not defined; did you mean "factura"?'


def test_same_tool_same_spot_stays_separate() -> None:
    report = _report(_diag("mypy", "arg-type", "primer problema"), _diag("mypy", "union-attr", "otro problema"))
    assert report.errors == 2


def test_different_spot_stays_separate() -> None:
    report = _report(_diag("ruff", "F821", "x", column=5), _diag("mypy", "name-defined", "x", column=6))
    assert report.errors == 2


def test_merge_keeps_worst_severity_and_fixable() -> None:
    report = _report(
        _diag("ruff", "F401", "unused", severity="warning", fixable=True, hint="Remove import"),
        _diag("mypy", "misc", "real error"),
    )
    [merged] = report.diagnostics
    assert (merged.severity, merged.fixable) == ("error", True)
    assert merged.hint == "Remove import\nmypy: real error"
    assert (report.errors, report.warnings) == (1, 0)
