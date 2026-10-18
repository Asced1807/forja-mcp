"""Registro de adaptadores. Para añadir un lenguaje, impórtalo y añádelo a la lista."""

from pathlib import Path

from forja_mcp.adapters.base import BaseAdapter
from forja_mcp.adapters.compiled import GoAdapter, RustAdapter
from forja_mcp.adapters.custom import CustomAdapter, disabled
from forja_mcp.adapters.data import DataAdapter
from forja_mcp.adapters.python import PythonAdapter
from forja_mcp.adapters.scripting import JsAdapter, LuaAdapter, PhpAdapter
from forja_mcp.adapters.shell import FishAdapter, ShellAdapter

ADAPTERS: list[BaseAdapter] = [
    PythonAdapter(), JsAdapter(), RustAdapter(), GoAdapter(), PhpAdapter(), LuaAdapter(),
    ShellAdapter(), FishAdapter(), DataAdapter(), CustomAdapter(),
]

_ROOT_MARKERS = (
    ".git", ".forja.toml", "pyproject.toml", "setup.py", "Cargo.toml", "package.json",
    "go.mod", "composer.json",
)


def find_project(path: Path) -> Path | None:
    """La raíz del proyecto que contiene `path`, o None si no hay ninguna marca."""
    start = path if path.is_dir() else path.parent
    for candidate in (start, *start.parents):
        if any((candidate / m).exists() for m in _ROOT_MARKERS):
            return candidate
    return None


def find_root(path: Path) -> Path:
    """Como find_project, pero sin marca usa la propia carpeta."""
    return find_project(path) or (path if path.is_dir() else path.parent)


def detect_all(root: Path) -> list[BaseAdapter]:
    """Todos los lenguajes presentes. Un proyecto mixto se revisa entero, no solo una parte."""
    off = disabled(root)
    return [a for a in ADAPTERS if a.name not in off and a.detect(root)]


def detect(root: Path) -> BaseAdapter | None:
    found = detect_all(root)
    return found[0] if found else None
