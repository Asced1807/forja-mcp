"""Memoria por proyecto en SQLite: forja aprende de los errores sin que la IA guarde nada.

Cada check/test registra qué problemas aparecen, cuáles desaparecen (resueltos) y
cuáles VUELVEN después de resueltos. La IA consulta un resumen corto y ordenado
(`recall`) en vez de cargar todo el historial en su contexto, y cuando repite un
error el propio aviso se lo recuerda.

Cada proyecto tiene su propia base: lo que se aprende en uno no contamina a otro.
"""

import hashlib
import os
import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS problema (
    id INTEGER PRIMARY KEY,
    clave TEXT UNIQUE NOT NULL,
    tipo TEXT NOT NULL,              -- check | test
    archivo TEXT, codigo TEXT, herramienta TEXT, mensaje TEXT NOT NULL,
    gravedad TEXT NOT NULL,          -- error | warning | info | test
    estado TEXT NOT NULL,            -- abierto | resuelto
    linea INTEGER, fuente TEXT,
    veces_visto INTEGER NOT NULL DEFAULT 1,
    veces_resuelto INTEGER NOT NULL DEFAULT 0,
    veces_reaparecido INTEGER NOT NULL DEFAULT 0,
    primera_vez TEXT NOT NULL, ultima_vez TEXT NOT NULL, resuelto_en TEXT
);
CREATE TABLE IF NOT EXISTS evento (
    id INTEGER PRIMARY KEY,
    problema_id INTEGER NOT NULL REFERENCES problema(id),
    tipo TEXT NOT NULL,              -- aparece | resuelto | reaparece
    cuando TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leccion (
    id INTEGER PRIMARY KEY,
    cuando TEXT NOT NULL,
    texto TEXT NOT NULL,
    sobre TEXT                       -- código de error, archivo o test al que aplica
);
CREATE INDEX IF NOT EXISTS problema_estado ON problema(estado, tipo);
CREATE INDEX IF NOT EXISTS problema_codigo ON problema(codigo);
"""
_NUMBERS = re.compile(r"\d+(\.\d+)?")
MAX_TEXT = 160


@dataclass
class Visto:
    """Un problema de la ejecución actual, en la forma que la memoria entiende."""

    tipo: str
    archivo: str | None
    codigo: str | None
    herramienta: str
    mensaje: str
    gravedad: str
    linea: int | None = None
    fuente: str | None = None

    def clave(self) -> str:
        # los números cambian de una ejecución a otra (valores, líneas): no definen el problema
        base = "|".join([self.tipo, self.archivo or "", self.codigo or "", _NUMBERS.sub("#", self.mensaje)[:120]])
        return hashlib.sha1(base.encode()).hexdigest()[:16]


def db_path(root: Path) -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "forja"
    slug = re.sub(r"[^\w.-]", "_", root.name)[:40] or "proyecto"
    digest = hashlib.sha1(str(root).encode()).hexdigest()[:8]
    return base / f"{slug}-{digest}.db"


@contextmanager
def conectar(root: Path) -> Iterator[sqlite3.Connection]:
    path = db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")  # el hook y el servidor pueden escribir a la vez
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def _ahora() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def registrar(root: Path, tipo: str, vistos: Iterable[Visto], completo: bool) -> dict[str, str]:
    """Anota una ejecución. Devuelve {clave: nota} para reforzar los errores que la IA repite.

    `completo=False` (p. ej. tests filtrados con selector) solo añade: lo que no se ejecutó
    no se puede dar por resuelto.
    """
    ahora = _ahora()
    notas: dict[str, str] = {}
    actuales = {v.clave(): v for v in vistos}
    with conectar(root) as con:
        for clave, v in actuales.items():
            fila = con.execute("SELECT * FROM problema WHERE clave = ?", (clave,)).fetchone()
            if fila is None:
                cur = con.execute(
                    "INSERT INTO problema (clave, tipo, archivo, codigo, herramienta, mensaje, gravedad,"
                    " estado, linea, fuente, primera_vez, ultima_vez) VALUES (?,?,?,?,?,?,?,'abierto',?,?,?,?)",
                    (clave, v.tipo, v.archivo, v.codigo, v.herramienta, v.mensaje[:MAX_TEXT], v.gravedad,
                     v.linea, (v.fuente or "")[:MAX_TEXT] or None, ahora, ahora),
                )
                _evento(con, cur.lastrowid, "aparece", ahora)
            elif fila["estado"] == "resuelto":
                con.execute(
                    "UPDATE problema SET estado='abierto', veces_visto=veces_visto+1,"
                    " veces_reaparecido=veces_reaparecido+1, ultima_vez=?, linea=?, fuente=? WHERE id=?",
                    (ahora, v.linea, (v.fuente or "")[:MAX_TEXT] or None, fila["id"]),
                )
                _evento(con, fila["id"], "reaparece", ahora)
                notas[clave] = f"repetido: ya lo arreglaste {fila['veces_resuelto']} vez/veces y volvió"
            else:
                con.execute("UPDATE problema SET veces_visto=veces_visto+1, ultima_vez=?, linea=? WHERE id=?",
                            (ahora, v.linea, fila["id"]))
            patron = _patron(con, v)
            if patron and clave not in notas:
                notas[clave] = patron
            leccion = _leccion(con, v)
            if leccion:
                notas[clave] = f"{notas[clave]} · " if clave in notas else ""
                notas[clave] += f"lección: {leccion}"

        if completo:
            abiertos = con.execute("SELECT id, clave FROM problema WHERE estado='abierto' AND tipo=?", (tipo,))
            for fila in abiertos.fetchall():
                if fila["clave"] not in actuales:
                    con.execute("UPDATE problema SET estado='resuelto', veces_resuelto=veces_resuelto+1,"
                                " resuelto_en=? WHERE id=?", (ahora, fila["id"]))
                    _evento(con, fila["id"], "resuelto", ahora)
    return notas


def _evento(con: sqlite3.Connection, problema_id: int | None, tipo: str, cuando: str) -> None:
    con.execute("INSERT INTO evento (problema_id, tipo, cuando) VALUES (?,?,?)", (problema_id, tipo, cuando))


def _patron(con: sqlite3.Connection, v: Visto) -> str | None:
    if not v.codigo:
        return None
    fila = con.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT archivo) AS a FROM problema WHERE codigo=? AND tipo=?",
        (v.codigo, v.tipo),
    ).fetchone()
    # un patrón es el MISMO tipo de error en varios archivos; varios en un mismo archivo es solo uno
    if fila["n"] < 3 or fila["a"] < 2:
        return None
    return f"patrón: {fila['n']} errores {v.codigo} en {fila['a']} archivos de este proyecto"


def _leccion(con: sqlite3.Connection, v: Visto) -> str | None:
    # un código combinado ("F821/name-defined") debe encontrar lecciones de cualquiera de sus partes
    codigos = [*(v.codigo.split("/") if v.codigo else []), v.codigo]
    claves = [x for x in (*codigos, v.archivo, v.mensaje[:60]) if x]
    if not claves:
        return None
    fila = con.execute(
        f"SELECT texto FROM leccion WHERE sobre IN ({','.join('?' * len(claves))}) ORDER BY id DESC LIMIT 1",
        claves,
    ).fetchone()
    return str(fila["texto"]) if fila else None


def aprender(root: Path, texto: str, sobre: str | None) -> int:
    with conectar(root) as con:
        cur = con.execute("INSERT INTO leccion (cuando, texto, sobre) VALUES (?,?,?)",
                          (_ahora(), texto.strip()[:300], sobre))
        return cur.lastrowid or 0


def resumen(root: Path, tema: str | None = None, limite: int = 5) -> dict[str, list[str]]:
    """Lo importante primero: repetidos, críticos, patrones, recientes y lecciones. Corto a propósito."""
    filtro, args = "", []
    if tema:
        filtro = " AND (archivo LIKE ? OR codigo LIKE ? OR mensaje LIKE ?)"
        args = [f"%{tema}%"] * 3

    def linea(f: sqlite3.Row) -> str:
        donde = f"{f['archivo']}:{f['linea']}" if f["archivo"] else f["herramienta"]
        codigo = f" [{f['codigo']}]" if f["codigo"] else ""
        return f"{donde}{codigo} {f['mensaje']}"

    with conectar(root) as con:
        def q(sql: str, extra: list[object] | None = None) -> list[sqlite3.Row]:
            return con.execute(sql, [*args, *(extra or []), limite]).fetchall()

        repetidos = [
            f"{linea(f)} — volvió {f['veces_reaparecido']} vez/veces ({f['estado']})"
            for f in q("SELECT * FROM problema WHERE veces_reaparecido > 0" + filtro
                       + " ORDER BY veces_reaparecido DESC, ultima_vez DESC LIMIT ?")
        ]
        criticos = [
            linea(f) for f in q("SELECT * FROM problema WHERE estado='abierto' AND gravedad IN ('error','test')"
                                + filtro + " ORDER BY veces_visto DESC, ultima_vez DESC LIMIT ?")
        ]
        patrones = [
            f"{f['codigo']}: {f['n']} veces en {f['a']} archivos (p. ej. {f['ejemplo']})"
            for f in q("SELECT codigo, COUNT(*) n, COUNT(DISTINCT archivo) a, MIN(mensaje) ejemplo"
                       " FROM problema WHERE codigo IS NOT NULL" + filtro
                       + " GROUP BY codigo HAVING n >= 2 ORDER BY n DESC LIMIT ?")
        ]
        recientes = [
            f"{linea(f)} (resuelto {f['resuelto_en']})"
            for f in q("SELECT * FROM problema WHERE estado='resuelto'" + filtro
                       + " ORDER BY resuelto_en DESC LIMIT ?")
        ]
        lec_args: list[object] = [f"%{tema}%", f"%{tema}%"] if tema else []
        lecciones = [
            f"{f['texto']}" + (f" (sobre {f['sobre']})" if f["sobre"] else "")
            for f in con.execute(
                "SELECT * FROM leccion" + (" WHERE texto LIKE ? OR sobre LIKE ?" if tema else "")
                + " ORDER BY id DESC LIMIT ?", [*lec_args, limite]).fetchall()
        ]
        total = con.execute("SELECT COUNT(*) n, SUM(estado='resuelto') r FROM problema").fetchone()

    out = {"repetidos": repetidos, "criticos": criticos, "patrones": patrones,
           "recientes": recientes, "lecciones": lecciones}
    out = {k: v for k, v in out.items() if v}
    out["historial"] = [f"{total['n'] or 0} problemas vistos, {total['r'] or 0} resueltos"]
    return out
