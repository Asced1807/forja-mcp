"""Lenguajes con revisión de sintaxis archivo por archivo: PHP, Lua y JavaScript."""

import json
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from forja_mcp import junit
from forja_mcp.adapters.base import BaseAdapter, per_file, report, which
from forja_mcp.model import CheckReport, Diagnostic, TestReport, ToolRun
from forja_mcp.parsers import parse_gcc, rel
from forja_mcp.runner import Result, run

_PHP = re.compile(r"(?:PHP )?(?P<kind>Parse|Fatal) error:\s*(?P<msg>.+?) in (?P<file>.+?) on line (?P<line>\d+)")
_NODE_LOC = re.compile(r"^(?P<file>/[^\n]+?):(?P<line>\d+)$", re.MULTILINE)
_NODE_ERR = re.compile(r"^(?P<msg>\w*Error: .+)$", re.MULTILINE)
_TSC = re.compile(r"^(?P<file>[^\s(][^(\n]*)\((?P<line>\d+),(?P<col>\d+)\): (?P<sev>error|warning) (?P<code>TS\d+): (?P<msg>.+)$",
                  re.MULTILINE)


class PhpAdapter(BaseAdapter):
    name = "php"
    extensions = (".php",)

    def tools(self, root: Path) -> dict[str, str]:
        tools = {"check": "php -l"}
        if which("phpunit", root, ("vendor/bin",)):
            tools["test"] = "phpunit"
        return tools

    async def check(self, root: Path, timeout: float) -> CheckReport:
        def parse(res: Result, path: Path) -> list[Diagnostic]:
            return [
                Diagnostic(rel(m["file"], root), int(m["line"]), 1, "error", "php -l", None, m["msg"])
                for m in _PHP.finditer(res.stdout + res.stderr)
            ]

        return report(root, self.name, await per_file(
            "php -l", self.files(root), lambda f: ["php", "-l", "-d", "display_errors=stderr", str(f)],
            parse, root, timeout,
        ))

    async def test(
        self, root: Path, selector: str | None, timeout: float, coverage: bool = False
    ) -> TestReport | None:
        phpunit = which("phpunit", root, ("vendor/bin",))
        if phpunit is None:
            return None
        return await junit_run("phpunit", [phpunit, *([selector] if selector else [])],
                               lambda xml: ["--log-junit", str(xml)], root, self.name, timeout)


class LuaAdapter(BaseAdapter):
    name = "lua"
    extensions = (".lua",)

    def tools(self, root: Path) -> dict[str, str]:
        return {"check": "luac -p"}

    async def check(self, root: Path, timeout: float) -> CheckReport:
        def parse(res: Result, path: Path) -> list[Diagnostic]:
            return parse_gcc(res.stderr, root, "luac", strip_prefix="luac: ")

        return report(root, self.name, await per_file(
            "luac", self.files(root), lambda f: ["luac", "-p", str(f)], parse, root, timeout
        ))


class JsAdapter(BaseAdapter):
    """JavaScript/TypeScript: sintaxis con node; tipos y lint si el proyecto trae tsc/eslint."""

    name = "js"
    extensions = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".jsx")
    _LOCAL = ("node_modules/.bin",)

    def tools(self, root: Path) -> dict[str, str]:
        checks = ["node --check"]
        if (root / "tsconfig.json").exists():
            checks.append("tsc")
        if which("eslint", root, self._LOCAL):
            checks.append("eslint")
        tools = {"check": " + ".join(checks)}
        runner = self._test_runner(root)
        if runner:
            tools["test"] = runner
        return tools

    async def check(self, root: Path, timeout: float) -> CheckReport:
        files = self.files(root)
        plain = [f for f in files if f.suffix in (".js", ".mjs", ".cjs")]
        typed = [f for f in files if f not in plain]
        parts = [await per_file("node --check", plain, lambda f: ["node", "--check", str(f)],
                                self._parse_node, root, timeout)]
        if typed:
            parts.append(await self._tsc(root, timeout))
        if which("eslint", root, self._LOCAL):
            parts.append(await self._eslint(root, timeout))
        return report(root, self.name, *parts)

    def _parse_node(self, res: Result, path: Path) -> list[Diagnostic]:
        loc, err = _NODE_LOC.search(res.stderr), _NODE_ERR.search(res.stderr)
        if not loc or not err:
            return []
        return [Diagnostic(rel(loc["file"], path.parent), int(loc["line"]), 1, "error",
                           "node --check", None, err["msg"])]

    async def _tsc(self, root: Path, timeout: float) -> tuple[ToolRun, list[Diagnostic]]:
        tsc = which("tsc", root, self._LOCAL)
        if tsc is None:
            return ToolRun("tsc", False, None, "hay TypeScript pero falta tsc: npm i -D typescript"), []
        res = await run([tsc, "--noEmit", "--pretty", "false"], root, timeout)
        if res.exit_code not in (0, 1, 2):
            return ToolRun("tsc", False, res.exit_code, res.note or res.stderr.strip()[-300:]), []
        return ToolRun("tsc", True, res.exit_code), [
            Diagnostic(rel(m["file"], root), int(m["line"]), int(m["col"]),
                       "error" if m["sev"] == "error" else "warning", "tsc", m["code"], m["msg"])
            for m in _TSC.finditer(res.stdout)
        ]

    async def _eslint(self, root: Path, timeout: float) -> tuple[ToolRun, list[Diagnostic]]:
        eslint = which("eslint", root, self._LOCAL)
        assert eslint is not None
        res = await run([eslint, "-f", "json", "."], root, timeout)
        if res.exit_code not in (0, 1):
            return ToolRun("eslint", False, res.exit_code, res.note or res.stderr.strip()[-300:]), []
        diagnostics = []
        for entry in json.loads(res.stdout or "[]"):
            for msg in entry["messages"]:
                diagnostics.append(Diagnostic(
                    rel(entry["filePath"], root), msg.get("line", 1), msg.get("column", 1),
                    "error" if msg.get("severity") == 2 else "warning", "eslint",
                    msg.get("ruleId"), msg["message"], fixable="fix" in msg,
                ))
        return ToolRun("eslint", True, res.exit_code), diagnostics

    def _test_runner(self, root: Path) -> str | None:
        if which("vitest", root, self._LOCAL) and (root / "node_modules").exists():
            return "vitest"
        try:
            scripts = json.loads((root / "package.json").read_text()).get("scripts", {})
        except (OSError, ValueError):
            return None
        return "node --test" if "node --test" in scripts.get("test", "") else None

    async def test(
        self, root: Path, selector: str | None, timeout: float, coverage: bool = False
    ) -> TestReport | None:
        runner = self._test_runner(root)
        if runner == "vitest":
            vitest = which("vitest", root, self._LOCAL)
            assert vitest is not None
            return await junit_run("vitest", [vitest, "run", "--reporter=junit", *([selector] if selector else [])],
                                   lambda xml: [f"--outputFile={xml}"], root, self.name, timeout)
        if runner == "node --test":
            return await junit_run("node --test", ["node", "--test", "--test-reporter=junit"],
                                   lambda xml: [f"--test-reporter-destination={xml}",
                                                *([selector] if selector else [])],
                                   root, self.name, timeout)
        return None


async def junit_run(
    tool: str,
    cmd: list[str],
    xml_args: Callable[[Path], list[str]],
    root: Path,
    adapter: str,
    timeout: float,
) -> TestReport:
    """Ejecuta un runner de tests que escribe JUnit XML y lo traduce al formato único."""
    report_ = TestReport(project=str(root), adapter=adapter)
    with tempfile.TemporaryDirectory(prefix="forja-") as tmp:
        xml = Path(tmp) / "junit.xml"
        res = await run([*cmd, *xml_args(xml)], root, timeout)
        if xml.exists() and xml.stat().st_size and res.exit_code is not None:
            report_.tools.append(ToolRun(tool, True, res.exit_code))
            junit.parse(xml, report_)
            if res.exit_code != 0 and report_.failed == report_.errors == 0:
                report_.output_tail = (res.stdout + res.stderr).strip()[-1500:] or None
                report_.tools[-1].note = "el runner falló sin tests fallidos: mira output_tail"
                report_.tools[-1].ran = False
        else:
            note = res.note or (res.stdout + res.stderr).strip()[-300:] or f"código {res.exit_code}"
            report_.tools.append(ToolRun(tool, False, res.exit_code, note))
    return report_.finalize()
