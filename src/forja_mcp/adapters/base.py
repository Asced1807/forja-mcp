"""Base de los adaptadores de lenguaje.

Un adaptador solo tiene que decir qué archivos son suyos y cómo revisarlos.
Lo que no sepa hacer (tests, mutaciones, correcciones) lo hereda como "no
aplica", y el servidor lo trata como tal: nunca como un éxito inventado.
"""

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path

from forja_mcp.files import files_with
from forja_mcp.model import CheckReport, Diagnostic, MutationReport, TestReport, ToolRun
from forja_mcp.runner import Result, run

MAX_PARALLEL = 8


class BaseAdapter:
    name = ""
    extensions: tuple[str, ...] = ()

    def files(self, root: Path) -> list[Path]:
        return files_with(root, self.extensions)

    def detect(self, root: Path) -> bool:
        return bool(self.files(root))

    def tools(self, root: Path) -> dict[str, str]:
        """Qué ejecutaría en cada fase, para `overview`: {"check": "...", "test": "..."}."""
        return {}

    async def check(self, root: Path, timeout: float) -> CheckReport:
        raise NotImplementedError

    async def fix(self, root: Path, timeout: float) -> CheckReport:
        """Sin correcciones automáticas para este lenguaje: solo vuelve a comprobar."""
        return await self.check(root, timeout)

    async def test(
        self, root: Path, selector: str | None, timeout: float, coverage: bool = False
    ) -> TestReport | None:
        """None = este lenguaje no tiene tests que ejecutar en el proyecto."""
        return None

    async def mutate(
        self, root: Path, file: str | None, max_mutants: int, timeout: float
    ) -> MutationReport | None:
        return None


Parse = Callable[[Result, Path], list[Diagnostic]]


async def per_file(
    tool: str,
    files: list[Path],
    command: Callable[[Path], list[str]],
    parse: Parse,
    root: Path,
    timeout: float,
) -> tuple[ToolRun, list[Diagnostic]]:
    """Ejecuta un revisor archivo por archivo (php -l, luac -p...) en paralelo."""
    if not files:
        return ToolRun(tool, True, 0), []
    executable = command(files[0])[0]
    if shutil.which(executable) is None:
        return ToolRun(tool, False, None, f"no está instalado: {executable}"), []

    semaphore = asyncio.Semaphore(MAX_PARALLEL)

    async def one(path: Path) -> tuple[Result, Path]:
        async with semaphore:
            return await run(command(path), root, timeout), path

    results = await asyncio.gather(*(one(f) for f in files))
    diagnostics: list[Diagnostic] = []
    for res, path in results:
        if res.exit_code is None:
            diagnostics.append(
                Diagnostic(str(path.relative_to(root)), 1, 1, "error", tool, None,
                           res.note or "no se pudo revisar")
            )
        elif res.exit_code != 0:
            found = parse(res, path)
            if not found:
                # falló pero no entiendo el formato: el error NO se puede perder
                raw = (res.stderr or res.stdout).strip().splitlines()
                found = [Diagnostic(str(path.relative_to(root)), 1, 1, "error", tool, None,
                                    " | ".join(raw[:3])[:300] or f"salió con código {res.exit_code}")]
            diagnostics.extend(found)
    return ToolRun(tool, True, 0), diagnostics


def report(root: Path, adapter: str, *parts: tuple[ToolRun, list[Diagnostic]]) -> CheckReport:
    result = CheckReport(project=str(root), adapter=adapter)
    for tool, diagnostics in parts:
        result.tools.append(tool)
        result.diagnostics.extend(diagnostics)
    return result.finalize()


def which(name: str, root: Path, local: tuple[str, ...] = ()) -> str | None:
    """Busca primero una instalación local del proyecto (node_modules/.bin, vendor/bin...)."""
    for prefix in local:
        candidate = root / prefix / name
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)

