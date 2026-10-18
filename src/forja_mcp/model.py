"""Formato único de resultados, igual para todos los lenguajes.

La IA solo tiene que entender estas estructuras: da igual si el fallo
viene de ruff, mypy, pytest o (más adelante) cargo o tsc.

`to_dict()` devuelve la forma COMPACTA: sin campos vacíos, sin lo que ya
se vio en la llamada anterior y sin detalles de herramientas que fueron
bien. Cada carácter que se ahorra aquí es contexto que la IA no gasta.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]

# Límite de elementos por respuesta: más no ayuda a la IA y gasta contexto.
MAX_ITEMS = 100
MAX_SOURCE_CHARS = 160
_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Diagnostic:
    file: str
    line: int
    column: int
    severity: Severity
    tool: str
    code: str | None
    message: str
    hint: str | None = None
    fixable: bool = False
    source: str | None = None  # la línea de código señalada, para no tener que abrir el archivo
    seen: bool = False  # ya aparecía en la llamada anterior: se envía resumido
    memory: str | None = None  # lo que la memoria del proyecto sabe de este error (repetido, lección...)

    def key(self) -> tuple[str, str | None, str]:
        # sin la línea: si edito más arriba el error se desplaza, pero es el mismo
        return (self.file, self.code, self.message)


@dataclass
class TestFailure:
    __test__ = False  # para que pytest no la confunda con una clase de tests

    test: str
    kind: Literal["failure", "error"]
    message: str
    file: str | None = None
    line: int | None = None
    traceback: str = ""
    seen: bool = False
    # otros tests que fallan exactamente por la misma causa (misma línea, mismo error)
    same_cause: list[str] = field(default_factory=list)
    memory: str | None = None

    def key(self) -> tuple[str, str]:
        return (self.test, self.message or self.traceback[-200:])

    def cause(self) -> tuple[str | None, int | None, str]:
        last = self.traceback.splitlines()[-1] if self.traceback else self.message
        return (self.file, self.line, last)


@dataclass
class ToolRun:
    """Qué herramienta se ejecutó y cómo terminó.

    `ran=False` significa que el paso NO se comprobó: nunca debe contarse
    como éxito.
    """

    name: str
    ran: bool
    exit_code: int | None = None
    note: str | None = None


@dataclass
class Changes:
    """Comparación con la llamada anterior sobre el mismo proyecto."""

    fixed: int = 0
    new: int = 0
    seen: int = 0


@dataclass
class CheckReport:
    project: str
    adapter: str
    ok: bool = False
    errors: int = 0
    warnings: int = 0
    diagnostics: list[Diagnostic] = field(default_factory=list)
    truncated: int = 0
    tools: list[ToolRun] = field(default_factory=list)
    changes: Changes | None = None
    fixed: list[str] = field(default_factory=list)  # lo que `fix` corrigió solo

    def finalize(self) -> "CheckReport":
        self.diagnostics = _merge_duplicates(self.diagnostics)
        self.diagnostics.sort(key=lambda d: (_ORDER[d.severity], d.file, d.line))
        self.errors = sum(d.severity == "error" for d in self.diagnostics)
        self.warnings = sum(d.severity == "warning" for d in self.diagnostics)
        self.ok = self.errors == 0 and all(t.ran for t in self.tools)
        if len(self.diagnostics) > MAX_ITEMS:
            self.truncated = len(self.diagnostics) - MAX_ITEMS
            self.diagnostics = self.diagnostics[:MAX_ITEMS]
        _attach_source(self.diagnostics, Path(self.project))
        return self

    def to_dict(self, full: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if full:
            return data
        data["diagnostics"] = [
            _brief(d, ("hint", "source", "fixable", "column")) if d["seen"] else d
            for d in data["diagnostics"]
        ]
        return _compact(data)


def _merge_duplicates(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    """Une el mismo fallo visto por varias herramientas (p. ej. ruff F821 + mypy name-defined).

    Solo se unen diagnósticos de herramientas DISTINTAS en la misma posición:
    dos errores de la misma herramienta en el mismo sitio son dos problemas.
    El mensaje de la otra herramienta no se pierde: pasa a `hint`.
    """
    merged: list[Diagnostic] = []
    for diag in diagnostics:
        target = next(
            (
                m
                for m in merged
                if (m.file, m.line, m.column) == (diag.file, diag.line, diag.column)
                and diag.tool not in m.tool.split("+")
            ),
            None,
        )
        if target is None:
            merged.append(diag)
            continue
        target.tool = f"{target.tool}+{diag.tool}"
        target.code = "/".join(c for c in (target.code, diag.code) if c) or None
        if _ORDER[diag.severity] < _ORDER[target.severity]:
            target.severity = diag.severity
        target.fixable = target.fixable or diag.fixable
        extra = [f"{diag.tool}: {diag.message}"] + ([diag.hint] if diag.hint else [])
        target.hint = "\n".join(([target.hint] if target.hint else []) + extra)
    return merged


def _attach_source(diagnostics: list[Diagnostic], root: Path) -> None:
    cache: dict[str, list[str]] = {}
    for diag in diagnostics:
        if diag.file not in cache:
            try:
                cache[diag.file] = (root / diag.file).read_text(errors="replace").splitlines()
            except OSError:
                cache[diag.file] = []
        lines = cache[diag.file]
        if 0 < diag.line <= len(lines):
            diag.source = lines[diag.line - 1].strip()[:MAX_SOURCE_CHARS] or None


@dataclass
class Coverage:
    percent: float
    # líneas sin cubrir por archivo, en rangos: {"src/x.py": "41-43, 57"}
    uncovered: dict[str, str] = field(default_factory=dict)
    scope: Literal["changed", "all"] = "all"


@dataclass
class TestReport:
    __test__ = False

    project: str
    adapter: str
    ok: bool = False
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    failures: list[TestFailure] = field(default_factory=list)
    truncated: int = 0
    tools: list[ToolRun] = field(default_factory=list)
    output_tail: str | None = None
    coverage: Coverage | None = None
    changes: Changes | None = None

    def finalize(self) -> "TestReport":
        self.ok = (
            self.total > 0
            and self.failed == 0
            and self.errors == 0
            and all(t.ran for t in self.tools)
        )
        self.failures = _group_by_cause(self.failures)
        if len(self.failures) > MAX_ITEMS:
            self.truncated = len(self.failures) - MAX_ITEMS
            self.failures = self.failures[:MAX_ITEMS]
        return self

    def to_dict(self, full: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if full:
            return data
        data["failures"] = [
            _brief(f, ("traceback",)) if f["seen"] else f for f in data["failures"]
        ]
        if data["duration_s"] < 5:
            data["duration_s"] = None  # solo interesa si los tests son lentos
        return _compact(data)


def _group_by_cause(failures: list[TestFailure]) -> list[TestFailure]:
    """Varios tests que caen en la misma línea con el mismo error son UN bug: una traza basta.

    Los contadores (failed/errors) no cambian: siguen contando tests.
    """
    groups: dict[tuple[str | None, int | None, str], TestFailure] = {}
    for failure in failures:
        cause = failure.cause()
        if failure.line is None or cause not in groups:
            groups[cause if failure.line is not None else (failure.test, None, "")] = failure
        else:
            groups[cause].same_cause.append(failure.test)
    return list(groups.values())


@dataclass
class Survivor:
    """Mutación que ningún test detectó: señal de un test débil (o de un mutante equivalente)."""

    file: str
    line: int
    mutation: str  # p. ej. "'+' -> '-'"
    source: str
    mutated: str  # la línea tal como quedó: inequívoco si el operador aparece dos veces


@dataclass
class MutationReport:
    project: str
    adapter: str
    ok: bool = False
    candidates: int = 0  # mutaciones posibles en total
    tested: int = 0
    killed: int = 0
    survived: int = 0
    timeouts: int = 0
    score_pct: float = 0.0
    survivors: list[Survivor] = field(default_factory=list)
    tools: list[ToolRun] = field(default_factory=list)
    output_tail: str | None = None

    def finalize(self) -> "MutationReport":
        self.survived = len(self.survivors)
        caught = self.killed + self.timeouts
        self.score_pct = round(100 * caught / self.tested, 1) if self.tested else 0.0
        self.ok = self.tested > 0 and self.survived == 0 and all(t.ran for t in self.tools)
        return self

    def to_dict(self, full: bool = False) -> dict[str, Any]:
        data = asdict(self)
        return data if full else _compact(data)


def _brief(item: dict[str, Any], drop: tuple[str, ...]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in drop}


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    """Quita lo que no aporta: vacíos, ceros, falsos y herramientas que fueron bien.

    `ok` se conserva siempre, aunque sea false. Una herramienta con
    `ran=false` también: es justo lo que la IA no debe pasar por alto.
    """
    tools = [t for t in data.get("tools", []) if not t["ran"] or t["note"]]
    data["tools"] = [{k: v for k, v in t.items() if k != "exit_code" or not t["ran"]} for t in tools]
    data.pop("project", None)
    data.pop("adapter", None)
    stripped: dict[str, Any] = _strip(data)
    return {"ok": data["ok"]} | stripped


# Contadores que en 0 no dicen nada. Otros ceros SÍ informan y se quedan:
# total=0 (no hay tests), percent=0.0 (nada cubierto), killed=0...
_ZERO_IS_NOISE = {"errors", "warnings", "failed", "skipped", "truncated", "timeouts",
                  "survived", "fixed", "new", "seen"}


def _is_noise(key: str, value: Any) -> bool:
    # ojo: en Python 0 == False, por eso se compara por identidad y por tipo
    if value is None or value is False:
        return True
    if isinstance(value, (str, list, dict)):
        return not value
    return isinstance(value, (int, float)) and value == 0 and key in _ZERO_IS_NOISE


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        out = {k: _strip(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if not _is_noise(k, v)}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


NO_TESTS = (
    "este proyecto no tiene tests que forja sepa ejecutar: añade tests "
    "o decláralos en .forja.toml ([[test]] cmd = [...])"
)


def merge_checks(root: str, reports: list[CheckReport]) -> CheckReport:
    """Un solo informe para un proyecto con varios lenguajes."""
    merged = CheckReport(project=root, adapter="+".join(r.adapter for r in reports))
    for r in reports:
        merged.diagnostics += r.diagnostics
        merged.tools += r.tools
        merged.fixed += r.fixed
        merged.truncated += r.truncated
    return merged.finalize()


def merge_tests(root: str, reports: list[TestReport]) -> TestReport:
    """Suma los tests de todos los lenguajes. Sin ningún runner: NO es un éxito."""
    merged = TestReport(project=root, adapter="+".join(r.adapter for r in reports))
    if not reports:
        merged.tools.append(ToolRun("tests", False, None, NO_TESTS))
    for r in reports:
        merged.total += r.total
        merged.passed += r.passed
        merged.failed += r.failed
        merged.errors += r.errors
        merged.skipped += r.skipped
        merged.duration_s = round(merged.duration_s + r.duration_s, 3)
        merged.failures += r.failures
        merged.tools += r.tools
        merged.truncated += r.truncated
        merged.coverage = merged.coverage or r.coverage
        if r.output_tail:
            merged.output_tail = "\n".join(filter(None, [merged.output_tail, r.output_tail]))[-3000:]
    return merged.finalize()
