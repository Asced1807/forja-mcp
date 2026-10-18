# forja

**Una IA que programa no necesita ser más lista: necesita poder comprobar lo que hace.**

forja es un servidor [MCP](https://modelcontextprotocol.io) que se conecta a cualquier IA que
programe (Claude Code, opencode, Cline… o un modelo gratuito en tu propio PC con Ollama) y le da
lo que le falta para trabajar en proyectos reales sin perderse: saber qué hay, saber qué está
roto, saber si su cambio funciona **de verdad** y recordar los errores que ya cometió.

## Por qué existe

Las IAs que programan fallan siempre en los mismos sitios, y no por falta de inteligencia:

- **No ven el proyecto entero.** Su memoria de trabajo (el contexto) es limitada, y la gastan
  orientándose: leen el README completo, listan carpetas, abren archivos enteros para encontrar
  una función.
- **Dicen "listo, funciona" sin haberlo comprobado.** Es el fallo más caro: te enteras tú, después.
- **Repiten errores que ya arreglaron**, porque entre una sesión y otra no recuerdan nada.
- **Se fían de tests que no prueban nada**: todo verde no significa que esté bien.
- **Con modelos pequeños y gratuitos todo esto se multiplica**: menos contexto y más torpeza
  usando herramientas.

Las herramientas para comprobar código ya existen (compiladores, linters, tests, verificadores
como Dafny o Lean), pero cada una habla su propio idioma y todas están pensadas para que las lea
una persona.

## Para qué sirve

forja es el **traductor entre esas herramientas y la IA**. Le responde, en pocas palabras y
siempre con el mismo formato, sea cual sea el lenguaje:

1. **¿Qué hay en este proyecto?** — un mapa de una sola llamada, en vez de leer documentación.
2. **¿Qué está roto exactamente?** — archivo, línea, el código afectado y qué hacer a continuación.
3. **¿Mi cambio funciona de verdad?** — `ok` solo si todo se comprobó; nunca un éxito inventado.
4. **¿Ya me equivoqué en esto antes?** — una memoria por proyecto que se llena sola.
5. **¿Es correcto o solo lo parece?** — con Dafny y Lean, demostrado matemáticamente.

## Cómo funciona

```
  tú: "el reporte no funciona, arréglalo"
          │
          ▼
  ┌──────────────────┐   MCP   ┌──────────┐   php -l, pytest, cargo, go,
  │ tu IA            │ ──────► │  forja   │ ─► shellcheck, dafny, lean…
  │ (cualquiera)     │ ◄────── │          │
  └──────────────────┘         └────┬─────┘
          ▲   "corrige datos.php:12      │
          │    (falta ;) y vuelve        ▼
          │    a llamar a gate"     memoria del proyecto
          └─────────────────────    (errores, repetidos, lecciones)
```

El ciclo de trabajo de la IA con forja:

1. `overview` → entiende el proyecto sin leer archivos enteros.
2. `recall` → mira qué errores se repiten en este proyecto.
3. `outline` → encuentra la función y lee solo esas líneas.
4. edita y llama a `gate` → si algo falla, la respuesta dice exactamente qué y dónde, y su campo
   `next` dice qué hacer.
5. repite hasta que `gate` diga `ok`. Solo entonces la tarea está terminada.

## El compilador de IA: Dafny y Lean

Un compilador convierte código en un programa y se puede confiar en él porque es determinista.
Una IA no lo es: la misma petición puede dar código distinto, y a veces con errores. La
verificación formal resuelve eso:

- **el humano** escribe **qué** debe cumplirse (`requires`/`ensures` en Dafny, el enunciado de un
  `theorem` en Lean) — unas pocas líneas fáciles de revisar;
- **la IA** escribe el **cómo**;
- **el verificador** demuestra que el código lo cumple. Si no, no pasa.

Así no hace falta fiarse de la IA. forja vigila además las dos trampas que lo invalidarían:

- **demostrar a medias** (`sorry`, `admit`, `axiom` en Lean; `assume`, `{:axiom}`,
  `{:verify false}` en Dafny) → es un **error**, nunca un éxito;
- **debilitar la especificación** para que sea fácil de cumplir → el humano la bloquea con
  `forja-spec lock` y cualquier cambio posterior es un error. El bloqueo no se puede hacer desde
  MCP: la IA no puede desbloquearse sola.

## Principios

Funciona con cualquier cliente MCP y cualquier modelo · cada carácter que recibe la IA cuenta ·
nunca un falso éxito · no necesita internet ni `sudo` · no toca el código del proyecto salvo con
`fix`, cuando se pide.

**No es** un linter, ni una IA, ni un IDE: no sustituye a ninguna herramienta, las traduce.

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
| **Dafny** | `dafny verify` + trampas + especificación bloqueada | — (verificar es más fuerte que probar) |
| **Lean 4** | `lake build` / `lean` + trampas + especificación bloqueada | — |
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

### Con Docker (recomendado: no instala nada en tu máquina)

La imagen trae todas las herramientas: Python, PHP, Lua, fish, Node, Go, Rust, Dafny y Lean.

```sh
git clone https://github.com/Asced1807/forja-mcp && cd forja-mcp
docker build -t forja-mcp .
```

Como servidor MCP, el proyecto se monta **en la misma ruta** para que las rutas coincidan, y
la memoria en una carpeta tuya para que no se pierda entre sesiones:

```sh
docker run -i --rm --user "$(id -u):$(id -g)" -v "$PWD:$PWD" -w "$PWD" \
  -v "$HOME/.local/share/forja:/data" forja-mcp
```

Para desarrollar forja sin instalar nada (los tests de Dafny y Lean incluidos):

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work -e PYTHONPATH=/work/src \
  -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python forja-mcp -m pytest
```

### Sin Docker

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
