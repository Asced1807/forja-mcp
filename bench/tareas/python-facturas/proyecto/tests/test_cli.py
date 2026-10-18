import json
from pathlib import Path

import pytest

from calculadora_facturas.cli import EJEMPLO, cargar, formatear, main


def test_ejemplo_es_json_valido_y_calcula(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archivo = tmp_path / "factura.json"
    archivo.write_text(EJEMPLO)

    assert main([str(archivo)]) == 0
    salida = capsys.readouterr().out
    assert "RD$ 8,800.00" in salida       # subtotal
    assert "RD$ -300.00" in salida        # descuentos
    assert "ITBIS 18%:" in salida and "RD$ 1,386.00" in salida
    assert "ITBIS 0%:" in salida
    assert salida.rstrip().endswith("RD$ 9,886.00")


def test_imprime_ejemplo(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--ejemplo"]) == 0
    assert json.loads(capsys.readouterr().out)["lineas"]


@pytest.mark.parametrize(
    ("contenido", "mensaje"),
    [
        ("{no es json", "JSON inválido"),
        ('{"items": []}', '{"lineas": [...]}'),
        ('{"lineas": [{"cantidad": 1, "precio_unitario": 5}]}', "línea 1: falta el campo 'descripcion'"),
        ('{"lineas": [{"descripcion": "X", "cantidad": 0, "precio_unitario": 5}]}', "línea 1: X: la cantidad"),
        ('{"lineas": ["texto"]}', "línea 1: debe ser un objeto"),
    ],
)
def test_errores_claros(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], contenido: str, mensaje: str
) -> None:
    archivo = tmp_path / "f.json"
    archivo.write_text(contenido)
    assert main([str(archivo)]) == 1
    assert mensaje in capsys.readouterr().err


def test_archivo_inexistente(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["/no/existe.json"]) == 1
    assert "no existe el archivo" in capsys.readouterr().err


def test_sin_argumentos(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "--ejemplo" in capsys.readouterr().err


def test_tabla_alineada_y_descripciones_largas_recortadas() -> None:
    larga = "Monitor curvo de 34 pulgadas UltraWide"
    salida = formatear(cargar({"lineas": [{"descripcion": larga, "cantidad": 1, "precio_unitario": 100}]}))
    filas = salida.splitlines()

    assert {len(f) for f in filas if set(f) <= {"-", "="}} == {len(filas[0])}  # separadores = cabecera
    assert larga[:25] in salida and larga[:26] not in salida


def test_descuento_global_en_el_json() -> None:
    datos = json.loads(EJEMPLO) | {"descuento_global_pct": "12.5"}
    salida = formatear(cargar(datos))
    assert "Descuento global 12.5%:" in salida and "RD$ -1,062.50" in salida


def test_sin_descuento_global_no_se_muestra() -> None:
    assert "Descuento global" not in formatear(cargar(json.loads(EJEMPLO)))


def test_descuento_global_invalido_en_el_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archivo = tmp_path / "f.json"
    archivo.write_text(json.dumps(json.loads(EJEMPLO) | {"descuento_global_pct": 150}))
    assert main([str(archivo)]) == 1
    assert "descuento global debe estar entre 0 y 100" in capsys.readouterr().err
