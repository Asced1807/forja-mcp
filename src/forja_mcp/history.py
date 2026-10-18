"""Memoria de la llamada anterior, para informar solo de lo que cambió.

En el bucle "corrijo → vuelvo a comprobar" lo que la IA necesita saber es
qué arregló y qué es nuevo. Lo que ya vio se envía resumido (`seen=True`),
sin trazas ni pistas que ya tiene en el contexto.
"""

from collections.abc import Hashable, Sequence
from typing import Protocol

from forja_mcp.model import Changes


class _Keyed(Protocol):
    seen: bool

    def key(self) -> Hashable: ...


class History:
    def __init__(self) -> None:
        self._last: dict[tuple[str, ...], set[Hashable]] = {}

    def compare(self, scope: tuple[str, ...], items: Sequence[_Keyed]) -> Changes | None:
        """Marca `seen` en lo repetido y devuelve el resumen (None en la primera llamada)."""
        current = {item.key() for item in items}
        previous = self._last.get(scope)
        self._last[scope] = current
        if previous is None:
            return None
        for item in items:
            item.seen = item.key() in previous
        return Changes(
            fixed=len(previous - current),
            new=len(current - previous),
            seen=len(current & previous),
        )

    def forget(self, scope: tuple[str, ...]) -> None:
        self._last.pop(scope, None)
