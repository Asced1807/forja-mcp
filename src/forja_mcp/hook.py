"""Hook PostToolUse de Claude Code: revisa el archivo recién editado.

Silencio total si está bien: cero contexto gastado. Si el archivo editado
tiene errores, los imprime en stderr y sale con código 2, y Claude Code se
los enseña a la IA en el acto, sin que tenga que acordarse de llamar a check.

Solo informa de errores del archivo EDITADO: mientras se edita en varios
pasos, los demás archivos pueden estar a medias y no hay que meter ruido.
"""

import asyncio
import json
import sys
from pathlib import Path

from forja_mcp.adapters import detect_all, find_project
from forja_mcp.model import CheckReport, Diagnostic, merge_checks

TIMEOUT = 60


def review(event: dict[str, object]) -> str | None:
    """Devuelve el aviso para la IA, o None si no hay nada que decir."""
    tool_input = event.get("tool_input")
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(path, str):
        return None
    file = Path(path).expanduser().resolve()
    root = find_project(file) if file.is_file() else None
    if root is None:
        return None
    # solo los lenguajes a los que pertenece el archivo editado
    adapters = [a for a in detect_all(root) if file in {f.resolve() for f in a.files(root)}]
    if not adapters:
        return None

    async def run_checks() -> list[CheckReport]:
        return list(await asyncio.gather(*(a.check(root, TIMEOUT) for a in adapters)))

    report = merge_checks(str(root), asyncio.run(run_checks()))
    from forja_mcp.server import (
        _remember,  # import tardío: el hook arranca rápido si no hay nada que hacer
    )

    _remember(root, "check", report.diagnostics, report.tools, complete=False)
    rel = str(file.relative_to(root))
    errors = [d for d in report.diagnostics if d.file == rel and d.severity == "error"]
    broken = [t for t in report.tools if not t.ran]
    if not errors and not broken:
        return None

    lines = [f"forja: {len(errors)} error(es) en {rel} tras la edición:"] if errors else []
    lines += [_format(d) for d in errors]
    lines += [f"forja: no se pudo ejecutar {t.name}: {t.note}" for t in broken]
    return "\n".join(lines)


def _format(d: Diagnostic) -> str:
    text = f"  {d.line}:{d.column} [{d.tool} {d.code}] {d.message}"
    if d.source:
        text += f"\n      código: {d.source}"
    if d.hint:
        text += "\n      pista: " + d.hint.replace("\n", "\n             ")
    if d.memory:
        text += f"\n      memoria: {d.memory}"
    return text


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        sys.exit(0)  # un hook roto nunca debe bloquear la edición
    message = review(event) if isinstance(event, dict) else None
    if message:
        print(message, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)
