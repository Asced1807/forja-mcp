"""Lector de JUnit XML, el formato de tests que generan casi todos los lenguajes.

pytest, cargo-nextest, jest, go-junit-report, gradle... todos pueden
escribirlo, así que este módulo se reutiliza en cada adaptador.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from forja_mcp.model import TestFailure, TestReport

# Referencias "archivo:línea:" de la traza. La última del propio proyecto
# (ruta relativa) es donde falló de verdad; las absolutas suelen ser librerías.
_LOCATION = re.compile(r"^(?P<file>[^\s:]+):(?P<line>\d+):", re.MULTILINE)
MAX_TRACEBACK_LINES = 40
MAX_LINE_CHARS = 200


def parse(xml_path: Path, report: TestReport) -> None:
    root = ET.parse(xml_path).getroot()
    suites = [root] if root.tag == "testsuite" else root.iter("testsuite")

    for suite in suites:
        report.duration_s += float(suite.get("time") or 0)
        for case in suite.iter("testcase"):
            report.total += 1
            problem = None
            for kind in ("failure", "error"):
                problem = case.find(kind)
                if problem is not None:
                    break

            if problem is None:
                if case.find("skipped") is not None:
                    report.skipped += 1
                else:
                    report.passed += 1
                continue

            if problem.tag == "failure":
                report.failed += 1
                report.failures.append(_failure(case, problem, "failure"))
            else:
                report.errors += 1
                report.failures.append(_failure(case, problem, "error"))

    report.duration_s = round(report.duration_s, 3)


def _failure(
    case: ET.Element, problem: ET.Element, kind: Literal["failure", "error"]
) -> TestFailure:
    name = case.get("name", "?")
    classname = case.get("classname")
    text = (problem.text or "").strip()
    # fuera las líneas de "^^^^" que subrayan el código: la línea ya está justo encima
    lines = [_shorten(raw) for raw in text.splitlines() if raw.strip(" ^~")]
    if len(lines) > MAX_TRACEBACK_LINES:
        lines = ["..."] + lines[-MAX_TRACEBACK_LINES:]
    traceback = "\n".join(lines)

    # pytest repite el mensaje dentro de la traza: enviarlo dos veces es contexto tirado
    message = (problem.get("message") or "").strip()
    first = message.splitlines()[0] if message else ""
    if first and _shorten(first)[:80] in traceback:
        message = ""

    file, line = case.get("file"), None
    matches = list(_LOCATION.finditer(text))
    own = [m for m in matches if not m["file"].startswith("/")] or matches
    if own:
        file, line = own[-1]["file"], int(own[-1]["line"])

    return TestFailure(
        test=f"{classname}::{name}" if classname else name,
        kind=kind,
        message=_shorten(message, 500),
        file=file,
        line=line,
        traceback=traceback,
    )


def _shorten(text: str, limit: int = MAX_LINE_CHARS) -> str:
    """Recorta líneas enormes, como el repr de un objeto con decenas de campos."""
    return text if len(text) <= limit else text[: limit - 5] + " […]"
