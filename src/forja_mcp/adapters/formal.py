"""Verificación formal: Dafny y Lean. El "compilador de IA".

Un humano escribe QUÉ debe cumplirse (requires/ensures, el enunciado de un teorema); la IA
escribe el CÓMO; el verificador DEMUESTRA que el código lo cumple. No hace falta fiarse de
la IA: o la demostración pasa, o no compila.

Para que eso valga de algo, forja vigila las dos trampas posibles:
  - demostrar "a medias": sorry/admit/axiom en Lean, assume/{:axiom}/{:verify false} en Dafny
  - debilitar la especificación: ver spec.py (forja-spec lock)
Las dos son errores, nunca un éxito.
"""

import re
import shutil
from pathlib import Path

from forja_mcp import spec
from forja_mcp.adapters.base import BaseAdapter, report, which
from forja_mcp.files import project_files
from forja_mcp.model import CheckReport, Diagnostic, Severity, ToolRun
from forja_mcp.parsers import rel
from forja_mcp.runner import Result, run

MAX_GOAL_LINES = 12

_DAFNY_MSG = re.compile(
    r"^(?P<file>[^\s(][^(\n]*)\((?P<line>\d+),(?P<col>\d+)\): (?P<sev>Error|Warning|Related location|Info)"
    r"(?:\s*\[[^\]]*\])?: (?P<msg>.+)$"
)
_LEAN_MSG = re.compile(
    r"^(?:(?:error|warning|info): )?(?P<file>[^\s:][^:\n]*\.lean):(?P<line>\d+):(?P<col>\d+): "
    r"(?P<sev>error|warning|info)(?:\([^)]*\))?: (?P<msg>.*)$"
)

# (patrón, explicación). Se buscan fuera de comentarios y cadenas.
_DAFNY_CHEATS = [
    (re.compile(r"\bassume\b"), "`assume` da por cierto algo sin demostrarlo"),
    (re.compile(r"\{:\s*axiom\s*\}"), "`{:axiom}` declara algo como cierto sin demostrarlo"),
    (re.compile(r"\{:\s*verify\s+false\s*\}"), "`{:verify false}` desactiva la verificación"),
]
_LEAN_CHEATS = [
    (re.compile(r"\bsorry\b"), "`sorry` deja la demostración sin hacer"),
    (re.compile(r"\badmit\b"), "`admit` deja la demostración sin hacer"),
    (re.compile(r"^\s*(?:private\s+)?axiom\b"), "`axiom` declara algo como cierto sin demostrarlo"),
]
_STRINGS_COMMENTS = {
    ".dfy": re.compile(r'"(?:\\.|[^"\\])*"|//.*$'),
    ".lean": re.compile(r'"(?:\\.|[^"\\])*"|--.*$'),
}


def cheats(root: Path, files: list[Path]) -> list[Diagnostic]:
    """Atajos que hacen 'verificar' algo que no se ha demostrado."""
    out = []
    for path in files:
        patterns = _DAFNY_CHEATS if path.suffix == ".dfy" else _LEAN_CHEATS
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if path.suffix == ".lean":
            text = re.sub(r"/-.*?-/", lambda m: "\n" * m.group().count("\n"), text, flags=re.DOTALL)
        else:
            text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), text, flags=re.DOTALL)
        for n, line in enumerate(text.splitlines(), start=1):
            code = _STRINGS_COMMENTS[path.suffix].sub("", line)
            for pattern, why in patterns:
                match = pattern.search(code)
                if match:
                    out.append(Diagnostic(
                        str(path.relative_to(root)), n, match.start() + 1, "error", "forja", "trampa",
                        f"{why}: la demostración no cuenta como hecha",
                        hint="demuéstralo de verdad; si no se puede, dilo en vez de ocultarlo",
                    ))
    return out


def spec_changes(root: Path, suffix: str) -> list[Diagnostic]:
    """Contratos bloqueados por el humano que alguien ha cambiado o borrado."""
    out = []
    for file, name, kind in spec.violations(root):
        if not file.endswith(suffix) and name != "*":
            continue
        out.append(Diagnostic(
            file, spec.line_of(root, file, name), 1, "error", "forja", "spec",
            f"la especificación de `{name}` fue {kind} después de bloquearla",
            hint="la especificación la fija el humano: deshaz el cambio y cambia el código. "
                 "Si el cambio es intencionado, el humano debe volver a ejecutar `forja-spec lock`",
        ))
    return out


class DafnyAdapter(BaseAdapter):
    name = "dafny"
    extensions = (".dfy",)

    def tools(self, root: Path) -> dict[str, str]:
        return {"check": "dafny verify + trampas + especificación bloqueada"}

    async def check(self, root: Path, timeout: float) -> CheckReport:
        files = self.files(root)
        dafny = which("dafny", root) or _home_tool("dafny")
        parts = [(ToolRun("forja trampas", True, 0), cheats(root, files) + spec_changes(root, ".dfy"))]
        if dafny is None:
            parts.append((ToolRun("dafny", False, None,
                                  "no está instalado: dafny (o usa la imagen Docker de forja)"), []))
            return report(root, self.name, *parts)
        res = await run([dafny, "verify", "--cores", "2", *(str(f) for f in files)], root, timeout)
        found = _dafny_messages(res.stdout + "\n" + res.stderr, root)
        # 0 = verificado; 2/4 = errores de resolución o verificación; otro = fallo de la herramienta
        if res.exit_code is None or (res.exit_code not in (0, 2, 4) and not found):
            note = res.note or (res.stdout + res.stderr).strip()[-400:]
            parts.append((ToolRun("dafny", False, res.exit_code, note), []))
        else:
            if res.exit_code != 0 and not found:
                found = [Diagnostic(str(files[0].relative_to(root)), 1, 1, "error", "dafny", None,
                                    (res.stdout + res.stderr).strip()[-300:])]
            parts.append((ToolRun("dafny", True, res.exit_code), found))
        return report(root, self.name, *parts)


def _dafny_messages(text: str, root: Path) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for line in text.splitlines():
        match = _DAFNY_MSG.match(line.strip())
        if not match:
            continue
        if match["sev"] == "Related location" and out:
            # "esta es la postcondición que no se pudo demostrar": lo más útil para corregir
            file = rel(match["file"], root)
            code = _line(root / file, int(match["line"]))
            # el texto de la postcondición que falló: así no hace falta abrir el archivo
            related = f"{file}:{match['line']}: {match['msg']}" + (f" → {code}" if code else "")
            out[-1].hint = f"{out[-1].hint}\n{related}" if out[-1].hint else related
            continue
        out.append(Diagnostic(rel(match["file"], root), int(match["line"]), int(match["col"]) + 1,
                              _severity(match["sev"]), "dafny", None, match["msg"].strip()))
    return out


class LeanAdapter(BaseAdapter):
    name = "lean"
    extensions = (".lean",)

    def detect(self, root: Path) -> bool:
        return bool(self.files(root)) or _lake_project(root)

    def files(self, root: Path) -> list[Path]:
        return [f for f in project_files(root) if f.suffix == ".lean" and ".lake" not in f.parts]

    def tools(self, root: Path) -> dict[str, str]:
        return {"check": ("lake build" if _lake_project(root) else "lean") + " + trampas + especificación bloqueada"}

    async def check(self, root: Path, timeout: float) -> CheckReport:
        files = self.files(root)
        parts = [(ToolRun("forja trampas", True, 0), cheats(root, files) + spec_changes(root, ".lean"))]
        lake, lean = which("lake", root) or _home_tool("lake"), which("lean", root) or _home_tool("lean")
        outputs: list[tuple[Result, Path | None]] = []
        if _lake_project(root) and lake:
            outputs.append((await run([lake, "build"], root, timeout), None))
        elif lean:
            for f in files:  # sin proyecto lake: cada archivo por separado
                outputs.append((await run([lean, str(f)], root, timeout), f))
        else:
            parts.append((ToolRun("lean", False, None, "no está instalado: lean (o usa la imagen Docker de forja)"), []))
            return report(root, self.name, *parts)

        found: list[Diagnostic] = []
        for res, file in outputs:
            if res.exit_code is None:
                parts.append((ToolRun("lean", False, None, res.note), []))
                continue
            msgs = _lean_messages(res.stdout + "\n" + res.stderr, root)
            if res.exit_code != 0 and not any(d.severity == "error" for d in msgs):
                where = str(file.relative_to(root)) if file else "lakefile"
                msgs.append(Diagnostic(where, 1, 1, "error", "lean", None,
                                       (res.stdout + res.stderr).strip()[-300:] or f"código {res.exit_code}"))
            found += msgs
        parts.append((ToolRun("lake build" if _lake_project(root) else "lean", True, 0), found))
        return report(root, self.name, *parts)


def _lean_messages(text: str, root: Path) -> list[Diagnostic]:
    """Mensajes de Lean. Cada error trae debajo el objetivo pendiente: va en `hint`."""
    out: list[Diagnostic] = []
    goal: list[str] = []
    skip = False

    def close() -> None:
        if out and goal:
            out[-1].hint = "\n".join(goal[:MAX_GOAL_LINES]) + ("\n…" if len(goal) > MAX_GOAL_LINES else "")
        goal.clear()

    for line in text.splitlines():
        match = _LEAN_MSG.match(line.strip())
        if match:
            close()
            msg = match["msg"].strip()
            if "declaration uses 'sorry'" in msg:
                # Lean lo deja en simple aviso y en la línea del teorema; el detector de trampas
                # ya lo marca como ERROR en la línea exacta del sorry
                skip = True
                continue
            skip = False
            out.append(Diagnostic(rel(match["file"].removeprefix("./"), root), int(match["line"]),
                                  int(match["col"]) + 1, _severity(match["sev"]), "lean", None, msg or "error"))
        elif out and not skip and line.strip() and not line.startswith(("✖", "⚠", "✔", "Build completed", "error: build failed",
                                                           "Some required builds", "info:")):
            goal.append(line.rstrip())
    close()
    return out


def _line(path: Path, n: int) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return lines[n - 1].strip()[:160] if 0 < n <= len(lines) else ""


def _severity(word: str) -> Severity:
    lowered = word.lower()
    if lowered == "error":
        return "error"
    if lowered == "warning":
        return "warning"
    return "info"


def _lake_project(root: Path) -> bool:
    return any((root / f).exists() for f in ("lakefile.lean", "lakefile.toml"))


def _home_tool(name: str) -> str | None:
    """Instalaciones típicas fuera del PATH: ~/.elan/bin, ~/.dotnet/tools."""
    for folder in (Path.home() / ".elan" / "bin", Path.home() / ".dotnet" / "tools"):
        if (folder / name).exists():
            return str(folder / name)
    return shutil.which(name)
