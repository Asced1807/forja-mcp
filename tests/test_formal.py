"""El compilador de IA: Dafny y Lean, sus trampas y la especificación bloqueada."""

import asyncio
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from forja_mcp import server, spec

MAXIMO_OK = """\
    method Maximo(a: array<int>) returns (m: int)
      requires a.Length > 0
      ensures forall i :: 0 <= i < a.Length ==> a[i] <= m
      ensures exists i :: 0 <= i < a.Length && a[i] == m
    {
      m := a[0];
      var k := 1;
      while k < a.Length
        invariant 1 <= k <= a.Length
        invariant forall i :: 0 <= i < k ==> a[i] <= m
        invariant exists i :: 0 <= i < k && a[i] == m
      {
        if a[k] > m { m := a[k]; }
        k := k + 1;
      }
    }
"""
# el bug clásico: se salta el último elemento
MAXIMO_ROTO = MAXIMO_OK.replace("while k < a.Length\n", "while k < a.Length - 1\n").replace(
    "invariant 1 <= k <= a.Length\n", "invariant 1 <= k <= a.Length - 1 || a.Length == 1\n")

LONGITUD_OK = """\
    def longitud : List α → Nat
      | []      => 0
      | _ :: xs => 1 + longitud xs

    theorem longitud_concat (xs ys : List α) :
        longitud (xs ++ ys) = longitud xs + longitud ys := by
      induction xs with
      | nil => simp [longitud]
      | cons x xs ih => simp [longitud, ih, Nat.add_assoc]
"""


def _make(root: Path, files: dict[str, str]) -> Path:
    (root / ".git").mkdir(exist_ok=True)
    for name, body in files.items():
        (root / name).write_text(textwrap.dedent(body))
    return root


def _codes(data: dict[str, object]) -> set[tuple[str, int, str | None]]:
    diags = data.get("diagnostics", [])
    assert isinstance(diags, list)
    return {(d["file"], d["line"], d.get("code")) for d in diags if d["severity"] == "error"}


def needs(tool: str) -> pytest.MarkDecorator:
    found = shutil.which(tool) or (Path.home() / ".elan/bin" / tool).exists()
    return pytest.mark.skipif(not found, reason=f"{tool} no está instalado (se prueba en la imagen Docker)")


# ── trampas: no necesitan el verificador ─────────────────────────────────


def test_dafny_cheats_are_errors(tmp_path: Path) -> None:
    root = _make(tmp_path, {"t.dfy": """\
        lemma Facil(x: int) ensures x > 0 { assume x > 0; }
        lemma {:axiom} Gratis(x: int) ensures x == 1
        method {:verify false} SinMirar() { }
        // assume en un comentario no cuenta
        method Texto() { var s := "assume"; }
    """})
    data = asyncio.run(server.check(str(root)))
    assert data["ok"] is False
    trampas = {(f, line) for f, line, code in _codes(data) if code == "trampa"}
    assert trampas == {("t.dfy", 1), ("t.dfy", 2), ("t.dfy", 3)}


def test_lean_cheats_are_errors(tmp_path: Path) -> None:
    root = _make(tmp_path, {"t.lean": """\
        theorem a : 1 = 1 := by sorry
        theorem b : 2 = 2 := by admit
        axiom c : 3 = 4
        /- sorry dentro de un comentario de bloque
           no cuenta -/
        -- sorry tampoco aquí
        def s := "sorry"
    """})
    data = asyncio.run(server.check(str(root)))
    trampas = {(f, line) for f, line, code in _codes(data) if code == "trampa"}
    assert trampas == {("t.lean", 1), ("t.lean", 2), ("t.lean", 3)}


# ── especificación bloqueada ─────────────────────────────────────────────


def test_weakening_a_locked_spec_is_an_error(tmp_path: Path) -> None:
    root = _make(tmp_path, {"m.dfy": MAXIMO_OK, "t.lean": LONGITUD_OK})
    assert spec.lock(root) == 2  # Maximo + longitud_concat
    assert spec.violations(root) == []

    # la IA no consigue demostrarlo y "arregla" la especificación quitando un ensures
    (root / "m.dfy").write_text(textwrap.dedent(MAXIMO_OK).replace(
        "  ensures exists i :: 0 <= i < a.Length && a[i] == m\n", ""))
    (root / "t.lean").write_text(textwrap.dedent(LONGITUD_OK).replace(
        "longitud xs + longitud ys", "longitud xs + longitud ys + 0"))
    assert set(spec.violations(root)) == {("m.dfy", "Maximo", "cambiada"), ("t.lean", "longitud_concat", "cambiada")}

    data = asyncio.run(server.check(str(root)))
    specs = {(f, line) for f, line, code in _codes(data) if code == "spec"}
    assert specs == {("m.dfy", 1), ("t.lean", 5)}


def test_changing_only_the_proof_is_allowed(tmp_path: Path) -> None:
    root = _make(tmp_path, {"m.dfy": MAXIMO_OK})
    spec.lock(root)
    # cambiar el cuerpo y los invariantes (el CÓMO) es justo lo que la IA debe poder hacer
    body = textwrap.dedent(MAXIMO_OK).replace("if a[k] > m { m := a[k]; }", "if m < a[k] { m := a[k]; }")
    (root / "m.dfy").write_text(body)
    assert spec.violations(root) == []


def test_removing_a_locked_lemma_is_an_error(tmp_path: Path) -> None:
    root = _make(tmp_path, {"t.lean": LONGITUD_OK})
    spec.lock(root)
    (root / "t.lean").write_text("def nada := 1\n")
    assert spec.violations(root) == [("t.lean", "longitud_concat", "eliminada")]


def test_lock_is_not_available_over_mcp() -> None:
    """La IA no debe poder desbloquearse sola: no hay ninguna herramienta MCP que bloquee."""
    names = {name for name in dir(server) if not name.startswith("_")}
    assert not {"lock", "spec_lock", "lock_spec"} & names


def test_spec_cli(tmp_path: Path) -> None:
    root = _make(tmp_path, {"m.dfy": MAXIMO_OK})
    exe = Path(sys.executable).parent / "forja-spec"
    lock = subprocess.run([str(exe), "lock", str(root)], capture_output=True, text=True, check=False)
    assert lock.returncode == 0 and "1 contratos bloqueados" in lock.stdout
    (root / "m.dfy").write_text(textwrap.dedent(MAXIMO_OK).replace("requires a.Length > 0", "requires true"))
    status = subprocess.run([str(exe), "status", str(root)], capture_output=True, text=True, check=False)
    assert status.returncode == 1 and "Maximo — cambiada" in status.stdout


# ── con el verificador de verdad ─────────────────────────────────────────


@needs("dafny")
def test_dafny_proves_and_rejects(tmp_path: Path) -> None:
    root = _make(tmp_path, {"m.dfy": MAXIMO_OK})
    assert asyncio.run(server.check(str(root), timeout=300))["ok"] is True

    (root / "m.dfy").write_text(textwrap.dedent(MAXIMO_ROTO))
    data = asyncio.run(server.check(str(root), timeout=300))
    assert data["ok"] is False
    errors = [d for d in data["diagnostics"] if d["tool"] == "dafny" and d["severity"] == "error"]
    assert errors, data
    assert errors[0]["file"] == "m.dfy"


@needs("lean")
def test_lean_proves_and_shows_the_goal(tmp_path: Path) -> None:
    root = _make(tmp_path, {"t.lean": LONGITUD_OK})
    assert asyncio.run(server.check(str(root), timeout=300))["ok"] is True

    # una demostración incompleta: Lean dice qué falta demostrar
    (root / "t.lean").write_text(textwrap.dedent(LONGITUD_OK).replace(
        "| cons x xs ih => simp [longitud, ih, Nat.add_assoc]", "| cons x xs ih => simp [longitud]"))
    data = asyncio.run(server.check(str(root), timeout=300))
    assert data["ok"] is False
    error = next(d for d in data["diagnostics"] if d["tool"] == "lean" and d["severity"] == "error")
    assert error["file"] == "t.lean"
    assert error.get("hint"), "el objetivo pendiente debe llegar a la IA"
