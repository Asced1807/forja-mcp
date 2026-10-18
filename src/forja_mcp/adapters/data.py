"""Archivos de datos y configuración: JSON, YAML y TOML. Se revisan sin herramientas externas."""

import json
import re
import tomllib
from pathlib import Path

import yaml

from forja_mcp.adapters.base import BaseAdapter, report
from forja_mcp.model import CheckReport, Diagnostic, ToolRun

# JSON con comentarios (JSONC): son válidos para su programa aunque no sean JSON estricto
_JSONC = re.compile(r"(^|/)(tsconfig[^/]*|jsconfig|\.eslintrc|devcontainer)\.json$|\.jsonc$|(^|/)\.vscode/")
_TOML_POS = re.compile(r"\(at line (\d+), column (\d+)\)")


class DataAdapter(BaseAdapter):
    name = "data"
    extensions = (".json", ".yaml", ".yml", ".toml")

    def tools(self, root: Path) -> dict[str, str]:
        return {"check": "json/yaml/toml (interno)"}

    async def check(self, root: Path, timeout: float) -> CheckReport:
        diagnostics = []
        for path in self.files(root):
            rel = str(path.relative_to(root))
            if path.suffix == ".json" and _JSONC.search(rel):
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            found = _check(text, path.suffix, rel)
            if found:
                diagnostics.append(found)
        return report(root, self.name, (ToolRun("data", True, 0), diagnostics))


def _check(text: str, suffix: str, rel: str) -> Diagnostic | None:
    try:
        if suffix == ".json":
            json.loads(text)
        elif suffix == ".toml":
            tomllib.loads(text)
        else:
            list(yaml.safe_load_all(text))
    except json.JSONDecodeError as e:
        return Diagnostic(rel, e.lineno, e.colno, "error", "json", None, e.msg)
    except tomllib.TOMLDecodeError as e:
        pos = _TOML_POS.search(str(e))
        line, col = (int(pos[1]), int(pos[2])) if pos else (1, 1)
        return Diagnostic(rel, line, col, "error", "toml", None, _TOML_POS.sub("", str(e)).strip())
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line, col = (mark.line + 1, mark.column + 1) if mark else (1, 1)
        problem = getattr(e, "problem", None) or str(e).splitlines()[0]
        return Diagnostic(rel, line, col, "error", "yaml", None, str(problem))
    return None
