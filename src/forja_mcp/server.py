"""Servidor MCP de forja: orientarse, comprobar, corregir y recordar en cualquier proyecto."""

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from forja_mcp import mapa, memoria
from forja_mcp.adapters import ADAPTERS, detect_all, find_root
from forja_mcp.adapters.base import BaseAdapter
from forja_mcp.history import History
from forja_mcp.model import (
    CheckReport,
    Diagnostic,
    TestFailure,
    TestReport,
    ToolRun,
    merge_checks,
    merge_tests,
)

server = MCPServer(
    name="forja",
    instructions=(
        "forja valida código de cualquier lenguaje y recuerda los errores de cada proyecto. "
        "Orden de uso: 1) `overview` al empezar (en vez de leer README o listar carpetas). "
        "2) `recall` para ver qué errores se repiten en este proyecto. "
        "3) `outline` para ver las funciones de un archivo con sus líneas y leer solo esas líneas. "
        "4) tras editar, `gate`. Cada respuesta trae `next`: haz lo que dice. "
        "Nunca digas que terminaste si ok=false o si una herramienta salió con ran=false. "
        "Si un error trae `memory`, ya pasó antes en este proyecto: no lo repitas. "
        "Cuando entiendas por qué ocurrió un error, guárdalo con `learn`."
    ),
)
history = History()


def _resolve(path: str) -> tuple[Path, list[BaseAdapter]]:
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise ValueError(f"la ruta no existe: {target}")
    root = find_root(target)
    adapters = detect_all(root)
    if not adapters:
        supported = ", ".join(a.name for a in ADAPTERS)
        raise ValueError(f"no reconozco ningún lenguaje en {root} (soportados: {supported}; "
                         "o declara tus herramientas en .forja.toml)")
    return root, adapters


async def _check(root: Path, adapters: list[BaseAdapter], timeout: float, fix: bool = False) -> CheckReport:
    reports = await asyncio.gather(*((a.fix if fix else a.check)(root, timeout) for a in adapters))
    report = merge_checks(str(root), list(reports))
    report.changes = history.compare(("check", str(root)), report.diagnostics)
    _remember(root, "check", report.diagnostics, report.tools, complete=True)
    return report


async def _test(
    root: Path, adapters: list[BaseAdapter], selector: str | None, timeout: float, coverage: bool
) -> TestReport:
    results = await asyncio.gather(*(a.test(root, selector, timeout, coverage) for a in adapters))
    report = merge_tests(str(root), [r for r in results if r is not None])
    report.changes = history.compare(("test", str(root), selector or ""), report.failures)
    _remember(root, "test", report.failures, report.tools, complete=selector is None)
    return report


def _remember(
    root: Path,
    kind: str,
    items: list[Diagnostic] | list[TestFailure],
    tools: list[ToolRun],
    complete: bool,
) -> None:
    """Guarda la ejecución en la memoria del proyecto y anota lo que ya pasó antes.

    Si alguna herramienta no se ejecutó, sus errores no aparecen: eso NO significa
    que se arreglaran, así que en ese caso nada se marca como resuelto.
    """
    seen: list[tuple[memoria.Visto, Diagnostic | TestFailure]] = []
    for item in items:
        if isinstance(item, Diagnostic):
            if item.severity == "info":
                continue
            v = memoria.Visto("check", item.file, item.code, item.tool, item.message, item.severity,
                              item.line, item.source)
        else:
            last = item.traceback.splitlines()[-1] if item.traceback else item.message
            v = memoria.Visto("test", item.file, item.test, "test", last or item.message, "test", item.line)
        seen.append((v, item))
    try:
        notes = memoria.registrar(root, kind, [v for v, _ in seen],
                                  complete and all(t.ran for t in tools))
    except sqlite3.Error as e:
        tools.append(ToolRun("memoria", True, None, f"no se pudo guardar el historial: {e}"))
        return
    for v, item in seen:
        item.memory = notes.get(v.clave())


def _next_check(report: CheckReport, then: str = "check") -> str | None:
    broken = [t for t in report.tools if not t.ran]
    if broken:
        t = broken[0]
        return (f"{t.name} no se pudo ejecutar ({t.note}). Arréglalo o díselo al usuario; "
                "no des la tarea por terminada")
    errors = [d for d in report.diagnostics if d.severity == "error"]
    if errors:
        d = errors[0]
        more = f" (y {len(errors) - 1} errores más)" if len(errors) > 1 else ""
        return f"corrige {d.file}:{d.line} ({d.message[:70]}){more} y vuelve a llamar a {then}"
    fixable = sum(d.fixable for d in report.diagnostics)
    if fixable:
        return f"hay {fixable} avisos que se corrigen solos: llama a fix"
    return None


def _next_test(report: TestReport) -> str | None:
    broken = [t for t in report.tools if not t.ran]
    if broken:
        t = broken[0]
        return f"{t.note or t.name + ' no se pudo ejecutar'}. No des la tarea por terminada"
    if report.failures:
        f = report.failures[0]
        where = f"{f.file}:{f.line}" if f.file else f.test
        return f"corrige {where} (falla {f.test}) y vuelve a llamar a gate"
    if report.total == 0:
        return "no se ejecutó ningún test: escribe tests para lo que cambiaste"
    return None


def _out(data: dict[str, Any], next_step: str | None) -> dict[str, Any]:
    if next_step:
        data["next"] = next_step
    return data


@server.tool()
async def overview(path: str) -> dict[str, Any]:
    """Mapa del proyecto en una llamada: lenguajes, carpetas, cómo se comprueba, puntos de
    entrada, índice de la documentación (solo títulos con su línea) y estado de git.
    Úsalo al empezar en vez de leer README o listar carpetas.

    Args:
        path: ruta absoluta al proyecto o a cualquier archivo dentro de él.
    """
    root, adapters = _resolve(path)
    tools = {a.name: a.tools(root) for a in adapters if a.tools(root)}
    data = {"root": str(root), **mapa.overview(root, tools)}
    try:
        mem = memoria.resumen(root, limite=2)
    except sqlite3.Error:
        mem = {}
    if len(mem) > 1:  # hay algo más que el contador
        data["memory"] = mem
    return _out(data, "usa outline(archivo) para ver sus funciones y lee solo las líneas que necesites")


@server.tool()
async def outline(path: str) -> dict[str, Any]:
    """Funciones y clases de un archivo, o de todos los de una carpeta, con su rango de líneas
    (p. ej. "L12-40 def calcular(x) -> int"). Lee después solo ese rango, no el archivo entero.

    Args:
        path: ruta absoluta a un archivo o carpeta del proyecto.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise ValueError(f"la ruta no existe: {target}")
    return mapa.outline(find_root(target), target)


@server.tool()
async def check(path: str, timeout: float = 120, full: bool = False) -> dict[str, Any]:
    """Comprueba el proyecto sin ejecutarlo: sintaxis, tipos y lint de TODOS sus lenguajes.

    Args:
        path: ruta absoluta al proyecto o a cualquier archivo dentro de él.
        timeout: segundos máximos por herramienta.
        full: true para recibir todos los campos, incluido el detalle de lo ya visto.
    """
    root, adapters = _resolve(path)
    report = await _check(root, adapters, timeout)
    return _out(report.to_dict(full), _next_check(report) or "sin errores: llama a gate para ejecutar también los tests")


@server.tool()
async def fix(path: str, timeout: float = 120) -> dict[str, Any]:
    """Aplica las correcciones automáticas SEGURAS y vuelve a comprobar.

    Modifica archivos: `fixed` dice qué se corrigió y dónde.
    """
    root, adapters = _resolve(path)
    report = await _check(root, adapters, timeout, fix=True)
    return _out(report.to_dict(), _next_check(report) or "sin errores: llama a gate para ejecutar también los tests")


@server.tool()
async def test(
    path: str,
    selector: str | None = None,
    coverage: bool = False,
    timeout: float = 300,
    full: bool = False,
) -> dict[str, Any]:
    """Ejecuta los tests de todos los lenguajes del proyecto y localiza cada fallo.

    Args:
        path: ruta absoluta al proyecto o a cualquier archivo dentro de él.
        selector: opcional, limita a un archivo o test concreto.
        coverage: true para ver qué líneas no ejecuta ningún test (Python).
        timeout: segundos máximos.
        full: true para recibir todos los campos, incluido el detalle de lo ya visto.
    """
    root, adapters = _resolve(path)
    report = await _test(root, adapters, selector, timeout, coverage)
    return _out(report.to_dict(full), _next_test(report) or "tests ok: llama a gate antes de dar la tarea por terminada")


@server.tool()
async def gate(path: str, coverage: bool = False, timeout: float = 300) -> dict[str, Any]:
    """check + test en una llamada: la puerta final. ok solo si TODO pasa.

    Args:
        path: ruta absoluta al proyecto o a cualquier archivo dentro de él.
        coverage: true para incluir las líneas sin cubrir por los tests.
        timeout: segundos máximos por fase.
    """
    root, adapters = _resolve(path)
    checked, tested = await asyncio.gather(
        _check(root, adapters, timeout), _test(root, adapters, None, timeout, coverage)
    )
    c, t = checked.to_dict(), tested.to_dict()
    ok = c["ok"] and t["ok"]
    step = _next_check(checked, then="gate") or _next_test(tested)
    return _out({"ok": ok, "check": c, "test": t},
                step or "todo verificado: puedes dar la tarea por terminada")


@server.tool()
async def mutate(
    path: str, file: str | None = None, max_mutants: int = 40, timeout: float = 600
) -> dict[str, Any]:
    """¿Los tests son buenos? Mete bugs pequeños en COPIAS del código y mira si los tests los
    detectan. Cada superviviente es algo que ningún test comprueba (algunos son equivalentes:
    júzgalos). Hoy solo para Python.

    Args:
        path: ruta absoluta al proyecto.
        file: opcional, solo muta este archivo (ruta relativa al proyecto).
        max_mutants: máximo de mutaciones a probar.
        timeout: segundos máximos en total.
    """
    root, adapters = _resolve(path)
    if file and not (root / file).is_file():
        raise ValueError(f"no existe {file} dentro de {root}")
    for adapter in adapters:
        result = await adapter.mutate(root, file, max_mutants, timeout)
        if result is not None:
            survivors = result.survivors
            step = None
            if survivors:
                s = survivors[0]
                step = f"añade un test que falle si {s.file}:{s.line} pasa a ser: {s.mutated}"
            return _out(result.to_dict(), step)
    raise ValueError("mutate todavía no soporta los lenguajes de este proyecto (solo Python)")


@server.tool()
async def recall(path: str, topic: str | None = None) -> dict[str, Any]:
    """Memoria del proyecto, ordenada por importancia: errores que se repiten, errores
    abiertos, patrones (el mismo tipo de error en varios sitios), lo resuelto hace poco
    y las lecciones guardadas. Se llena sola con cada check/test. Úsalo antes de cambiar
    código en un proyecto en el que ya trabajaste.

    Args:
        path: ruta absoluta al proyecto.
        topic: opcional, filtra por archivo, código de error o texto (p. ej. "cli.py").
    """
    root = find_root(Path(path).expanduser().resolve())
    return memoria.resumen(root, topic)


@server.tool()
async def learn(path: str, lesson: str, about: str | None = None) -> dict[str, Any]:
    """Guarda una lección del proyecto para no repetir un error (p. ej. "usar Decimal, nunca
    float, para dinero"). Aparecerá sola junto a ese error si vuelve a ocurrir.

    Args:
        path: ruta absoluta al proyecto.
        lesson: la lección, en una frase.
        about: opcional, a qué se aplica: código de error (F821), archivo o nombre de test.
    """
    root = find_root(Path(path).expanduser().resolve())
    memoria.aprender(root, lesson, about)
    return {"ok": True}
