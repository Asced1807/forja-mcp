"""Mapa del proyecto (`overview`) y de cada archivo (`outline`) para no leer de más.

La IA gasta mucho contexto orientándose: abre README, lista carpetas, lee archivos
enteros para encontrar una función. Esto le da lo mismo en pocas líneas: qué hay,
cómo se comprueba, dónde está cada función (con sus líneas) para leer solo ese trozo.
"""

import ast
import json
import re
import subprocess
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

from forja_mcp.files import project_files, shebang

MAX_HEADINGS = 8
MAX_SYMBOLS = 150
_HEADING = re.compile(r"^(#{1,2})\s+(.+?)\s*#*$")

# (regex, tipo) por extensión. Las líneas de fin se aproximan hasta el siguiente símbolo.
_PATTERNS: dict[tuple[str, ...], list[tuple[re.Pattern[str], str]]] = {
    (".php",): [
        (re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|enum)\s+(\w+)"), "class"),
        (re.compile(r"^\s*(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+&?(\w+)\s*\(([^)]*)\)?"), "function"),
    ],
    (".sh", ".bash", ""): [
        (re.compile(r"^\s*(?:function\s+)?([\w:.-]+)\s*\(\)\s*\{?"), "function"),
        (re.compile(r"^\s*function\s+([\w:.-]+)\s*\{"), "function"),
    ],
    (".fish",): [(re.compile(r"^\s*function\s+(\S+)(.*)$"), "function")],
    (".lua",): [
        (re.compile(r"^\s*(?:local\s+)?function\s+([\w.:]+)\s*\(([^)]*)\)"), "function"),
        (re.compile(r"^\s*(?:local\s+)?([\w.]+)\s*=\s*function\s*\(([^)]*)\)"), "function"),
    ],
    (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".mts", ".cts"): [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)"), "class"),
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\(([^)]*)\)?"), "function"),
        (re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\(([^)]*)\)|\w+)\s*=>"), "function"),
        (re.compile(r"^\s*(?:export\s+)?(?:interface|type)\s+(\w+)"), "type"),
    ],
    (".rs",): [
        (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|union)\s+(\w+)"), "type"),
        (re.compile(r"^\s*impl(?:<[^>]*>)?\s+([\w:<>, ]+?)\s*\{"), "impl"),
        (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?\(([^)]*)\)?"), "function"),
    ],
    (".go",): [
        (re.compile(r"^type\s+(\w+)\s+(struct|interface)"), "type"),
        (re.compile(r"^func\s+(?:\(([^)]*)\)\s*)?(\w+)\s*\(([^)]*)\)?"), "function"),
    ],
}


def outline(root: Path, target: Path) -> dict[str, Any]:
    """Símbolos de un archivo (o de todos los de una carpeta) con sus líneas."""
    files = [target] if target.is_file() else [f for f in project_files(root) if _is_under(f, target)]
    result: dict[str, Any] = {}
    budget = MAX_SYMBOLS
    for path in files:
        symbols = _symbols(path)
        if not symbols:
            continue
        rel = str(path.relative_to(root))
        result[rel] = symbols[:budget]
        budget -= len(result[rel])
        if budget <= 0:
            result["truncated"] = "demasiados símbolos: pide outline de una carpeta o archivo concreto"
            break
    if target.is_file() and not result:
        n = _count_lines(target)
        result[str(target.relative_to(root))] = [f"sin funciones reconocibles ({n} líneas)"]
    elif not result:
        # código sin funciones (scripts, PHP procedural, configuración): al menos el tamaño de cada archivo
        sizes = [f"{f.relative_to(root)}: {_count_lines(f)} líneas" for f in files[:30]]
        result["sin funciones"] = sizes or ["la carpeta no tiene archivos del proyecto"]
    return result


def _is_under(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
    except ValueError:
        return False
    return True


def _symbols(path: Path) -> list[str]:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    interpreter = "" if path.suffix else shebang(path)  # scripts sin extensión: manda el #!
    if path.suffix in (".py", ".pyi") or "python" in interpreter:
        try:
            return _python_symbols(ast.parse(text))
        except SyntaxError as e:
            return [f"L{e.lineno} error de sintaxis: {e.msg}"]
    if not path.suffix and not re.search(r"\b(ba|da|k)?sh\b", interpreter):
        return []
    suffix = path.suffix
    patterns = next((p for exts, p in _PATTERNS.items() if suffix in exts), None)
    if patterns is None:
        return []
    lines = text.splitlines()
    found: list[tuple[int, str, str]] = []
    for n, line in enumerate(lines, start=1):
        for pattern, kind in patterns:
            match = pattern.match(line)
            if match:
                found.append((n, _describe(kind, match, path.suffix), kind))
                break
    out = []
    for i, (start, desc, kind) in enumerate(found):
        limit = found[i + 1][0] - 1 if i + 1 < len(found) else len(lines)
        end = _block_end(lines, start, suffix)
        if end is None:
            end = limit  # sin un final claro se acota hasta el siguiente símbolo
        elif kind not in _CONTAINERS:
            end = min(end, limit)  # una función nunca se extiende sobre la siguiente
        out.append(f"L{start}-{end} {desc}")
    return out


_CONTAINERS = {"class", "impl", "type"}  # contienen otros símbolos: su final no lo marca el siguiente


_STRING = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|#.*$|//.*$")


def _block_end(lines: list[str], start: int, suffix: str) -> int | None:
    """Línea donde termina el bloque que empieza en `start` (1-based), o None si no se sabe.

    Sin esto, el cuerpo principal de un script se atribuiría a su última función.
    """
    if suffix in (".lua", ".fish"):
        indent = len(lines[start - 1]) - len(lines[start - 1].lstrip())
        if re.search(r"\bend\s*$", lines[start - 1]):
            return start  # función de una línea
        for n in range(start, len(lines)):
            line = lines[n]
            closes = line.strip() in ("end", "end,") or line.strip().startswith("end)")
            if closes and len(line) - len(line.lstrip()) == indent:
                return n + 1
        return None
    depth, opened = 0, False
    for n in range(start - 1, len(lines)):
        code = _STRING.sub("", lines[n]) if suffix != ".php" else re.sub(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|//.*$", "", lines[n])
        for ch in code:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if opened and depth <= 0:
                    return n + 1
        if not opened and (n >= start or code.rstrip().endswith(";")):
            return None  # la llave debe abrir en su línea o en la siguiente; `struct P;` ya terminó
    return None


def _describe(kind: str, match: re.Match[str], suffix: str) -> str:
    groups = [g for g in match.groups() if g is not None]
    if suffix == ".go" and kind == "function":
        receiver, name, args = match.group(1), match.group(2), match.group(3) or ""
        return f"func {'(' + receiver + ') ' if receiver else ''}{name}({args.strip()})"
    name = groups[0].strip() if groups else "?"
    args = groups[1].strip() if len(groups) > 1 else ""
    if kind in ("function",):
        return f"{name}({args[:80]})"
    return f"{kind} {name}"


def _python_symbols(tree: ast.Module) -> list[str]:
    out: list[str] = []

    def visit(nodes: list[ast.stmt], prefix: str) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = ast.unparse(node.args)[:100]
                returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
                kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                out.append(f"L{node.lineno}-{node.end_lineno} {prefix}{kind} {node.name}({args}){returns}")
            elif isinstance(node, ast.ClassDef):
                out.append(f"L{node.lineno}-{node.end_lineno} {prefix}class {node.name}")
                visit(node.body, prefix + "  ")

    visit(tree.body, "")
    return out


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def overview(root: Path, tools: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Todo lo necesario para orientarse sin abrir ningún archivo."""
    files = project_files(root)
    by_ext = Counter(f.suffix or "(sin extensión)" for f in files)
    data: dict[str, Any] = {
        "files": len(files),
        "languages": dict(by_ext.most_common(8)),
        "tools": tools,
        "layout": _layout(root, files),
    }
    entry = _entry_points(root, files)
    if entry:
        data["entry_points"] = entry
    docs = _docs(root, files)
    if docs:
        data["docs"] = docs
    git = _git(root)
    if git:
        data["git"] = git
    return data


def _layout(root: Path, files: list[Path]) -> list[str]:
    groups: dict[str, Counter[str]] = {}
    for f in files:
        parts = f.relative_to(root).parts
        key = parts[0] + "/" if len(parts) > 1 else "."
        groups.setdefault(key, Counter())[f.suffix or "otros"] += 1
    out = []
    for key in sorted(groups, key=lambda k: (k != ".", k)):
        counts = groups[key]
        detail = ", ".join(f"{n} {ext}" for ext, n in counts.most_common(3))
        label = "raíz" if key == "." else key
        out.append(f"{label}: {sum(counts.values())} ({detail})")
    return out[:25]


def _entry_points(root: Path, files: list[Path]) -> list[str]:
    out: list[str] = []
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text())
        out += [f"python: {k} = {v}" for k, v in project.get("project", {}).get("scripts", {}).items()]
    except (OSError, tomllib.TOMLDecodeError):
        pass
    try:
        package = json.loads((root / "package.json").read_text())
        out += [f"npm run {k}: {v}"[:120] for k, v in list(package.get("scripts", {}).items())[:8]]
    except (OSError, ValueError, AttributeError):
        pass
    if (root / "Makefile").exists():
        targets = re.findall(r"^([\w.-]+):", (root / "Makefile").read_text(errors="replace"), re.MULTILINE)
        out += [f"make {t}" for t in targets[:8] if not t.startswith(".")]
    for f in files:
        rel = f.relative_to(root)
        top_level = len(rel.parts) == 1 and (
            f.name in ("index.php", "main.py", "index.js")
            or (f.suffix == ".sh" and f.stem in ("install", "setup", "run", "build"))
        )
        if top_level or f.name in ("main.rs", "main.go"):  # main.rs/main.go cuentan a cualquier nivel
            out.append(str(rel))
    return out[:15]


def _docs(root: Path, files: list[Path]) -> dict[str, list[str]]:
    """Índice de la documentación: solo títulos. Se lee la sección que haga falta, no el archivo."""
    out: dict[str, list[str]] = {}
    for f in files:
        if f.suffix.lower() not in (".md", ".rst", ".txt") or f.stat().st_size > 500_000:
            continue
        if f.suffix.lower() == ".txt" and "readme" not in f.name.lower():
            continue
        headings = []
        in_code = False
        for n, line in enumerate(f.read_text(errors="replace").splitlines(), start=1):
            if line.startswith("```"):
                in_code = not in_code
            match = None if in_code else _HEADING.match(line)
            if match:
                headings.append(f"L{n} {match[1]} {match[2][:70]}")
            if len(headings) >= MAX_HEADINGS:
                break
        out[str(f.relative_to(root))] = headings or [f"{_count_lines(f)} líneas, sin títulos"]
        if len(out) >= 10:
            break
    return out


def _git(root: Path) -> list[str] | None:
    def git(*args: str) -> str | None:
        try:
            res = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                 timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return res.stdout.strip() if res.returncode == 0 else None

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    status = git("status", "--porcelain")
    if branch is None and status is None:
        return None
    last = git("log", "-1", "--format=%h %s (%cr)")
    out = [f"rama {branch or '?'}", f"{len(status.splitlines()) if status else 0} archivos con cambios sin commit"]
    if last:
        out.append(f"último commit: {last[:100]}")
    return out
