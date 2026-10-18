"""fix, cobertura, mutation testing y hook de edición."""

import asyncio
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import MakeProject

from forja_mcp import hook, server
from forja_mcp.adapters.python import _changed_lines, _ranges

CALC = """\
    def suma(a: int, b: int) -> int:
        return a + b


    def es_mayor_de_edad(edad: int) -> bool:
        return edad >= 18
    """

# test débil: no prueba el límite de 18, así que '>=' -> '>' sobrevive
WEAK_TESTS = """\
    from calc import es_mayor_de_edad, suma


    def test_suma() -> None:
        assert suma(2, 3) == 5


    def test_mayor() -> None:
        assert es_mayor_de_edad(30)
        assert not es_mayor_de_edad(5)
    """


# ── fix ──────────────────────────────────────────────────────────────────


def test_fix_applies_safe_fixes_and_says_where(make_project: MakeProject) -> None:
    root = make_project({"a.py": "import os\n\n\ndef f() -> int:\n    return 1\n"})
    data = asyncio.run(server.fix(str(root)))

    assert data["ok"] is True
    assert data["fixed"] == ["a.py:1 F401 `os` imported but unused"]
    assert "import os" not in (root / "a.py").read_text()


def test_fix_without_fixable_changes_nothing(make_project: MakeProject) -> None:
    root = make_project({"a.py": "def f() -> int:\n    return 1\n"})
    before = (root / "a.py").read_text()
    data = asyncio.run(server.fix(str(root)))
    assert data["ok"] is True and "fixed" not in data
    assert "gate" in data["next"]  # siempre dice el siguiente paso, también cuando todo va bien
    assert (root / "a.py").read_text() == before


# ── cobertura ────────────────────────────────────────────────────────────


def test_ranges() -> None:
    assert _ranges([41, 42, 43, 57]) == "41-43, 57"
    assert _ranges([7]) == "7"


def test_coverage_reports_uncovered_lines(make_project: MakeProject) -> None:
    root = make_project({"calc.py": CALC, "test_calc.py": "from calc import suma\n\n\ndef test_suma() -> None:\n    assert suma(1, 1) == 2\n"})
    data = asyncio.run(server.test(str(root), coverage=True))

    assert data["ok"] is True
    assert data["coverage"]["scope"] == "all"  # sin git: todo el código
    assert data["coverage"]["uncovered"] == {"calc.py": "6"}  # el cuerpo de es_mayor_de_edad


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_coverage_only_changed_lines_with_git(make_project: MakeProject) -> None:
    root = make_project({"calc.py": CALC, "test_calc.py": "def test_nada() -> None:\n    pass\n"})
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "base", "--allow-empty")
    _git(root, "add", ".")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "codigo")

    # solo se cambia suma: es_mayor_de_edad (también sin cubrir) no debe aparecer
    (root / "calc.py").write_text(CALC.replace("    ", "", 1).replace("return a + b", "return a + b + 0").replace("\n    ", "\n"))
    changed = asyncio.run(_changed_lines(root))
    assert changed == {"calc.py": {2}}

    data = asyncio.run(server.test(str(root), coverage=True))
    assert data["coverage"]["scope"] == "changed"
    assert data["coverage"]["uncovered"] == {"calc.py": "2"}


# ── mutation testing ─────────────────────────────────────────────────────


def test_mutate_finds_the_weak_test(make_project: MakeProject) -> None:
    root = make_project({"calc.py": CALC, "test_calc.py": WEAK_TESTS})
    original = (root / "calc.py").read_text()
    data = asyncio.run(server.mutate(str(root)))

    assert data["ok"] is False
    survivors = {(s["line"], s["mutation"]) for s in data["survivors"]}
    assert (6, "'>=' -> '>'") in survivors  # el límite de 18 nadie lo prueba
    assert any(s["mutated"] == "return edad > 18" for s in data["survivors"])
    assert (2, "'+' -> '-'") not in survivors  # suma sí está bien probada
    assert data["killed"] >= 1
    assert (root / "calc.py").read_text() == original  # el código real no se tocó


def test_mutate_with_strong_tests_is_ok(make_project: MakeProject) -> None:
    strong = textwrap.dedent(WEAK_TESTS) + "\n\ndef test_limite() -> None:\n    assert es_mayor_de_edad(18)\n    assert not es_mayor_de_edad(17)\n"
    root = make_project({"calc.py": CALC, "test_calc.py": strong})
    data = asyncio.run(server.mutate(str(root)))
    assert data["ok"] is True, data.get("survivors", data)
    assert data["score_pct"] == 100.0


def test_mutate_refuses_when_tests_already_fail(make_project: MakeProject) -> None:
    root = make_project({"calc.py": CALC, "test_calc.py": "def test_x() -> None:\n    assert False\n"})
    data = asyncio.run(server.mutate(str(root)))
    assert data["ok"] is False
    assert "no pasan sin mutar" in data["tools"][0]["note"]


def test_mutate_skips_type_annotations(make_project: MakeProject) -> None:
    root = make_project({"calc.py": "def f(x: int = 1) -> list[int]:\n    return [x]\n", "test_calc.py": "def test_x() -> None:\n    pass\n"})
    data = asyncio.run(server.mutate(str(root)))
    # el único candidato es el valor por defecto 1 -> 2, nada de las anotaciones
    assert data["candidates"] == 1


# ── hook ─────────────────────────────────────────────────────────────────


def _event(path: Path) -> dict[str, object]:
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path)}}


def test_hook_is_silent_when_file_is_clean(make_project: MakeProject) -> None:
    root = make_project({"a.py": "def f() -> int:\n    return 1\n"})
    assert hook.review(_event(root / "a.py")) is None


def test_hook_reports_only_the_edited_file(make_project: MakeProject) -> None:
    root = make_project({
        "a.py": "def f() -> int:\n    return facura\n",
        "b.py": "def g() -> str:\n    return 1\n",  # otro archivo roto: no debe aparecer
    })
    message = hook.review(_event(root / "a.py"))
    assert message is not None
    assert "a.py" in message and "facura" in message and "código: return facura" in message
    assert "b.py" not in message


@pytest.mark.parametrize("name", ["notas.md", "fuera_de_proyecto.py"])
def test_hook_ignores_irrelevant_files(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_text("return facura\n")
    assert hook.review(_event(path)) is None  # sin marca de proyecto o no es Python


def test_hook_exit_codes(make_project: MakeProject) -> None:
    root = make_project({"a.py": "x: int = 'texto'\n"})
    exe = Path(sys.executable).parent / "forja-hook"
    broken = subprocess.run([str(exe)], input=json.dumps(_event(root / "a.py")), capture_output=True, text=True, check=False)
    assert broken.returncode == 2 and "assignment" in broken.stderr

    garbage = subprocess.run([str(exe)], input="esto no es json", capture_output=True, text=True, check=False)
    assert garbage.returncode == 0  # un hook roto nunca bloquea la edición
