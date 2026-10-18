"""Qué archivos tiene un proyecto, respetando .gitignore cuando hay git."""

import fnmatch
import subprocess
import time
import tomllib
from pathlib import Path

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", "build", "dist",
    "target", ".mypy_cache", ".ruff_cache", ".pytest_cache", "vendor", ".next", ".cache",
}
MAX_FILES = 20000
_TTL = 2.0  # varios adaptadores piden la lista en la misma llamada: se reutiliza
_cache: dict[Path, tuple[float, list[Path]]] = {}


def project_files(root: Path) -> list[Path]:
    hit = _cache.get(root)
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    files = _git_files(root)
    if files is None:
        files = _walk(root)
    patterns = excluded(root)
    if patterns:
        files = [f for f in files if not _matches(str(f.relative_to(root)), patterns)]
    _cache[root] = (time.monotonic(), files)
    return files


def excluded(root: Path) -> list[str]:
    """`[forja] exclude = ["bench/tareas", "*.min.js"]` en .forja.toml: código de terceros, fixtures rotos..."""
    try:
        value = tomllib.loads((root / ".forja.toml").read_text()).get("forja", {}).get("exclude", [])
    except (OSError, tomllib.TOMLDecodeError):
        return []
    return [str(v).rstrip("/") for v in value] if isinstance(value, list) else []


def _matches(rel: str, patterns: list[str]) -> bool:
    return any(rel == p or rel.startswith(p + "/") or fnmatch.fnmatch(rel, p) for p in patterns)


def files_with(root: Path, extensions: tuple[str, ...]) -> list[Path]:
    return [f for f in project_files(root) if f.suffix in extensions]


def shebang(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            first = fh.readline(200)
    except OSError:
        return ""
    return first.decode(errors="replace").strip() if first.startswith(b"#!") else ""


def _git_files(root: Path) -> list[Path] | None:
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    files = []
    for name in res.stdout.decode(errors="replace").split("\0"):
        if not name:
            continue
        path = root / name
        if path.is_file() and not SKIP_DIRS & set(Path(name).parts[:-1]):
            files.append(path)
    return files[:MAX_FILES]


def _walk(root: Path) -> list[Path]:
    files: list[Path] = []
    stack = [root]
    while stack and len(files) < MAX_FILES:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir() and not entry.is_symlink():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            elif entry.is_file():
                files.append(entry)
    return sorted(files)
