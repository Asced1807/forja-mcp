"""Mutation testing para Python: ¿los tests detectan bugs pequeños?

Cambia el código de formas mínimas (un `+` por un `-`, `<` por `<=`, 18 por
19...) y ejecuta los tests con cada cambio. Si los tests siguen pasando, ese
"mutante" sobrevivió: hay un comportamiento que ningún test comprueba.

Todo ocurre en COPIAS temporales del proyecto: el código real nunca se toca.
"""

import ast
import asyncio
import io
import os
import shutil
import tempfile
import time
import tokenize
from dataclasses import dataclass, replace
from pathlib import Path

from forja_mcp.model import MutationReport, Survivor, ToolRun
from forja_mcp.runner import Result, run

_SWAPS = {
    "+": "-", "-": "+", "*": "/", "/": "*", "//": "/", "%": "*",
    "<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==",
    "and": "or", "or": "and", "True": "False", "False": "True",
}
_SKIP_DIRS = {
    ".venv", "venv", ".git", "__pycache__", ".tox", "build", "dist", "node_modules",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "tests", "test",
}
_COPY_IGNORE = shutil.ignore_patterns(*(_SKIP_DIRS - {"tests", "test"}), "*.pyc")
MAX_WORKERS = 4


@dataclass(frozen=True)
class Mutant:
    file: str
    line: int
    start: int  # posición en el texto (caracteres)
    end: int
    original: str
    replacement: str
    source: str
    mutated: str

    def apply(self, text: str) -> str:
        return text[: self.start] + self.replacement + text[self.end :]


async def mutate(
    python: Path, root: Path, file: str | None, max_mutants: int, timeout: float
) -> MutationReport:
    report = MutationReport(project=str(root), adapter="python")
    files = [root / file] if file else _source_files(root)
    candidates: list[Mutant] = []
    skipped: list[str] = []
    for path in files:
        try:
            candidates += _candidates(path, str(path.relative_to(root)))
        except (OSError, SyntaxError, tokenize.TokenError, ValueError):
            skipped.append(str(path.relative_to(root)))
    report.candidates = len(candidates)
    selected = _sample(candidates, max_mutants)
    if not selected:
        report.tools.append(ToolRun("mutation", False, None, "no hay mutaciones posibles en el código"))
        return report.finalize()

    deadline = time.monotonic() + timeout
    workers = min(MAX_WORKERS, os.cpu_count() or 1, len(selected))
    with tempfile.TemporaryDirectory(prefix="forja-mut-") as tmp:
        copies = [Path(tmp) / f"w{i}" for i in range(workers)]
        for copy in copies:
            shutil.copytree(root, copy, ignore=_COPY_IGNORE)

        # sin mutar, los tests tienen que pasar: si no, el resultado no significa nada
        start = time.monotonic()
        base = await _pytest(python, copies[0], timeout)
        if base.exit_code != 0:
            report.tools.append(
                ToolRun("pytest", False, base.exit_code,
                        base.note or "los tests no pasan sin mutar: arregla eso primero")
            )
            report.output_tail = (base.stdout + base.stderr).strip()[-1500:] or None
            return report.finalize()
        report.tools.append(ToolRun("pytest", True, 0))
        per_mutant = max(10.0, (time.monotonic() - start) * 3 + 5)

        queue: asyncio.Queue[Mutant] = asyncio.Queue()
        for mutant in selected:
            queue.put_nowait(mutant)

        async def worker(copy: Path) -> None:
            while not queue.empty() and time.monotonic() < deadline:
                mutant = queue.get_nowait()
                target = copy / mutant.file
                original = target.read_text()
                target.write_text(mutant.apply(original))
                try:
                    res = await _pytest(python, copy, per_mutant)
                finally:
                    target.write_text(original)
                report.tested += 1
                if res.exit_code == 0:
                    report.survivors.append(
                        Survivor(mutant.file, mutant.line,
                                 f"'{mutant.original}' -> '{mutant.replacement}'",
                                 mutant.source, mutant.mutated)
                    )
                elif res.exit_code is None:
                    report.timeouts += 1  # un bucle infinito también es un bug detectado
                else:
                    report.killed += 1

        await asyncio.gather(*(worker(copy) for copy in copies))

    report.survivors.sort(key=lambda s: (s.file, s.line))
    if report.tested < len(selected):
        report.tools.append(
            ToolRun("mutation", True, None,
                    f"tiempo agotado: probados {report.tested} de {len(selected)}")
        )
    if skipped:
        report.tools.append(ToolRun("mutation", True, None, f"no se pudieron analizar: {', '.join(skipped)}"))
    return report.finalize()


async def _pytest(python: Path, copy: Path, timeout: float) -> Result:
    # PYTHONPATH delante: así se importa la copia mutada y no el paquete
    # instalado en modo editable, que apunta al código real
    paths = [str(copy / "src"), str(copy), os.environ.get("PYTHONPATH", "")]
    return await run(
        [str(python), "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"],
        copy,
        timeout,
        env={"PYTHONPATH": os.pathsep.join(p for p in paths if p)},
    )


def _source_files(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if _SKIP_DIRS & set(rel.parts[:-1]):
            continue
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py") or name in ("conftest.py", "setup.py"):
            continue
        files.append(path)
    return files


def _sample(items: list[Mutant], limit: int) -> list[Mutant]:
    """Si hay más candidatos que el límite, coge una muestra repartida por todo el código."""
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def _candidates(path: Path, rel: str) -> list[Mutant]:
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    protected = _protected_spans(ast.parse(text), lines)
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))

    mutants = []
    for i, tok in enumerate(tokens):
        if tok.type in (tokenize.OP, tokenize.NAME) and tok.string in _SWAPS:
            replacement = _SWAPS[tok.string]
            # `*` y `/` sueltos en una firma (def f(a, *, b)) son marcadores, no operaciones
            if tok.string in ("*", "/") and tokens[i + 1].string in (",", ")"):
                continue
        elif tok.type == tokenize.NUMBER:
            bumped = _bump(tok.string)
            if bumped is None:
                continue
            replacement = bumped
        else:
            continue

        (row, col), (end_row, end_col) = tok.start, tok.end
        source_line = lines[row - 1]
        if "pragma: no mutate" in source_line or any(a <= (row, col) < b for a, b in protected):
            continue
        mutant = Mutant(
            file=rel,
            line=row,
            start=offsets[row - 1] + col,
            end=offsets[end_row - 1] + end_col,
            original=tok.string,
            replacement=replacement,
            source=source_line.strip()[:160],
            mutated="",
        )
        mutated_text = mutant.apply(text)
        try:
            compile(mutated_text, rel, "exec")
        except SyntaxError:
            continue
        line_start = offsets[row - 1]
        line_end = mutated_text.find("\n", line_start)
        mutated_line = mutated_text[line_start : line_end if line_end != -1 else None]
        mutants.append(replace(mutant, mutated=mutated_line.strip()[:160]))
    return mutants


def _protected_spans(tree: ast.AST, lines: list[str]) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Zonas que no se mutan: anotaciones de tipo (no cambian el comportamiento)."""
    nodes: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation:
            nodes.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            nodes.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            nodes.append(node.annotation)

    def char_col(row: int, byte_col: int) -> int:
        # ast mide columnas en bytes UTF-8; tokenize, en caracteres ("Descripción" ≠)
        return len(lines[row - 1].encode()[:byte_col].decode(errors="replace"))

    spans = []
    for n in nodes:
        if n.end_lineno is None or n.end_col_offset is None:
            continue
        spans.append(
            ((n.lineno, char_col(n.lineno, n.col_offset)), (n.end_lineno, char_col(n.end_lineno, n.end_col_offset)))
        )
    return spans


def _bump(number: str) -> str | None:
    try:
        return str(int(number, 0) + 1)
    except ValueError:
        pass
    try:
        return repr(float(number) + 1)
    except ValueError:
        return None  # complejos (1j) y similares: no merece la pena
