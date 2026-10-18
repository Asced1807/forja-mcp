#!/bin/bash
# éxito = todos los .php sin errores de sintaxis y el reporte da el total correcto (25*2 + 10*3 + 70*1 = 150)
cd "$1" || exit 1
for f in *.php; do php -l "$f" >/dev/null 2>&1 || { echo "error de sintaxis en $f"; exit 1; }; done
out=$(php reporte.php 2>&1)
[ "$out" = "$(printf 'Productos: 3\nTotal: 150.00')" ] || { echo "salida incorrecta: $out"; exit 1; }
echo ok
