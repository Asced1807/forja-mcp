"""Línea de comandos: lee una factura en JSON y muestra el desglose."""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from calculadora_facturas.factura import Factura, FacturaError

EJEMPLO = """\
{
  "lineas": [
    {"descripcion": "Teclado mecánico", "cantidad": 2, "precio_unitario": "2500.00"},
    {"descripcion": "Libro", "cantidad": 1, "precio_unitario": "800", "impuesto_pct": 0},
    {"descripcion": "Instalación", "cantidad": 3, "precio_unitario": "1000", "descuento_pct": 10}
  ]
}"""


def cargar(datos: object) -> Factura:
    if not isinstance(datos, dict) or not isinstance(datos.get("lineas"), list):
        raise FacturaError('el JSON debe tener la forma {"lineas": [...]}')
    factura = Factura(descuento_global_pct=datos.get("descuento_global_pct", 0))
    for i, item in enumerate(datos["lineas"], start=1):
        if not isinstance(item, dict):
            raise FacturaError(f"línea {i}: debe ser un objeto")
        try:
            factura.agregar(
                descripcion=str(item["descripcion"]),
                cantidad=item["cantidad"],
                precio_unitario=item["precio_unitario"],
                descuento_pct=item.get("descuento_pct", 0),
                impuesto_pct=item.get("impuesto_pct", 18),
            )
        except KeyError as falta:
            raise FacturaError(f"línea {i}: falta el campo {falta}") from None
        except FacturaError as e:
            raise FacturaError(f"línea {i}: {e}") from None
    return factura


def dinero(valor: Decimal) -> str:
    return f"RD$ {valor:,.2f}"


def formatear(factura: Factura) -> str:
    ancho = 72
    filas = [f"{'Descripción':<26}{'Cant.':>7}{'Precio':>13}{'Desc.':>11}{'Importe':>15}", "-" * ancho]
    for linea in factura.lineas:
        filas.append(
            f"{linea.descripcion[:25]:<26}{linea.cantidad.normalize():>7}"
            f"{linea.precio_unitario:>13,.2f}{linea.descuento:>11,.2f}{linea.base:>15,.2f}"
        )
    filas.append("-" * ancho)

    def total(etiqueta: str, valor: Decimal) -> str:
        return f"{etiqueta:>50}{dinero(valor):>22}"

    filas.append(total("Subtotal:", factura.subtotal))
    if factura.descuentos:
        filas.append(total("Descuentos:", -factura.descuentos))
    if factura.descuento_global:
        pct = factura.descuento_global_pct.normalize()
        filas.append(total(f"Descuento global {pct:f}%:", -factura.descuento_global))
    filas.append(total("Base imponible:", factura.base_imponible))
    for tasa, importe in factura.impuestos_por_tasa.items():
        filas.append(total(f"ITBIS {tasa}%:", importe))
    filas.append("=" * ancho)
    filas.append(total("TOTAL:", factura.total))
    return "\n".join(filas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="facturas",
        description="Calcula el desglose de una factura a partir de un JSON.",
    )
    parser.add_argument("archivo", nargs="?", help="JSON de la factura ('-' para leer de stdin)")
    parser.add_argument("--ejemplo", action="store_true", help="muestra un JSON de ejemplo")
    args = parser.parse_args(argv)

    if args.ejemplo:
        print(EJEMPLO)
        return 0
    if not args.archivo:
        parser.print_usage(sys.stderr)
        print("facturas: indica un archivo JSON o usa --ejemplo", file=sys.stderr)
        return 2

    try:
        texto = sys.stdin.read() if args.archivo == "-" else Path(args.archivo).read_text()
        factura = cargar(json.loads(texto))
    except FileNotFoundError:
        print(f"facturas: no existe el archivo {args.archivo}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"facturas: JSON inválido (línea {e.lineno}): {e.msg}", file=sys.stderr)
        return 1
    except FacturaError as e:
        print(f"facturas: {e}", file=sys.stderr)
        return 1

    print(formatear(facura))
    return 0
