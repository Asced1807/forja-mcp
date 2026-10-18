#!/bin/bash
# éxito = sintaxis válida, shellcheck limpio y el script hace lo que debe al ejecutarlo
cd "$1" || exit 1
bash -n instalar.sh 2>/dev/null || { echo "error de sintaxis"; exit 1; }
SC=${SHELLCHECK:-shellcheck}
"$SC" -S warning instalar.sh >/dev/null 2>&1 || { echo "shellcheck encuentra problemas"; exit 1; }
dest=$(mktemp -d); echo basura > "$dest/viejo.txt"
out=$(DESTINO="$dest" bash instalar.sh 2>&1)
[ "$out" = "copiados: 2" ] || { echo "salida incorrecta: $out"; rm -rf "$dest"; exit 1; }
[ -f "$dest/apariencia.conf" ] && [ -f "$dest/general.conf" ] && [ ! -f "$dest/viejo.txt" ] || { echo "no copió bien"; rm -rf "$dest"; exit 1; }
rm -rf "$dest"; echo ok
