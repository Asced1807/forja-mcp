"""Lectores de salida compartidos por varios lenguajes.

El formato "gcc" (`archivo:línea[:columna]: [error|warning:] mensaje`) lo usan
gcc, clang, go vet, luac, muchos linters... Con este lector, cualquier herramienta
que lo imprima se integra sin escribir código (ver `.forja.toml`).
"""

import re
from pathlib import Path

from forja_mcp.model import Diagnostic, Severity

_GCC = re.compile(
    r"^(?P<file>[^\s:][^:\n]*?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
    r"(?:(?P<sev>fatal error|error|warning|note|info)\s*:\s*)?(?P<msg>\S.*)$",
    re.MULTILINE,
)
_SEVERITY: dict[str, Severity] = {
    "fatal error": "error", "error": "error", "warning": "warning", "note": "info", "info": "info",
}


def parse_gcc(
    text: str, root: Path, tool: str, default: Severity = "error", strip_prefix: str = ""
) -> list[Diagnostic]:
    diagnostics = []
    for raw in text.splitlines():
        line = raw.removeprefix(strip_prefix).strip() if strip_prefix else raw.strip()
        match = _GCC.match(line)
        if not match:
            continue
        sev = match["sev"]
        diagnostics.append(
            Diagnostic(
                file=rel(match["file"], root),
                line=int(match["line"]),
                column=int(match["col"] or 1),
                severity=_SEVERITY[sev] if sev else default,
                tool=tool,
                code=None,
                message=match["msg"].strip(),
            )
        )
    return diagnostics


def rel(path: str, root: Path) -> str:
    """Ruta relativa al proyecto, venga absoluta, relativa o con './'."""
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        return str(p.resolve().relative_to(root))
    except ValueError:
        return path
