"""Herramientas declaradas por el proyecto en `.forja.toml`: cualquier lenguaje, sin código.

    [[check]]
    name = "phpstan"
    cmd = ["vendor/bin/phpstan", "analyse", "--error-format=raw", "--no-progress"]
    ok_codes = [0, 1]          # opcional: códigos con los que la herramienta funcionó

    [[test]]
    name = "phpunit"
    cmd = ["vendor/bin/phpunit", "--log-junit", "{junit}"]   # {junit}: forja lee ese XML

    [forja]
    disable = ["php"]          # opcional: adaptadores automáticos que no quieres

La salida de cada check se lee en formato gcc: `archivo:línea[:col]: [error|warning:] mensaje`.
Un test sin `{junit}` se juzga solo por su código de salida.
"""

import tempfile
import tomllib
from pathlib import Path
from typing import Any

from forja_mcp import junit
from forja_mcp.adapters.base import BaseAdapter, report
from forja_mcp.model import CheckReport, Diagnostic, TestFailure, TestReport, ToolRun
from forja_mcp.parsers import parse_gcc
from forja_mcp.runner import run

CONFIG = ".forja.toml"


def load(root: Path) -> dict[str, Any]:
    """La configuración del proyecto; {} si no hay. Un TOML roto se informa, no se ignora."""
    path = root / CONFIG
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        return {"_error": f"{CONFIG} no es TOML válido: {e}"}


def disabled(root: Path) -> set[str]:
    value = load(root).get("forja", {}).get("disable", [])
    return {str(v) for v in value} if isinstance(value, list) else set()


class CustomAdapter(BaseAdapter):
    name = "custom"

    def detect(self, root: Path) -> bool:
        config = load(root)
        return bool(config.get("check") or config.get("test") or config.get("_error"))

    def tools(self, root: Path) -> dict[str, str]:
        config = load(root)
        tools = {}
        for phase in ("check", "test"):
            names = [str(e.get("name", "?")) for e in config.get(phase, []) if isinstance(e, dict)]
            if names:
                tools[phase] = " + ".join(names) + f" ({CONFIG})"
        return tools

    async def check(self, root: Path, timeout: float) -> CheckReport:
        config = load(root)
        if "_error" in config:
            return report(root, self.name, (ToolRun(CONFIG, False, None, config["_error"]), []))
        parts = []
        for entry in config.get("check", []):
            parts.append(await _run_check(entry, root, timeout))
        return report(root, self.name, *parts)

    async def test(
        self, root: Path, selector: str | None, timeout: float, coverage: bool = False
    ) -> TestReport | None:
        entries = load(root).get("test", [])
        if not entries:
            return None
        result = TestReport(project=str(root), adapter=self.name)
        for entry in entries:
            await _run_test(entry, root, timeout, result)
        return result.finalize()


def _command(entry: Any) -> tuple[str, list[str]] | str:
    if not isinstance(entry, dict) or not isinstance(entry.get("cmd"), list) or not entry["cmd"]:
        return f"cada entrada de {CONFIG} necesita cmd = [\"programa\", \"args\"...]"
    return str(entry.get("name", entry["cmd"][0])), [str(c) for c in entry["cmd"]]


async def _run_check(entry: Any, root: Path, timeout: float) -> tuple[ToolRun, list[Diagnostic]]:
    parsed = _command(entry)
    if isinstance(parsed, str):
        return ToolRun(CONFIG, False, None, parsed), []
    name, cmd = parsed
    ok_codes = entry.get("ok_codes", [0, 1])
    res = await run(cmd, root, timeout)
    found = parse_gcc(res.stdout + "\n" + res.stderr, root, name)
    if res.exit_code is None or (res.exit_code not in ok_codes and not found):
        return ToolRun(name, False, res.exit_code, res.note or (res.stdout + res.stderr).strip()[-300:]), []
    if res.exit_code != 0 and not found:
        # dice que hay problemas pero no en un formato que sepa leer: no se puede dar por bueno
        found = [Diagnostic(CONFIG, 1, 1, "error", name, None,
                            f"salió con {res.exit_code}: " + (res.stdout + res.stderr).strip()[-200:])]
    return ToolRun(name, True, res.exit_code), found


async def _run_test(entry: Any, root: Path, timeout: float, result: TestReport) -> None:
    parsed = _command(entry)
    if isinstance(parsed, str):
        result.tools.append(ToolRun(CONFIG, False, None, parsed))
        return
    name, cmd = parsed
    with tempfile.TemporaryDirectory(prefix="forja-") as tmp:
        xml = Path(tmp) / "junit.xml"
        uses_junit = any("{junit}" in c for c in cmd)
        res = await run([c.replace("{junit}", str(xml)) for c in cmd], root, timeout)
        if res.exit_code is None:
            result.tools.append(ToolRun(name, False, None, res.note))
        elif uses_junit and xml.exists():
            result.tools.append(ToolRun(name, True, res.exit_code))
            junit.parse(xml, result)
        else:
            result.tools.append(ToolRun(name, True, res.exit_code))
            result.total += 1
            if res.exit_code == 0:
                result.passed += 1
            else:
                result.failed += 1
                tail = (res.stdout + res.stderr).strip()
                result.failures.append(TestFailure(name, "failure", f"salió con {res.exit_code}",
                                                   traceback=tail[-1500:]))
