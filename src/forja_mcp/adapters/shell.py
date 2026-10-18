"""Shell (sh/bash) con shellcheck, y fish con su comprobación de sintaxis."""

import json
import re
import sys
from pathlib import Path

from forja_mcp.adapters.base import BaseAdapter, per_file, report
from forja_mcp.files import project_files, shebang
from forja_mcp.model import CheckReport, Diagnostic, Severity, ToolRun
from forja_mcp.parsers import rel
from forja_mcp.runner import Result, run

# shellcheck viene empaquetado con forja (shellcheck-py): no hace falta instalarlo
_SHELLCHECK = str(Path(sys.executable).parent / "shellcheck")
_SH_SHEBANG = re.compile(r"^#!.*\b(ba|da|k)?sh\b")
_BASH_N = re.compile(r"^(?P<file>.+?): line (?P<line>\d+): (?P<msg>.+)$", re.MULTILINE)
_FISH = re.compile(r"^(?P<file>.+?) \(line (?P<line>\d+)\): (?P<msg>.+)$", re.MULTILINE)
_LEVEL: dict[str, Severity] = {"error": "error", "warning": "warning"}
CHUNK = 200


class ShellAdapter(BaseAdapter):
    name = "shell"
    extensions = (".sh", ".bash")

    def files(self, root: Path) -> list[Path]:
        return [
            f for f in project_files(root)
            if f.suffix in self.extensions or (not f.suffix and _SH_SHEBANG.match(shebang(f)))
        ]

    def tools(self, root: Path) -> dict[str, str]:
        return {"check": "shellcheck"}

    async def check(self, root: Path, timeout: float) -> CheckReport:
        files = self.files(root)
        if Path(_SHELLCHECK).exists():
            return report(root, self.name, await _shellcheck(files, root, timeout))
        return report(root, self.name, await per_file("bash -n", files, lambda f: ["bash", "-n", str(f)],
                                                      _parse_bash_n, root, timeout))


async def _shellcheck(files: list[Path], root: Path, timeout: float) -> tuple[ToolRun, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    for i in range(0, len(files), CHUNK):
        chunk = [str(f) for f in files[i : i + CHUNK]]
        res = await run([_SHELLCHECK, "-f", "json1", "-S", "warning", *chunk], root, timeout)
        # 0 = limpio, 1 = hallazgos; otro código = fallo de shellcheck
        if res.exit_code not in (0, 1):
            return ToolRun("shellcheck", False, res.exit_code, res.note or res.stderr.strip()[-300:]), []
        for item in json.loads(res.stdout or '{"comments": []}')["comments"]:
            diagnostics.append(
                Diagnostic(
                    file=rel(item["file"], root),
                    line=item["line"],
                    column=item["column"],
                    severity=_LEVEL.get(item["level"], "info"),
                    tool="shellcheck",
                    code=f"SC{item['code']}",
                    message=item["message"],
                    fixable=bool(item.get("fix")),
                )
            )
    return ToolRun("shellcheck", True, 0), diagnostics


def _parse_bash_n(res: Result, path: Path) -> list[Diagnostic]:
    root = path.parent
    return [
        Diagnostic(rel(m["file"], root), int(m["line"]), 1, "error", "bash -n", None, m["msg"])
        for m in _BASH_N.finditer(res.stderr)
    ]


class FishAdapter(BaseAdapter):
    name = "fish"
    extensions = (".fish",)

    def tools(self, root: Path) -> dict[str, str]:
        return {"check": "fish --no-execute"}

    async def check(self, root: Path, timeout: float) -> CheckReport:
        def parse(res: Result, path: Path) -> list[Diagnostic]:
            # fish sale con 127 ante un error de sintaxis (no significa "no instalado")
            return [
                Diagnostic(str(path.relative_to(root)), int(m["line"]), 1, "error", "fish", None, m["msg"])
                for m in _FISH.finditer(res.stderr)
            ]

        return report(root, self.name, await per_file(
            "fish", self.files(root), lambda f: ["fish", "--no-execute", str(f)], parse, root, timeout
        ))
