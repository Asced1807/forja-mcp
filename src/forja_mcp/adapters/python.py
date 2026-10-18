"""Adaptador de Python: ruff (lint), mypy (tipos), pytest (tests) y coverage."""

import asyncio
import fnmatch
import json
import re
import sys
import tempfile
from pathlib import Path

from forja_mcp import junit
from forja_mcp.adapters import python_mutation
from forja_mcp.adapters.base import BaseAdapter
from forja_mcp.files import excluded, project_files
from forja_mcp.model import (
    CheckReport,
    Coverage,
    Diagnostic,
    MutationReport,
    Severity,
    TestReport,
    ToolRun,
)
from forja_mcp.parsers import rel as _rel
from forja_mcp.runner import run

# ruff y mypy vienen instalados con forja: funcionan aunque el proyecto no los tenga.
_BIN = Path(sys.executable).parent
_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
_EXCLUDE = r"(^|/)(\.venv|venv|\.tox|build|dist|node_modules)/"
_CACHE = Path.home() / ".cache" / "forja-mcp" / "mypy"
_RUFF_CACHE = Path.home() / ".cache" / "forja-mcp" / "ruff"  # nunca dentro del proyecto del usuario
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


class PythonAdapter(BaseAdapter):
    name = "python"
    extensions: tuple[str, ...] = (".py", ".pyi")

    def detect(self, root: Path) -> bool:
        return any((root / m).exists() for m in _MARKERS) or bool(self.files(root))

    def tools(self, root: Path) -> dict[str, str]:
        tools = {"check": "ruff + mypy", "fix": "ruff --fix"}
        if self._test_files(root):
            tools["test"] = "pytest"
        return tools

    def _test_files(self, root: Path) -> list[Path]:
        return [
            f for f in project_files(root)
            if f.suffix == ".py" and (f.name.startswith("test_") or f.name.endswith("_test.py"))
        ]

    def python(self, root: Path) -> Path:
        """El intérprete del proyecto, para que mypy y pytest vean sus dependencias."""
        for venv in (".venv", "venv"):
            candidate = root / venv / "bin" / "python"
            if candidate.exists():
                return candidate
        return Path(sys.executable)

    # ── check ────────────────────────────────────────────────────────────

    async def check(self, root: Path, timeout: float) -> CheckReport:
        report = CheckReport(project=str(root), adapter=self.name)
        # en paralelo, pero se combinan siempre en el mismo orden (ruff, mypy)
        # para que la agrupación de duplicados sea determinista
        for tool, diagnostics in await asyncio.gather(
            self._ruff(root, timeout), self._mypy(root, timeout)
        ):
            report.tools.append(tool)
            report.diagnostics.extend(diagnostics)
        return report.finalize()

    async def fix(self, root: Path, timeout: float) -> CheckReport:
        """Aplica las correcciones seguras de ruff y devuelve el check resultante."""
        _, before = await self._ruff(root, timeout)
        fixable = [d for d in before if d.fixable]
        if fixable:
            await run([str(_BIN / "ruff"), "check", "--cache-dir", str(_RUFF_CACHE), "--fix", "--quiet",
                       *_ruff_exclude(root), "."], root, timeout)
        report = await self.check(root, timeout)
        remaining = {(d.file, d.code, d.message) for d in report.diagnostics}
        report.fixed = [
            f"{d.file}:{d.line} {d.code} {d.message}"
            for d in fixable
            if (d.file, d.code, d.message) not in remaining
        ]
        return report

    async def _ruff(self, root: Path, timeout: float) -> tuple[ToolRun, list[Diagnostic]]:
        res = await run(
            [str(_BIN / "ruff"), "check", "--cache-dir", str(_RUFF_CACHE), "--output-format", "json", "--no-fix",
             *_ruff_exclude(root), "."],
            root,
            timeout,
        )
        # ruff: 0 = limpio, 1 = hay hallazgos, 2 = fallo de la propia herramienta.
        if res.exit_code not in (0, 1):
            note = res.note or res.stderr.strip()[-500:]
            return ToolRun("ruff", False, res.exit_code, note), []

        diagnostics = []
        for item in json.loads(res.stdout or "[]"):
            severity: Severity = "warning"
            if item.get("severity") == "error" and item.get("code") in (None, "invalid-syntax"):
                severity = "error"
            elif (item.get("code") or "").startswith(("F82", "E9")):
                # nombre no definido / error de sintaxis: el código no puede funcionar
                severity = "error"
            fix = item.get("fix")
            diagnostics.append(
                Diagnostic(
                    file=_rel(item["filename"], root),
                    line=item["location"]["row"],
                    column=item["location"]["column"],
                    severity=severity,
                    tool="ruff",
                    code=item.get("code"),
                    message=item["message"],
                    hint=fix["message"] if fix else None,
                    fixable=bool(fix and fix.get("applicability") == "safe"),
                )
            )
        return ToolRun("ruff", True, res.exit_code), diagnostics

    async def _mypy(self, root: Path, timeout: float) -> tuple[ToolRun, list[Diagnostic]]:
        _CACHE.mkdir(parents=True, exist_ok=True)
        res = await run(
            [
                str(_BIN / "mypy"),
                "-O",
                "json",
                "--show-column-numbers",
                "--python-executable",
                str(self.python(root)),
                "--cache-dir",
                str(_CACHE),
                "--exclude",
                _mypy_exclude(root),
                ".",
            ],
            root,
            timeout,
        )
        # mypy: 0 = limpio, 1 = errores de tipos, 2 = fallo de la herramienta.
        if res.exit_code not in (0, 1):
            note = res.note or (res.stderr or res.stdout).strip()[-500:]
            return ToolRun("mypy", False, res.exit_code, note), []

        diagnostics: list[Diagnostic] = []
        for raw in res.stdout.splitlines():
            if not raw.startswith("{"):
                continue
            item = json.loads(raw)
            if item["severity"] == "note":
                # las notas amplían el error anterior: se adjuntan como pista
                if diagnostics:
                    prev = diagnostics[-1]
                    prev.hint = f"{prev.hint}\n{item['message']}" if prev.hint else item["message"]
                continue
            diagnostics.append(
                Diagnostic(
                    file=_rel(item["file"], root),
                    line=item["line"],
                    column=item["column"] + 1,  # mypy cuenta columnas desde 0
                    severity="error",
                    tool="mypy",
                    code=item.get("code"),
                    message=item["message"],
                    hint=item.get("hint"),
                )
            )
        return ToolRun("mypy", True, res.exit_code), diagnostics

    # ── test ─────────────────────────────────────────────────────────────

    async def test(
        self, root: Path, selector: str | None, timeout: float, coverage: bool = False
    ) -> TestReport | None:
        if not selector and not self._test_files(root):
            return None  # en este proyecto Python no tiene tests: no aplica
        report = TestReport(project=str(root), adapter=self.name)
        python = self.python(root)

        with tempfile.TemporaryDirectory(prefix="forja-") as tmp:
            xml = Path(tmp) / "junit.xml"
            data_file = Path(tmp) / ".coverage"
            pytest_args = [
                "pytest",
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
                "-o",
                "junit_family=xunit1",
                f"--junitxml={xml}",
                *_pytest_ignore(root),
                *([selector] if selector else []),
            ]
            measure = coverage and await self._has_coverage(python, root, report)
            if measure:
                cmd = [
                    str(python), "-m", "coverage", "run", f"--data-file={data_file}",
                    f"--source={root}", "--omit=*/tests/*,*/test_*.py,*/.venv/*,*/conftest.py",
                    "-m", *pytest_args,
                ]
            else:
                cmd = [str(python), "-m", *pytest_args]
            res = await run(cmd, root, timeout)

            # pytest: 0 = todo bien, 1 = fallos, 2 = interrumpido (p. ej. error al
            # importar), 5 = no hay tests. Cualquier otro caso es fallo de la herramienta.
            if xml.exists() and res.exit_code in (0, 1, 2, 5):
                report.tools.append(ToolRun("pytest", True, res.exit_code))
                junit.parse(xml, report)
                if res.exit_code == 5:
                    report.tools[-1].note = "pytest no encontró ningún test"
            else:
                output = res.note or (res.stdout + res.stderr)
                if "No module named pytest" in output:
                    note = f"pytest no está instalado en {python}"
                else:
                    note = output.strip()[-300:]
                report.tools.append(ToolRun("pytest", False, res.exit_code, note))

            if res.exit_code not in (0, 1):
                report.output_tail = (res.stdout + res.stderr).strip()[-2000:] or None

            if measure and data_file.exists():
                await self._coverage_report(python, root, data_file, Path(tmp), report, timeout)

        return report.finalize()

    async def _has_coverage(self, python: Path, root: Path, report: TestReport) -> bool:
        res = await run([str(python), "-c", "import coverage"], root, 30)
        if res.exit_code == 0:
            return True
        report.tools.append(
            ToolRun(
                "coverage",
                False,
                res.exit_code,
                f"coverage no está instalado en {python}; añádelo con: uv add --dev coverage",
            )
        )
        return False

    async def _coverage_report(
        self, python: Path, root: Path, data_file: Path, tmp: Path, report: TestReport, timeout: float
    ) -> None:
        out = tmp / "coverage.json"
        res = await run(
            [str(python), "-m", "coverage", "json", "-q", f"--data-file={data_file}", "-o", str(out)],
            root,
            timeout,
        )
        if res.exit_code != 0 or not out.exists():
            note = res.note or (res.stdout + res.stderr).strip()[-300:]
            report.tools.append(ToolRun("coverage", False, res.exit_code, note))
            return
        report.tools.append(ToolRun("coverage", True, 0))

        data = json.loads(out.read_text())
        changed = await _changed_lines(root)
        cov = Coverage(
            percent=round(data["totals"]["percent_covered"], 1),
            scope="all" if changed is None else "changed",
        )
        for name, info in data["files"].items():
            path = _rel(name, root)
            missing = set(info["missing_lines"])
            if changed is not None:
                missing &= changed.get(path, set())
            if missing:
                cov.uncovered[path] = _ranges(sorted(missing))
        report.coverage = cov

    # ── mutate ───────────────────────────────────────────────────────────

    async def mutate(
        self, root: Path, file: str | None, max_mutants: int, timeout: float
    ) -> MutationReport:
        return await python_mutation.mutate(self.python(root), root, file, max_mutants, timeout)


def _ruff_exclude(root: Path) -> list[str]:
    patterns = excluded(root)
    return ["--extend-exclude", ",".join(patterns)] if patterns else []


def _mypy_exclude(root: Path) -> str:
    extra = [fnmatch.translate(p)[:-2] if any(c in p for c in "*?[") else re.escape(p) + "/" for p in excluded(root)]
    return "|".join([_EXCLUDE, *(f"(^|/){x}" for x in extra)])


def _pytest_ignore(root: Path) -> list[str]:
    return [f"--ignore-glob={p}" if any(c in p for c in "*?[") else f"--ignore={p}" for p in excluded(root)]


async def _changed_lines(root: Path) -> dict[str, set[int]] | None:
    """Líneas añadidas o cambiadas respecto a HEAD. None si no hay git o commits."""
    head = await run(["git", "rev-parse", "--verify", "-q", "HEAD"], root, 30)
    if head.exit_code != 0:
        return None
    diff = await run(["git", "diff", "-U0", "--no-color", "--relative", "HEAD"], root, 60)
    untracked = await run(["git", "ls-files", "--others", "--exclude-standard"], root, 60)
    if diff.exit_code != 0 or untracked.exit_code != 0:
        return None

    changed: dict[str, set[int]] = {}
    current: set[int] | None = None
    for line in diff.stdout.splitlines():
        if line.startswith("+++ "):
            name = line[4:].removeprefix("b/")
            current = None if name == "/dev/null" else changed.setdefault(name, set())
        elif current is not None and (match := _HUNK.match(line)):
            start, count = int(match[1]), int(match[2] or 1)
            current.update(range(start, start + count))
    for name in untracked.stdout.splitlines():
        try:
            n = len((root / name).read_text(errors="replace").splitlines())
        except OSError:
            continue
        changed[name] = set(range(1, n + 1))
    return changed


def _ranges(lines: list[int]) -> str:
    """[41, 42, 43, 57] -> "41-43, 57"."""
    parts: list[str] = []
    start = prev = lines[0]
    for n in lines[1:] + [0]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    return ", ".join(parts)

