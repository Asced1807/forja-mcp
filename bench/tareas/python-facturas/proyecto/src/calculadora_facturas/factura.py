"""Cálculo de facturas con dinero exacto (Decimal) y redondeo a céntimos."""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENTIMO = Decimal("0.01")
ITBIS = Decimal(18)


class FacturaError(ValueError):
    """Datos de factura inválidos."""


def a_decimal(valor: object, campo: str) -> Decimal:
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str, Decimal)):
        raise FacturaError(f"{campo}: '{valor}' no es un número")
    try:
        # float -> str primero para no arrastrar el error binario: 0.1 -> "0.1"
        resultado = Decimal(str(valor) if isinstance(valor, float) else valor)
    except InvalidOperation:
        raise FacturaError(f"{campo}: '{valor}' no es un número") from None
    if not resultado.is_finite():
        raise FacturaError(f"{campo}: '{valor}' no es un número finito")
    return resultado


def redondear(valor: Decimal) -> Decimal:
    return valor.quantize(CENTIMO, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Linea:
    descripcion: str
    cantidad: Decimal
    precio_unitario: Decimal
    descuento_pct: Decimal = Decimal(0)
    impuesto_pct: Decimal = ITBIS

    def __post_init__(self) -> None:
        if not self.descripcion.strip():
            raise FacturaError("la descripción no puede estar vacía")
        if self.cantidad <= 0:
            raise FacturaError(f"{self.descripcion}: la cantidad debe ser mayor que 0")
        if self.precio_unitario < 0:
            raise FacturaError(f"{self.descripcion}: el precio no puede ser negativo")
        for nombre, pct in (("descuento", self.descuento_pct), ("impuesto", self.impuesto_pct)):
            if not 0 <= pct <= 100:
                raise FacturaError(f"{self.descripcion}: el {nombre} debe estar entre 0 y 100")

    @property
    def bruto(self) -> Decimal:
        return redondear(self.cantidad * self.precio_unitario)

    @property
    def descuento(self) -> Decimal:
        return redondear(self.bruto * self.impuesto_pct / 100)

    @property
    def base(self) -> Decimal:
        return self.bruto - self.descuento

    @property
    def impuesto(self) -> Decimal:
        return redondear(self.base * self.impuesto_pct / 100)

    @property
    def total(self) -> Decimal:
        return self.base + self.impuesto


@dataclass
class Factura:
    lineas: list[Linea] = field(default_factory=list)
    # Sobre la base de cada línea (tras su propio descuento) y antes del impuesto.
    descuento_global_pct: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        self.descuento_global_pct = a_decimal(self.descuento_global_pct, "descuento_global_pct")
        if not 0 <= self.descuento_global_pct <= 100:
            raise FacturaError("el descuento global debe estar entre 0 y 100")

    def descuento_global_de(self, linea: Linea) -> Decimal:
        return redondear(linea.base * self.descuento_global_pct / 100)

    def impuesto_de(self, linea: Linea) -> Decimal:
        return redondear((linea.base - self.descuento_global_de(linea)) * linea.impuesto_pct / 100)

    def agregar(
        self,
        descripcion: str,
        cantidad: object,
        precio_unitario: object,
        descuento_pct: object = 0,
        impuesto_pct: object = ITBIS,
    ) -> Linea:
        linea = Linea(
            descripcion=descripcion,
            cantidad=a_decimal(cantidad, "cantidad"),
            precio_unitario=a_decimal(precio_unitario, "precio_unitario"),
            descuento_pct=a_decimal(descuento_pct, "descuento_pct"),
            impuesto_pct=a_decimal(impuesto_pct, "impuesto_pct"),
        )
        self.lineas.append(linea)
        return linea

    @property
    def subtotal(self) -> Decimal:
        return sum((linea.bruto for linea in self.lineas), Decimal("0.00"))

    @property
    def descuentos(self) -> Decimal:
        return sum((linea.descuento for linea in self.lineas), Decimal("0.00"))

    @property
    def descuento_global(self) -> Decimal:
        return sum((self.descuento_global_de(linea) for linea in self.lineas), Decimal("0.00"))

    @property
    def base_imponible(self) -> Decimal:
        return self.subtotal - self.descuentos - self.descuento_global

    @property
    def impuestos_por_tasa(self) -> dict[Decimal, Decimal]:
        """Impuesto agrupado por tasa, como se desglosa en una factura real."""
        grupos: dict[Decimal, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for linea in self.lineas:
            grupos[linea.impuesto_pct.normalize()] += self.impuesto_de(linea)
        return dict(sorted(grupos.items()))

    @property
    def impuestos(self) -> Decimal:
        return sum(self.impuestos_por_tasa.values(), Decimal("0.00"))

    @property
    def total(self) -> Decimal:
        return self.base_imponible + self.impuestos
