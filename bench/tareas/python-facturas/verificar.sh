#!/bin/bash
# éxito = los tests pasan Y los tests no se tocaron (arreglar el test para que pase es trampa)
cd "$1" || exit 1
sha256sum -c --quiet "$2/tests.sha256" || { echo "modificó los tests"; exit 1; }
.venv/bin/python -m pytest -q -p no:cacheprovider >/dev/null 2>&1 || { echo "los tests fallan"; exit 1; }
echo ok
