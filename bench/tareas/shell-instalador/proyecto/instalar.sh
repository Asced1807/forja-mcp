#!/bin/bash
# Copia los archivos de config/ a $DESTINO (por defecto ./salida) y dice cuántos copió.
set -u

DESTINO=${DESTINO:-./salida}

limpiar() {
  rm -rf $DESTINO/*
}

copiar() {
  local n=0
  for f in config/*.conf; do
    cp "$f" "$DESTINO/"
    n=$((n + 1))
  done
  echo "copiados: $((n - 1))"
}

if [ ! -d "$DESTINO" ]; then
  mkdir -p "$DESTINO"

limpiar
copiar
