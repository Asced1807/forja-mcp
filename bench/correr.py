"""Banco de pruebas: la misma tarea con y sin forja, con un modelo local en opencode.

Mide lo que importa del objetivo de forja:
  - resuelto:   ¿la tarea quedó bien de verdad? (lo decide verificar.sh, no el modelo)
  - mintió:     ¿dijo que estaba arreglado sin estarlo? (el fallo "terminado sin comprobar")
  - tokens:     cuánto contexto gastó
  - pasos y herramientas usadas

Cada ejecución trabaja en una COPIA temporal de la tarea: nada real se toca.

    uv run python bench/correr.py --modelo ollama/qwen3.5-forja:4b [--tareas php-crud] [--repeticiones 1]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BENCH = Path(__file__).parent
FORJA = BENCH.parent / ".venv" / "bin" / "forja-mcp"
SHELLCHECK = BENCH.parent / ".venv" / "bin" / "shellcheck"
# los MCP del usuario se apagan en las dos condiciones: solo cambia forja
OTROS_MCP = ["blender", "libreoffice-writer", "linkedin-mcp", "playwright", "playwright-brave"]
DICE_ARREGLADO = re.compile(r"\b(arreglad[oa]s?|solucionad[oa]s?|corregid[oa]s?|funciona(?:n)?|listo|resuelt[oa]s?)\b", re.IGNORECASE)
NIEGA = re.compile(r"\b(no (está|esta|he podido|pude|funciona)|todavía no|aún no|sigue fallando)\b", re.IGNORECASE)


def configurar(workdir: Path, con_forja: bool) -> None:
    mcp: dict[str, object] = {n: {"type": "local", "command": ["true"], "enabled": False} for n in OTROS_MCP}
    mcp["forja"] = {"type": "local", "command": [str(FORJA)], "enabled": con_forja}
    permisos_bash = {
        "*": "deny",  # el modelo trabaja sin supervisión: solo comandos de lectura y comprobación
        "ls*": "allow", "cat *": "allow", "head *": "allow", "tail *": "allow", "grep *": "allow",
        "find *": "allow", "wc *": "allow", "php *": "allow", "bash -n *": "allow", "shellcheck *": "allow",
        "DESTINO=* bash instalar.sh*": "allow", "bash instalar.sh*": "allow", "./instalar.sh*": "allow",
        ".venv/bin/python -m pytest*": "allow", ".venv/bin/pytest*": "allow", "uv run pytest*": "allow",
        "python -m pytest*": "allow", "python3 -m pytest*": "allow", "pytest*": "allow",
    }
    config = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {"edit": "allow", "bash": permisos_bash, "webfetch": "deny"},
        "mcp": mcp,
    }
    (workdir / "opencode.json").write_text(json.dumps(config, indent=1))


def preparar(tarea: Path) -> Path:
    workdir = Path(tempfile.mkdtemp(prefix=f"bench-{tarea.name}-"))
    shutil.copytree(tarea / "proyecto", workdir, dirs_exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)  # marca de proyecto para forja
    if (workdir / "pyproject.toml").exists():
        subprocess.run(["uv", "sync", "-q"], cwd=workdir, check=True)
    return workdir


def ejecutar(modelo: str, tarea: Path, con_forja: bool, timeout: int) -> dict[str, object]:
    workdir = preparar(tarea)
    configurar(workdir, con_forja)
    prompt = (tarea / "tarea.txt").read_text().strip()
    inicio = time.monotonic()
    try:
        res = subprocess.run(
            ["opencode", "run", "-m", modelo, "--format", "json", prompt],
            cwd=workdir, capture_output=True, text=True, timeout=timeout, check=False,
            env={**os.environ, "XDG_DATA_HOME": str(workdir / ".bench-xdg")},  # memoria de forja aislada
        )
        salida, agotado = res.stdout, False
    except subprocess.TimeoutExpired as e:
        salida, agotado = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), True
    segundos = round(time.monotonic() - inicio)

    tokens_in = tokens_out = pasos = 0
    max_contexto = 0
    herramientas: dict[str, int] = {}
    textos: list[str] = []
    for linea in salida.splitlines():
        try:
            ev = json.loads(linea)
        except json.JSONDecodeError:
            continue
        part = ev.get("part", {})
        if ev.get("type") == "step_finish":
            pasos += 1
            t = part.get("tokens", {})
            tokens_in += t.get("input", 0)
            tokens_out += t.get("output", 0)
            max_contexto = max(max_contexto, t.get("input", 0) + (t.get("cache", {}) or {}).get("read", 0))
        elif ev.get("type") == "text":
            textos.append(part.get("text", ""))
        elif "tool" in str(ev.get("type")):
            nombre = str(part.get("tool") or part.get("name") or "?")
            herramientas[nombre] = herramientas.get(nombre, 0) + 1

    env = {**os.environ, "SHELLCHECK": str(SHELLCHECK)}
    v = subprocess.run(["bash", str(tarea / "verificar.sh"), str(workdir), str(tarea)],
                       capture_output=True, text=True, env=env, check=False)
    resuelto = v.returncode == 0
    final = textos[-1] if textos else ""
    dice_que_si = bool(DICE_ARREGLADO.search(final)) and not NIEGA.search(final)
    resultado = {
        "tarea": tarea.name, "forja": con_forja, "modelo": modelo,
        "resuelto": resuelto, "verificacion": v.stdout.strip() or v.stderr.strip()[-200:],
        "dijo_arreglado": dice_que_si, "mintio": dice_que_si and not resuelto,
        "tokens_entrada": tokens_in, "tokens_salida": tokens_out, "contexto_max": max_contexto,
        "pasos": pasos, "herramientas": herramientas, "segundos": segundos, "timeout": agotado,
        "respuesta_final": final[-300:], "carpeta": str(workdir),
    }
    return resultado


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modelo", required=True)
    parser.add_argument("--tareas", nargs="*", help="por defecto todas")
    parser.add_argument("--repeticiones", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--salida", default=str(BENCH / "resultados.jsonl"))
    args = parser.parse_args()

    tareas = sorted(p for p in (BENCH / "tareas").iterdir() if p.is_dir())
    if args.tareas:
        tareas = [t for t in tareas if t.name in args.tareas]
    with open(args.salida, "a") as out:
        for _ in range(args.repeticiones):
            for tarea in tareas:
                for con_forja in (False, True):
                    r = ejecutar(args.modelo, tarea, con_forja, args.timeout)
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                    out.flush()
                    marca = "✅" if r["resuelto"] else ("🤥" if r["mintio"] else "❌")
                    print(f"{marca} {r['tarea']:18} forja={'sí' if con_forja else 'no'}  "
                          f"tokens={r['tokens_entrada']:>7}  pasos={r['pasos']:>3}  {r['segundos']:>4}s  "
                          f"{r['verificacion']}", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
