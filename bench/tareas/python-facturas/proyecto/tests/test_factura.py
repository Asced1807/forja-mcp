from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from calculadora_facturas.factura import Factura, FacturaError, Linea

D = Decimal


def factura_ejemplo() -> Factura:
    f = Factura()
    f.agregar("Teclado mecánico", 2, "2500.00")                  # 5000 + 900 ITBIS
    f.agregar("Libro", 1, "800", impuesto_pct=0)                  # 800 exento
    f.agregar("Instalación", 3, "1000", descuento_pct=10)         # 3000 - 300 = 2700 + 486
    return f


def test_totales_de_la_factura() -> None:
    f = factura_ejemplo()
    assert f.subtotal == D("8800.00")
    assert f.descuentos == D("300.00")
    assert f.base_imponible == D("8500.00")
    assert f.impuestos_por_tasa == {D("0"): D("0.00"), D("18"): D("1386.00")}
    assert f.impuestos == D("1386.00")
    assert f.total == D("9886.00")


def test_linea_individual() -> None:
    linea = Factura().agregar("Instalación", 3, "1000", descuento_pct=10)
    assert (linea.bruto, linea.descuento, linea.base, linea.impuesto, linea.total) == (
        D("3000.00"), D("300.00"), D("2700.00"), D("486.00"), D("3186.00")
    )


def test_float_no_arrastra_error_binario() -> None:
    # con float puro: 3 * 0.1 = 0.30000000000000004
    assert Factura().agregar("Caramelo", 3, 0.1, impuesto_pct=0).total == D("0.30")


def test_redondeo_medio_hacia_arriba() -> None:
    assert Factura().agregar("Tornillo", 1, "0.125", impuesto_pct=0).bruto == D("0.13")
    # 0.05 * 18% = 0.009 -> 0.01
    assert Factura().agregar("Clip", 1, "0.05").impuesto == D("0.01")


def test_factura_vacia() -> None:
    f = Factura()
    assert (f.subtotal, f.impuestos, f.total) == (D("0.00"), D("0.00"), D("0.00"))


@pytest.mark.parametrize(
    ("kwargs", "mensaje"),
    [
        ({"cantidad": 0}, "cantidad"),
        ({"cantidad": -1}, "cantidad"),
        ({"precio_unitario": "-5"}, "precio"),
        ({"descuento_pct": 101}, "descuento"),
        ({"impuesto_pct": -1}, "impuesto"),
        ({"precio_unitario": "abc"}, "no es un número"),
        ({"precio_unitario": "NaN"}, "finito"),
        ({"precio_unitario": None}, "no es un número"),
        ({"cantidad": True}, "no es un número"),  # bool es int en Python: no colarlo como 1
        ({"descripcion": "   "}, "descripción"),
    ],
)
def test_datos_invalidos(kwargs: dict[str, object], mensaje: str) -> None:
    datos: dict[str, object] = {"descripcion": "Item", "cantidad": 1, "precio_unitario": "10"}
    datos.update(kwargs)
    with pytest.raises(FacturaError, match=mensaje):
        Factura().agregar(**datos)  # type: ignore[arg-type]


# Huecos que encontró `forja mutate`: límites válidos que ningún test probaba.


def test_precio_cero_es_valido() -> None:
    assert Factura().agregar("Regalo", 1, "0").total == D("0.00")


def test_descuento_del_cien_por_cien_es_valido() -> None:
    assert Factura().agregar("Cortesía", 1, "500", descuento_pct=100).total == D("0.00")


def test_linea_creada_directamente_no_tiene_descuento() -> None:
    linea = Linea("Item", D("2"), D("10"))
    assert (linea.descuento, linea.impuesto) == (D("0.00"), D("3.60"))


def test_linea_es_inmutable() -> None:
    linea = Factura().agregar("Item", 1, "10")
    with pytest.raises(FrozenInstanceError):
        linea.cantidad = D("99")  # type: ignore[misc]


# Descuento global: sobre la base de cada línea (tras su descuento) y antes del ITBIS.


def test_descuento_global_reduce_base_e_impuestos() -> None:
    f = factura_ejemplo()
    f.descuento_global_pct = D(10)
    # 10% de 5000, 800 y 2700 -> 500 + 80 + 270
    assert f.descuento_global == D("850.00")
    assert f.base_imponible == D("7650.00")
    # ITBIS 18% sobre 4500 + 2430
    assert f.impuestos_por_tasa == {D("0"): D("0.00"), D("18"): D("1247.40")}
    assert f.total == D("8897.40")


def test_descuento_global_por_defecto_es_cero() -> None:
    f = factura_ejemplo()
    assert f.descuento_global_pct == D(0)
    assert f.descuento_global == D("0.00")
    assert f.total == D("9886.00")


def test_descuento_global_se_redondea_por_linea() -> None:
    f = Factura(descuento_global_pct=10)  # type: ignore[arg-type]
    f.agregar("Clip", 1, "0.05", impuesto_pct=0)
    f.agregar("Clip", 1, "0.05", impuesto_pct=0)
    # 0.005 -> 0.01 en cada línea (no 0.01 sobre el total de 0.10)
    assert f.descuento_global == D("0.02")
    assert f.total == D("0.08")


def test_descuento_global_del_cien_por_cien_es_valido() -> None:
    f = Factura(descuento_global_pct=100)  # type: ignore[arg-type]
    f.agregar("Item", 2, "10")
    assert (f.base_imponible, f.impuestos, f.total) == (D("0.00"), D("0.00"), D("0.00"))


@pytest.mark.parametrize(
    ("pct", "mensaje"),
    [(101, "entre 0 y 100"), (-1, "entre 0 y 100"), ("abc", "descuento_global_pct"), (None, "no es un número")],
)
def test_descuento_global_invalido(pct: object, mensaje: str) -> None:
    with pytest.raises(FacturaError, match=mensaje):
        Factura(descuento_global_pct=pct)  # type: ignore[arg-type]
