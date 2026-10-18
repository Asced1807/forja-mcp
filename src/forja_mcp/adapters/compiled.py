"""Lenguajes compilados con su propia herramienta de proyecto: Rust (cargo) y Go."""

import json
import re
import shutil
from pathlib import Path

from forja_mcp.adapters.base import BaseAdapter, report
from forja_mcp.model import (
    CheckReport,
    Diagnostic,
    Severity,
    TestFailure,
    TestReport,
    ToolRun,
)
from forja_mcp.parsers import parse_gcc, rel
from forja_mcp.runner import run

_RUST_RESULT = re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored")
_RUST_FAIL = re.compile(r"^test (\S+) \.\.\. FAILED$", re.MULTILINE)
_RUST_PANIC = re.compile(r"thread '(?P<test>[^']+)'.*? panicked at (?P<file>[^:\n]+):(?P<line>\d+):\d+:\n(?P<msg>(?:.+\n?){1,4})")
_GO_LOC = re.compile(r"^\s+(?P<file>\S+\.go):(?P<line>\d+): (?P<msg>.+)$", re.MULTILINE)


class RustAdapter(BaseAdapter):
    name = "rust"
    extensions = (".rs",)

    def detect(self, root: Path) -> bool:
        return (root / "Cargo.toml").exists()

    def tools(self, root: Path) -> dict[str, str]:
        return {"check": "cargo clippy" if shutil.which("cargo-clippy") else "cargo check", "test": "cargo test"}

    async def check(self, root: Path, timeout: float) -> CheckReport:
        if shutil.which("cargo") is None:
            return report(root, self.name, (ToolRun("cargo", False, None, "no está instalado: cargo"), []))
        sub = "clippy" if shutil.which("cargo-clippy") else "check"
        res = await run(["cargo", sub, "--all-targets", "--message-format=json", "-q"], root, timeout)
        if res.exit_code is None or (res.exit_code != 0 and '"reason":"compiler-message"' not in res.stdout):
            note = res.note or res.stderr.strip()[-400:]
            return report(root, self.name, (ToolRun(f"cargo {sub}", False, res.exit_code, note), []))
        return report(root, self.name, (ToolRun(f"cargo {sub}", True, res.exit_code), _cargo_messages(res.stdout, root)))

    async def fix(self, root: Path, timeout: float) -> CheckReport:
        if shutil.which("cargo"):
            await run(["cargo", "clippy" if shutil.which("cargo-clippy") else "fix", "--fix", "--allow-dirty",
                       "--allow-staged", "-q"], root, timeout)
        return await self.check(root, timeout)

    async def test(
        self, root: Path, selector: str | None, timeout: float, coverage: bool = False
    ) -> TestReport | None:
        result = TestReport(project=str(root), adapter=self.name)
        res = await run(["cargo", "test", "--no-fail-fast", "--message-format=json",
                         *([selector] if selector else [])], root, timeout)
        out = res.stdout + "\n" + res.stderr
        totals = _RUST_RESULT.findall(out)
        if res.exit_code is None or not totals:
            # no llegó a ejecutar tests: casi siempre es que no compila
            compile_errors = [d for d in _cargo_messages(res.stdout, root) if d.severity == "error"]
            for d in compile_errors:
                result.errors += 1
                result.failures.append(TestFailure("compilación", "error", d.message, d.file, d.line))
            result.tools.append(ToolRun("cargo test", bool(compile_errors), res.exit_code,
                                        None if compile_errors else (res.note or res.stderr.strip()[-400:])))
            return result.finalize()
        result.tools.append(ToolRun("cargo test", True, res.exit_code))
        for passed, failed, ignored in totals:
            result.passed += int(passed)
            result.failed += int(failed)
            result.skipped += int(ignored)
        result.total = result.passed + result.failed + result.skipped
        panics = {m["test"]: m for m in _RUST_PANIC.finditer(out)}
        for name in _RUST_FAIL.findall(out):
            p = panics.get(name)
            result.failures.append(TestFailure(
                name, "failure", p["msg"].strip() if p else "FAILED",
                rel(p["file"], root) if p else None, int(p["line"]) if p else None,
                p["msg"].strip() if p else "",
            ))
        return result.finalize()


def _cargo_messages(stdout: str, root: Path) -> list[Diagnostic]:
    diagnostics = []
    for raw in stdout.splitlines():
        if not raw.startswith("{"):
            continue
        item = json.loads(raw)
        msg = item.get("message")
        if item.get("reason") != "compiler-message" or not msg:
            continue
        spans = [s for s in msg["spans"] if s.get("is_primary")]
        level = msg["level"]
        if not spans or level not in ("error", "warning"):
            continue  # "aborting due to N errors" y similares no apuntan a ningún sitio
        span = spans[0]
        severity: Severity = "error" if level == "error" else "warning"
        hints = [c["message"] for c in msg.get("children", []) if c["level"] in ("help", "note")]
        diagnostics.append(Diagnostic(
            rel(span["file_name"], root), span["line_start"], span["column_start"], severity,
            "cargo", (msg.get("code") or {}).get("code"), msg["message"],
            hint="\n".join(hints[:3]) or None,
            fixable=any(s.get("suggested_replacement") for c in msg.get("children", []) for s in c.get("spans", [])),
        ))
    return diagnostics


class GoAdapter(BaseAdapter):
    name = "go"
    extensions = (".go",)

    def detect(self, root: Path) -> bool:
        return (root / "go.mod").exists()

    def tools(self, root: Path) -> dict[str, str]:
        return {"check": "go vet", "test": "go test"}

    async def check(self, root: Path, timeout: float) -> CheckReport:
        if shutil.which("go") is None:
            return report(root, self.name, (ToolRun("go", False, None, "no está instalado: go"), []))
        res = await run(["go", "vet", "./..."], root, timeout)
        found = parse_gcc(res.stderr, root, "go vet", strip_prefix="vet: ")
        if res.exit_code not in (0, 1) and not found:
            return report(root, self.name, (ToolRun("go vet", False, res.exit_code, res.note or res.stderr[-400:]), []))
        return report(root, self.name, (ToolRun("go vet", True, res.exit_code), found))

    async def test(
        self, root: Path, selector: str | None, timeout: float, coverage: bool = False
    ) -> TestReport | None:
        result = TestReport(project=str(root), adapter=self.name)
        args = ["-run", selector] if selector else []
        res = await run(["go", "test", "-json", *args, "./..."], root, timeout)
        if res.exit_code is None:
            result.tools.append(ToolRun("go test", False, None, res.note))
            return result.finalize()
        outputs: dict[str, list[str]] = {}
        build: list[str] = []
        for raw in res.stdout.splitlines():
            if not raw.startswith("{"):
                continue
            ev = json.loads(raw)
            action, test = ev.get("Action"), ev.get("Test")
            if action == "build-output":
                build.append(ev.get("Output", ""))
            elif test and action == "output":
                outputs.setdefault(test, []).append(ev.get("Output", ""))
            elif test and action in ("pass", "fail", "skip"):
                result.total += 1
                if action == "pass":
                    result.passed += 1
                elif action == "skip":
                    result.skipped += 1
                else:
                    result.failed += 1
                    text = "".join(outputs.get(test, []))
                    loc = _GO_LOC.search(text)
                    result.failures.append(TestFailure(
                        test, "failure", loc["msg"] if loc else "FAIL",
                        rel(loc["file"], root) if loc else None, int(loc["line"]) if loc else None,
                        text.strip()[-800:],
                    ))
        for d in parse_gcc("".join(build), root, "go build"):
            result.errors += 1
            result.failures.append(TestFailure("compilación", "error", d.message, d.file, d.line))
        result.tools.append(ToolRun("go test", True, res.exit_code))
        return result.finalize()
