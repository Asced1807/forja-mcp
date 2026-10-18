"""Memoria por proyecto: se llena sola, detecta repeticiones y no se mezcla entre proyectos."""

import asyncio
import textwrap
from pathlib import Path
from typing import Any

from conftest import MakeProject

from forja_mcp import memoria, server

ROTO = "def total() -> int:\n    factura = 1\n    return facura\n"
BIEN = "def total() -> int:\n    factura = 1\n    return factura\n"


def _check(root: Path) -> dict[str, Any]:
    return asyncio.run(server.check(str(root)))


def test_error_that_comes_back_is_flagged_as_repeated(make_project: MakeProject) -> None:
    root = make_project({"main.py": ROTO})
    first = _check(root)
    assert "memory" not in first["diagnostics"][0]  # la primera vez no hay historia

    (root / "main.py").write_text(BIEN)
    assert _check(root)["ok"] is True  # se arregla: queda como resuelto

    (root / "main.py").write_text(ROTO)  # ...y la IA lo vuelve a cometer
    again = _check(root)
    assert "repetido" in again["diagnostics"][0]["memory"]

    mem = asyncio.run(server.recall(str(root)))
    assert any("facura" in r and "volvió 1" in r for r in mem["repetidos"])


def test_unrun_tool_never_marks_errors_as_fixed(make_project: MakeProject) -> None:
    root = make_project({"main.py": ROTO})
    _check(root)
    vistos: list[memoria.Visto] = []
    memoria.registrar(root, "check", vistos, completo=False)  # una ejecución parcial
    abiertos = asyncio.run(server.recall(str(root)))["criticos"]
    assert any("facura" in c for c in abiertos)


def test_lesson_appears_with_the_error(make_project: MakeProject) -> None:
    root = make_project({"main.py": ROTO})
    asyncio.run(server.learn(str(root), "revisa los nombres: facura no es factura", about="F821"))
    data = _check(root)
    assert "lección: revisa los nombres" in data["diagnostics"][0]["memory"]


def test_patterns_and_recent(make_project: MakeProject) -> None:
    root = make_project({f"m{i}.py": f"def f{i}() -> int:\n    return x{i}\n" for i in range(3)})
    data = _check(root)
    assert any("patrón" in (d.get("memory") or "") for d in data["diagnostics"])
    for i in range(3):
        (root / f"m{i}.py").write_text(f"def f{i}() -> int:\n    return {i}\n")
    _check(root)
    mem = asyncio.run(server.recall(str(root)))
    assert len(mem["recientes"]) == 3
    assert mem["patrones"][0].startswith("F821/name-defined: 3 veces en 3 archivos")


def test_each_project_has_its_own_memory(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    for p in (a, b):
        p.mkdir()
        (p / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
    (a / "main.py").write_text(ROTO)
    (b / "main.py").write_text(BIEN)
    _check(a)
    _check(b)
    assert memoria.db_path(a) != memoria.db_path(b)
    assert "criticos" in asyncio.run(server.recall(str(a)))
    assert "criticos" not in asyncio.run(server.recall(str(b)))


def test_recall_topic_filter(make_project: MakeProject) -> None:
    root = make_project({"main.py": ROTO, "otro.py": "def g() -> int:\n    return y\n"})
    _check(root)
    mem = asyncio.run(server.recall(str(root), topic="otro.py"))
    assert mem["criticos"] and all("otro.py" in c for c in mem["criticos"])


def test_failing_test_is_remembered(make_project: MakeProject) -> None:
    root = make_project({
        "calc.py": "def suma(a: int, b: int) -> int:\n    return a - b\n",
        "test_calc.py": textwrap.dedent("""\
            from calc import suma


            def test_suma() -> None:
                assert suma(2, 3) == 5
        """),
    })
    asyncio.run(server.test(str(root)))
    mem = asyncio.run(server.recall(str(root)))
    assert any("test_suma" in c for c in mem["criticos"])
