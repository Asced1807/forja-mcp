"""Especificaciones bloqueadas: el humano fija el QUÉ, la IA solo puede cambiar el CÓMO.

Un verificador (Dafny, Lean) demuestra que el código cumple su especificación. Pero si la
IA no consigue cumplirla, puede debilitarla (quitar un `ensures`, cambiar el enunciado de
un teorema) y entonces "verifica" algo que ya no es lo que se pidió. Para evitarlo:

    forja-spec lock [ruta]     # el humano fija los contratos actuales en .forja/spec.lock
    forja-spec status [ruta]   # qué contratos cambiaron desde el bloqueo

`check` marca como ERROR cualquier contrato bloqueado que cambie o desaparezca. El bloqueo
solo se hace desde esta línea de comandos, nunca desde MCP: la IA no puede desbloquearse sola.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

from forja_mcp.files import files_with

LOCK = Path(".forja") / "spec.lock"

_DAFNY_DECL = re.compile(
    r"^\s*(?:ghost\s+|static\s+|opaque\s+)*(method|function|lemma|predicate|twostate\s+lemma|constructor)\s+"
    r"(?:\{:[^}]*\}\s*)*(\w+)"
)
_DAFNY_CONTRACT = re.compile(r"^\s*(requires|ensures|reads|modifies)\b")
_LEAN_DECL = re.compile(r"^\s*(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+)?(theorem|lemma)\s+([\w.']+)")
_COMMENT = re.compile(r"//.*$|--.*$")


def contracts(root: Path) -> dict[str, dict[str, str]]:
    """{archivo: {nombre: texto normalizado del contrato}} de todos los .dfy y .lean."""
    out: dict[str, dict[str, str]] = {}
    for path in files_with(root, (".dfy", ".lean")):
        found = _dafny(path) if path.suffix == ".dfy" else _lean(path)
        if found:
            out[str(path.relative_to(root))] = found
    return out


def _dafny(path: Path) -> dict[str, str]:
    """Firma + requires/ensures/reads/modifies de cada método, función, lema o predicado."""
    lines = [_COMMENT.sub("", line) for line in path.read_text(errors="replace").splitlines()]
    found: dict[str, str] = {}
    for i, line in enumerate(lines):
        match = _DAFNY_DECL.match(line)
        if not match:
            continue
        parts = [_signature(line)]
        for nxt in lines[i + 1 : i + 40]:
            stripped = nxt.strip()
            if not stripped or stripped.startswith("decreases"):
                continue  # decreases es parte de la demostración, no del contrato
            if _DAFNY_CONTRACT.match(nxt) or _open(" ".join(parts)):
                parts.append(_signature(nxt))
            else:
                break  # empieza el cuerpo u otra declaración
        found[match.group(2)] = _normalize(" ".join(parts))
    return found


def _signature(line: str) -> str:
    """La línea sin atributos {:...} y sin el cuerpo que empiece en ella."""
    return re.sub(r"\{:[^}]*\}", "", line).split("{", 1)[0]


def _open(text: str) -> bool:
    """¿La firma sigue en la línea siguiente? (paréntesis sin cerrar)."""
    return text.count("(") > text.count(")")


def _lean(path: Path) -> dict[str, str]:
    text = re.sub(r"/-.*?-/", "", path.read_text(errors="replace"), flags=re.DOTALL)
    lines = [_COMMENT.sub("", line) for line in text.splitlines()]
    found: dict[str, str] = {}
    for i, line in enumerate(lines):
        match = _LEAN_DECL.match(line)
        if not match:
            continue
        statement = []
        for nxt in lines[i : i + 40]:
            statement.append(nxt.split(":=", 1)[0])
            if ":=" in nxt or re.search(r"\bwhere\b|\|\s*$", nxt):
                break
        found[match.group(2)] = _normalize(" ".join(statement))
    return found


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def lock(root: Path) -> int:
    data = {f: {name: _digest(t) for name, t in items.items()} for f, items in contracts(root).items()}
    path = root / LOCK
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    return sum(len(v) for v in data.values())


def violations(root: Path) -> list[tuple[str, str, str]]:
    """[(archivo, nombre, "cambiada"|"eliminada")] respecto al bloqueo. Vacío si no hay bloqueo."""
    path = root / LOCK
    if not path.exists():
        return []
    try:
        locked: dict[str, dict[str, str]] = json.loads(path.read_text())
    except (OSError, ValueError):
        return [(str(LOCK), "*", "ilegible")]
    current = contracts(root)
    out = []
    for file, items in locked.items():
        now = current.get(file, {})
        for name, digest in items.items():
            if name not in now:
                out.append((file, name, "eliminada"))
            elif _digest(now[name]) != digest:
                out.append((file, name, "cambiada"))
    return out


def line_of(root: Path, file: str, name: str) -> int:
    try:
        for n, line in enumerate((root / file).read_text(errors="replace").splitlines(), start=1):
            if re.search(rf"\b{re.escape(name)}\b", line) and (_DAFNY_DECL.match(line) or _LEAN_DECL.match(line)):
                return n
    except OSError:
        pass
    return 1


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] not in ("lock", "status"):
        print(__doc__.split("\n\n")[1] if __doc__ else "", file=sys.stderr)
        print("uso: forja-spec lock|status [ruta]", file=sys.stderr)
        sys.exit(2)
    from forja_mcp.adapters import find_root

    root = find_root(Path(args[1] if len(args) > 1 else ".").resolve())
    if args[0] == "lock":
        n = lock(root)
        print(f"{n} contratos bloqueados en {root / LOCK}")
        return
    bad = violations(root)
    if not (root / LOCK).exists():
        print("no hay bloqueo: usa forja-spec lock")
    elif not bad:
        print("todas las especificaciones bloqueadas siguen intactas")
    for file, name, kind in bad:
        print(f"{file}: {name} — {kind}")
    sys.exit(1 if bad else 0)
