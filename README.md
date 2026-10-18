# forja-mcp

**Objetivo:** que cualquier IA —incluso un modelo pequeño y gratuito en local— trabaje
en proyectos reales y grandes sin perderse, porque forja le dice, con el mínimo de
palabras, qué hay en el proyecto, qué está roto exactamente, si su cambio funciona de
verdad y qué error ya cometió antes en ese proyecto.

| Dónde falla la IA | Respuesta de forja |
|---|---|
| Contexto limitado: lee archivos enteros y `.md` | `overview`, `outline`, `source` en cada error, respuestas compactas |
| Dice "terminado" sin comprobar | `gate`; un paso sin comprobar nunca es `ok` |
| No sabe probar un proyecto desconocido | detección multi-lenguaje + `.forja.toml` |
| Se pierde en el bucle de errores | historial `fixed/new/seen`, errores agrupados por causa, campo `next` |
| Repite errores que ya arregló | memoria SQLite por proyecto: `recall` y avisos de `repetido` |
| Tests débiles dan falsa confianza | `mutate` |
| Modelos pequeños: poco contexto, tool calling frágil | pocas herramientas, parámetros simples, salida mínima |

**Principios:** funciona con cualquier cliente MCP y modelo · cada carácter cuenta ·
nunca un falso éxito · sin internet ni `sudo` · no toca el código salvo `fix`.

**No es** un linter, una IA ni un IDE: es el traductor entre las herramientas que ya
existen y la IA.

## Herramientas

| Herramienta | Para qué |
|---|---|
| `overview(path)` | Mapa del proyecto en una llamada: lenguajes, carpetas, cómo se comprueba, puntos de entrada, índice de la documentación (solo títulos) y git. En vez de leer README y listar carpetas |
| `outline(path)` | Funciones y clases de un archivo o carpeta con su rango de líneas, para leer solo ese trozo |
| `check(path)` | Sintaxis, tipos y lint de **todos** los lenguajes del proyecto, sin ejecutarlo |
| `fix(path)` | Aplica las correcciones seguras y dice qué tocó |
| `test(path)` | Ejecuta los tests de todos los lenguajes y localiza cada fallo |
| `gate(path)` | `check` + `test`: la puerta final, `ok` solo si todo pasa |
| `mutate(path)` | ¿Los tests sirven? Mete bugs pequeños en copias y mira si los detectan (Python) |
| `recall(path)` | Memoria del proyecto: errores que se repiten, abiertos, patrones, recientes, lecciones |
| `learn(path, lesson)` | Guarda una lección; aparecerá sola junto a ese error si vuelve |

Cada respuesta trae `next`: el siguiente paso en una frase, para que un modelo pequeño no tenga que deducirlo.

## Lenguajes

| Lenguaje | check | test |
|---|---|---|
| Python | ruff + mypy | pytest (+ coverage, mutate) |
| JavaScript / TypeScript | `node --check`, tsc y eslint si el proyecto los trae | vitest, `node --test` |
| Rust | cargo clippy / check | cargo test |
| Go | go vet | go test |
| PHP | `php -l` | phpunit si está en `vendor/bin` |
| Shell (sh/bash) | shellcheck (incluido en forja) | — |
| fish | `fish --no-execute` | — |
| Lua | `luac -p` | — |
| JSON / YAML / TOML | interno | — |
| **Cualquier otro** | `.forja.toml` | `.forja.toml` |

Un proyecto con varios lenguajes se revisa **entero**. Para una herramienta que forja no conoce:

```toml
# .forja.toml
[[check]]
name = "phpstan"
cmd = ["vendor/bin/phpstan", "analyse", "--error-format=raw", "--no-progress"]  # salida archivo:línea: mensaje

[[test]]
name = "phpunit"
cmd = ["vendor/bin/phpunit", "--log-junit", "{junit}"]   # {junit}: forja lee ese XML

[forja]
disable = ["data"]   # opcional
```

## Memoria por proyecto

Cada `check`, `test`, `gate` y edición vista por el hook se guarda sola en una base SQLite **por
proyecto** (`~/.local/share/forja/`), fuera de la carpeta del proyecto. forja anota qué errores
aparecen, cuáles se arreglan y cuáles **vuelven** después de arreglados. `recall` devuelve un
resumen corto y ordenado por importancia (repetidos → críticos → patrones → recientes →
lecciones), y un error que la IA repite llega con `memory: "repetido: ya lo arreglaste…"`. Si una
herramienta no llegó a ejecutarse, nada se da por resuelto.

## Cómo ahorra contexto

- **`overview` / `outline`:** medido en proyectos reales, −84 % a −92 % frente a leer los `.md` y
  listar archivos, y −98 % frente a leer un script entero para encontrar una función.
- **Forma compacta:** sin campos vacíos; un `check` limpio es `{"ok": true, "next": ...}`.
- **`source`:** cada error trae su línea de código.
- **Duplicados agrupados** (ruff + mypy, tests con la misma causa) y **trazas limpias**.
- **Historial:** en llamadas repetidas lo ya visto llega resumido (`full=true` para todo).

## Reglas que nunca se rompen

- Una herramienta que no pudo ejecutarse aparece con `ran: false` y el resultado
  **nunca** es `ok: true`: un paso sin comprobar no cuenta como éxito.
- Un proyecto sin tests no pasa `test`: "no hay tests" no es "los tests pasan".
- forja no escribe en el proyecto (cachés en `~/.cache/forja-mcp`, memoria en
  `~/.local/share/forja`), salvo `fix`, que lo pide explícitamente.
- `mutate` trabaja en copias temporales y se niega a empezar si los tests no pasan sin mutar.

## Instalación

Requisitos: Python 3.12+ y [uv](https://docs.astral.sh/uv/). Las herramientas de cada lenguaje
(php, cargo, go, node…) se usan si están instaladas; shellcheck, ruff y mypy vienen incluidas.

```sh
git clone <este repo> forja-mcp && cd forja-mcp
uv sync
uv run pytest            # comprueba que todo funciona en tu máquina
```

El servidor es `<ruta>/forja-mcp/.venv/bin/forja-mcp` (MCP por stdio).

### Claude Code

```sh
claude mcp add --scope user forja -- <ruta>/forja-mcp/.venv/bin/forja-mcp
```

Hook opcional que revisa cada archivo editado (en `.claude/settings.json` del proyecto):

```json
{"hooks": {"PostToolUse": [{"matcher": "Edit|Write|MultiEdit",
  "hooks": [{"type": "command", "command": "<ruta>/forja-mcp/.venv/bin/forja-hook", "timeout": 90}]}]}}
```

### opencode

En `~/.config/opencode/opencode.jsonc`:

```jsonc
"mcp": {
  "forja": { "type": "local", "command": ["<ruta>/forja-mcp/.venv/bin/forja-mcp"], "enabled": true }
}
```

### Modelo local gratuito (Ollama)

Hace falta un modelo que haga *tool calling* de verdad (la familia qwen2.5-coder, por ejemplo,
devuelve la llamada como texto y no ejecuta nada). Con 4 GB de VRAM funciona `qwen3.5:4b`:

```sh
ollama pull qwen3.5:4b
ollama launch opencode --model qwen3.5:4b     # o configúralo como proveedor openai-compatible
ollama launch claude --model qwen3.5:4b       # Claude Code contra el modelo local
```

Con modelos pequeños, desactiva en el proyecto los servidores MCP que no uses: cada
herramienta extra ocupa contexto en todos los mensajes y le cuesta elegir la correcta.

## Estructura

```
src/forja_mcp/
  server.py           servidor MCP y campo `next`
  model.py            formato único, forma compacta y combinación de lenguajes
  memoria.py          memoria SQLite por proyecto
  mapa.py             overview y outline
  files.py            archivos del proyecto (respeta .gitignore)
  parsers.py          lectores compartidos (formato gcc)
  junit.py            lector de JUnit XML
  history.py          comparación con la llamada anterior
  runner.py           ejecución de comandos con tiempo límite
  hook.py             hook PostToolUse
  adapters/
    base.py           clase base y revisión archivo por archivo
    python.py, python_mutation.py
    scripting.py      PHP, Lua, JavaScript/TypeScript
    compiled.py       Rust, Go
    shell.py          sh/bash, fish
    data.py           JSON, YAML, TOML
    custom.py         .forja.toml
```

## Añadir un lenguaje

Una clase que herede de `BaseAdapter` con `name`, `extensions` y `check`; `test`, `fix` y
`mutate` son opcionales (por defecto "no aplica"). Regístrala en `ADAPTERS`
(`adapters/__init__.py`). `per_file` y `parse_gcc` resuelven la mayoría de los casos en
pocas líneas, y `junit_run` cualquier runner de tests que escriba JUnit XML.

## Licencia

Copyright (C) 2026 Asced1807

forja-mcp es software libre: puedes usarlo, estudiarlo, modificarlo y compartirlo bajo los
términos de la [GNU Affero General Public License v3.0](LICENSE) o posterior.

En resumen: cualquiera puede usarlo y mejorarlo, pero debe mantener este aviso de autoría, y si
distribuye una versión modificada **o la ofrece como servicio por la red**, tiene que publicar su
código bajo la misma licencia. Las contribuciones son bienvenidas.
