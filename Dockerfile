# forja con todas sus herramientas dentro: no hace falta instalar nada en la máquina.
#
#   docker build -t forja-mcp .
#
# Como servidor MCP (el proyecto se monta en la MISMA ruta, para que las rutas cuadren):
#   docker run -i --rm --user "$(id -u):$(id -g)" -v "$PWD:$PWD" -w "$PWD" \
#     -v "$HOME/.local/share/forja:/data" forja-mcp
#
# Para desarrollar forja (los tests usan el código montado, no la copia de la imagen):
#   docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work -e PYTHONPATH=/work/src \
#     -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python forja-mcp -m pytest
ARG BASE=ubuntu:26.04
FROM ${BASE}

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git unzip xz-utils python3 \
        php-cli lua5.4 fish nodejs golang-go rustc cargo rust-clippy \
        dotnet-sdk-10.0 \
    && { [ -e /usr/bin/luac ] || ln -s /usr/bin/luac5.4 /usr/local/bin/luac; } \
    && rm -rf /var/lib/apt/lists/*

# Dafny: verificador de programas (usa Z3 por dentro). Se instala aparte de .NET.
ENV DOTNET_ROLL_FORWARD=Major DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
RUN dotnet tool install --tool-path /opt/dafny dafny --version 4.11.0 \
    && chmod -R a+rX /opt/dafny && /opt/dafny/dafny --version
# Z3 es el demostrador que usa Dafny por dentro; la herramienta de .NET no lo trae.
RUN apt-get update && apt-get install -y --no-install-recommends z3 && rm -rf /var/lib/apt/lists/*

# Lean 4 con elan (su instalador oficial), en /opt para que lo use cualquier usuario.
# Se reintenta: la descarga es grande y una conexión inestable no debe tumbar toda la imagen.
ENV ELAN_HOME=/opt/elan
RUN curl -sSfL --retry 5 -o /tmp/elan-init.sh https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    && for intento in 1 2 3; do \
         sh /tmp/elan-init.sh -y --no-modify-path --default-toolchain leanprover/lean4:stable && break; \
         echo "fallo de descarga, reintento $intento"; sleep 15; \
       done \
    && rm /tmp/elan-init.sh && chmod -R a+rX /opt/elan && /opt/elan/bin/lean --version

# Con "stable", elan pregunta a internet por la última versión CADA vez que se ejecuta lean: sin red
# (o con una mala) se cuelga. Se fija la versión instalada y se usa su binario directamente.
RUN v=$(ls /opt/elan/toolchains | sed -n 's/^leanprover--lean4---//p' | head -1) \
    && /opt/elan/bin/elan default "leanprover/lean4:$v" \
    && ln -s "/opt/elan/toolchains/leanprover--lean4---$v" /opt/lean \
    && timeout 30 /opt/lean/bin/lean --version

# uv y forja
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/forja-venv UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /opt/forja
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --python python3 && chmod -R a+rX /opt/forja-venv

# Cualquier usuario (--user) necesita un HOME escribible para las cachés de cargo, go, lake...
ENV HOME=/tmp/forja-home XDG_DATA_HOME=/data GOTOOLCHAIN=local \
    PATH=/opt/forja-venv/bin:/opt/dafny:/opt/lean/bin:/opt/elan/bin:/usr/local/bin:/usr/bin:/bin
RUN mkdir -p /tmp/forja-home /data && chmod 1777 /tmp/forja-home /data

WORKDIR /work
ENTRYPOINT ["forja-mcp"]
