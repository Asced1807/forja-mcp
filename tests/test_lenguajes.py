"""Cada lenguaje con su herramienta real, sobre proyectos rotos a propósito."""

import asyncio
import shutil
import textwrap
from pathlib import Path

import pytest

from forja_mcp import server
from forja_mcp.adapters import detect_all


def _make(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body))
    return root


def _errors(data: dict[str, object]) -> set[tuple[str, int, str]]:
    diags = data.get("diagnostics", [])
    assert isinstance(diags, list)
    return {(d["file"], d["line"], d["tool"]) for d in diags if d["severity"] == "error"}


def needs(tool: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(shutil.which(tool) is None, reason=f"{tool} no está instalado")


def test_mixed_project_checks_every_language(tmp_path: Path) -> None:
    """El fallo que motivó todo esto: antes solo se revisaba UN lenguaje y daba ok."""
    root = _make(tmp_path, {
        ".forja.toml": "",
        "lib/util.py": "def f() -> int:\n    return 1\n",
        "install.sh": "#!/bin/bash\nif [ -f x ]; then\n  echo hola\n",  # roto
        "config.json": '{"a": 1,}',  # roto
    })
    names = {a.name for a in detect_all(root)}
    assert {"python", "shell", "data"} <= names
    data = asyncio.run(server.check(str(root)))
    assert data["ok"] is False
    files = {f for f, _, _ in _errors(data)}
    assert {"install.sh", "config.json"} <= files


def test_shell_uses_bundled_shellcheck(tmp_path: Path) -> None:
    root = _make(tmp_path, {".git/x": "", "run.sh": "#!/bin/bash\nrm -rf $DIR/*\n", "deploy": "#!/bin/sh\nif then\n"})
    data = asyncio.run(server.check(str(root)))
    diags = {(d["file"], d["code"]) for d in data["diagnostics"]}
    assert ("run.sh", "SC2115") in diags  # rm -rf $DIR/* sin comprobar que DIR exista
    assert any(f == "deploy" for f, _ in diags)  # script sin extensión detectado por su #!


@needs("fish")
def test_fish_syntax(tmp_path: Path) -> None:
    root = _make(tmp_path, {".git/x": "", "f.fish": "function hola\n  echo hi\n", "ok.fish": "echo bien\n"})
    assert _errors(asyncio.run(server.check(str(root)))) == {("f.fish", 1, "fish")}


@needs("php")
def test_php_syntax(tmp_path: Path) -> None:
    root = _make(tmp_path, {".git/x": "", "index.php": "<?php\nfunction f() {\n  return 1\n}\n", "ok.php": "<?php echo 1;\n"})
    assert _errors(asyncio.run(server.check(str(root)))) == {("index.php", 4, "php -l")}


@needs("luac")
def test_lua_syntax(tmp_path: Path) -> None:
    root = _make(tmp_path, {".git/x": "", "init.lua": "local x = \nfunction f(\n"})
    assert _errors(asyncio.run(server.check(str(root)))) == {("init.lua", 2, "luac")}


@needs("node")
def test_js_syntax(tmp_path: Path) -> None:
    root = _make(tmp_path, {"package.json": "{}", "app.js": "function f( {\n  return 1;\n}\n"})
    assert _errors(asyncio.run(server.check(str(root)))) == {("app.js", 2, "node --check")}


def test_typescript_without_tsc_is_not_ok(tmp_path: Path) -> None:
    root = _make(tmp_path, {"package.json": "{}", "a.ts": "const x: number = 1;\n"})
    data = asyncio.run(server.check(str(root)))
    assert data["ok"] is False  # hay TS sin comprobar: nunca un falso éxito
    assert "tsc" in data["next"]


def test_data_files(tmp_path: Path) -> None:
    root = _make(tmp_path, {
        ".git/x": "",
        "a.json": '{\n  "x": 1\n  "y": 2\n}',
        "b.yaml": "clave: [1, 2\n",
        "c.toml": "x = \n",
        "tsconfig.json": '{ // comentario válido en JSONC\n}',
    })
    errors = _errors(asyncio.run(server.check(str(root))))
    assert {f for f, _, _ in errors} == {"a.json", "b.yaml", "c.toml"}
    assert ("a.json", 3, "json") in errors


@needs("go")
def test_go_vet_and_test(tmp_path: Path) -> None:
    root = _make(tmp_path, {
        "go.mod": "module demo\n\ngo 1.22\n",
        "demo.go": "package demo\n\nfunc Suma(a, b int) int {\n\treturn a - b\n}\n",
        "demo_test.go": 'package demo\n\nimport "testing"\n\nfunc TestSuma(t *testing.T) {\n'
                        '\tif Suma(2, 3) != 5 {\n\t\tt.Errorf("mal")\n\t}\n}\n',
    })
    assert asyncio.run(server.check(str(root)))["ok"] is True
    data = asyncio.run(server.test(str(root)))
    assert (data["total"], data["failed"]) == (1, 1)
    assert (data["failures"][0]["file"], data["failures"][0]["line"]) == ("demo_test.go", 7)


@needs("cargo")
def test_rust_check_and_test(tmp_path: Path) -> None:
    root = _make(tmp_path, {
        "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/lib.rs": "pub fn suma(a: i32, b: i32) -> i32 {\n    a - b\n}\n\n#[cfg(test)]\nmod tests {\n"
                      "    #[test]\n    fn suma_ok() {\n        assert_eq!(super::suma(2, 3), 5);\n    }\n}\n",
    })
    data = asyncio.run(server.test(str(root), timeout=600))
    assert (data["total"], data["failed"]) == (1, 1)
    f = data["failures"][0]
    assert (f["test"], f["file"], f["line"]) == ("tests::suma_ok", "src/lib.rs", 9)

    (root / "src/lib.rs").write_text("pub fn suma(a: i32) -> i32 {\n    a + \"x\"\n}\n")
    errors = _errors(asyncio.run(server.check(str(root), timeout=600)))
    assert ("src/lib.rs", 2, "cargo") in errors


def test_custom_tool_from_forja_toml(tmp_path: Path) -> None:
    root = _make(tmp_path, {
        ".forja.toml": textwrap.dedent('''\
            [[check]]
            name = "mi-linter"
            cmd = ["sh", "-c", "echo 'src/x.txt:3:5: error: palabra prohibida' ; exit 1"]

            [[test]]
            name = "mis-tests"
            cmd = ["sh", "-c", "echo fallo >&2; exit 1"]
        '''),
    })
    data = asyncio.run(server.check(str(root)))
    assert _errors(data) == {("src/x.txt", 3, "mi-linter")}
    tested = asyncio.run(server.test(str(root)))
    assert tested["ok"] is False and tested["failed"] == 1


def test_broken_forja_toml_is_reported(tmp_path: Path) -> None:
    root = _make(tmp_path, {".forja.toml": "[[check]\nname = \n"})
    data = asyncio.run(server.check(str(root)))
    assert data["ok"] is False
    assert "TOML" in data["next"]


def test_forja_toml_can_disable_an_adapter(tmp_path: Path) -> None:
    root = _make(tmp_path, {".forja.toml": '[forja]\ndisable = ["data"]\n', "roto.json": "{"})
    assert "data" not in {a.name for a in detect_all(root)}


def test_forja_toml_exclude(tmp_path: Path) -> None:
    root = _make(tmp_path, {".forja.toml": '[forja]\nexclude = ["fixtures", "*.min.js"]\n',
                            "fixtures/roto.json": "{", "a.min.js": "function(", "ok.json": "{}"})
    assert asyncio.run(server.check(str(root)))["ok"] is True


def test_exclude_reaches_python_tools(tmp_path: Path) -> None:
    root = _make(tmp_path, {
        ".forja.toml": '[forja]\nexclude = ["bench"]\n', "pyproject.toml": "",
        "bench/roto.py": "def f() -> int:\n    return facura\n",
        "bench/test_roto.py": "def test_x() -> None:\n    assert False\n",
        "ok.py": "def g() -> int:\n    return 1\n",
    })
    assert asyncio.run(server.check(str(root)))["ok"] is True
    assert asyncio.run(server.test(str(root)))["ok"] is False  # sin tests fuera de bench: no es éxito
