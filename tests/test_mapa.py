"""overview y outline: orientarse sin leer archivos enteros."""

import asyncio
from pathlib import Path

from forja_mcp import server


def _make(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return root


def test_overview_gives_the_map_without_reading_docs(tmp_path: Path) -> None:
    root = _make(tmp_path, {
        "pyproject.toml": '[project]\nname = "x"\nversion = "0"\n[project.scripts]\nx = "x:main"\n',
        "README.md": "# Proyecto\n\n## Instalación\n```\n# no es un título\n```\n## Uso\n",
        "src/x/__init__.py": "def main() -> None:\n    pass\n",
        "install.sh": "#!/bin/bash\necho hola\n",
    })
    data = asyncio.run(server.overview(str(root)))
    assert data["docs"]["README.md"] == ["L1 # Proyecto", "L3 ## Instalación", "L7 ## Uso"]
    assert "python: x = x:main" in data["entry_points"] and "install.sh" in data["entry_points"]
    assert data["tools"]["python"]["check"] == "ruff + mypy"
    assert "shell" in data["tools"]
    assert any(line.startswith("src/:") for line in data["layout"])
    assert "outline" in data["next"]


def test_outline_python_with_line_ranges(tmp_path: Path) -> None:
    root = _make(tmp_path, {
        "pyproject.toml": "",
        "m.py": "class A:\n    def b(self, x: int) -> int:\n        return x\n\n\nasync def c() -> None:\n    pass\n",
    })
    data = asyncio.run(server.outline(str(root / "m.py")))
    assert data["m.py"] == ["L1-3 class A", "L2-3   def b(self, x: int) -> int", "L6-7 async def c() -> None"]


def test_outline_other_languages(tmp_path: Path) -> None:
    root = _make(tmp_path, {
        ".git/x": "",
        "a.php": "<?php\nclass Repo {\n  public function guardar($x) {\n  }\n}\nfunction ayuda() {}\n",
        "b.sh": "#!/bin/bash\nlimpiar() {\n  rm x\n}\nfunction instalar {\n  echo\n}\n",
        "c.lua": "local function on(ev, cb)\nend\nM.bind = function(k)\nend\n",
        "d.go": "package d\n\ntype S struct{}\n\nfunc (s *S) Run(n int) error {\n\treturn nil\n}\n",
        "e.ts": "export class C {}\nexport const f = async (x: number) => x\nexport function g(a) {}\n",
        "f.fish": "function on --description 'enciende'\n  echo\nend\n",
        "g.rs": "pub struct P;\nimpl P {\n    pub fn new() -> Self { P }\n}\n",
    })
    data = asyncio.run(server.outline(str(root)))
    assert data["a.php"] == ["L2-5 class Repo", "L3-4 guardar($x)", "L6-6 ayuda()"]
    assert data["b.sh"] == ["L2-4 limpiar()", "L5-7 instalar()"]
    assert data["c.lua"] == ["L1-2 on(ev, cb)", "L3-4 M.bind(k)"]
    assert data["d.go"] == ["L3-3 type S", "L5-7 func (s *S) Run(n int)"]
    assert data["e.ts"] == ["L1-1 class C", "L2-2 f(x: number)", "L3-3 g(a)"]
    assert data["f.fish"][0].startswith("L1-3 on(")
    assert data["g.rs"] == ["L1-1 type P", "L2-4 impl P", "L3-3 new()"]


def test_outline_python_script_without_extension(tmp_path: Path) -> None:
    root = _make(tmp_path, {".git/x": "", "herramienta": "#!/usr/bin/env python3\ndef main():\n    pass\n"})
    assert asyncio.run(server.outline(str(root / "herramienta")))["herramienta"] == ["L2-3 def main()"]


def test_outline_folder_without_functions_lists_sizes(tmp_path: Path) -> None:
    root = _make(tmp_path, {".git/x": "", "index.php": "<?php\necho 1;\necho 2;\n"})
    assert asyncio.run(server.outline(str(root))) == {"sin funciones": ["index.php: 3 líneas"]}
