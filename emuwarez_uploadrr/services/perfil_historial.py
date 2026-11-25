"""Historial de tu cuenta en cada tracker: una instantánea al día, para dibujar su evolución en Estadísticas.

Sale de lo que ya trae la lectura del perfil (emu_perfil, cada hora con la tarea «Leer tu cuenta»): sin
llamadas nuevas al tracker. El día es el local del servidor (TZ del contenedor, la misma que usan las tareas
programadas): así «hoy» es el día del usuario y no el UTC. Cada lectura sobrescribe la fila del día, de modo que gana el
último valor.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Optional

from .trackers import EMU, Tracker

logger = logging.getLogger(__name__)

RETENCION_DIAS = 730
MAX_DIAS = 730
# Los ritmos salen de la última semana: lo bastante reciente para que cuente lo que haces ahora.
VENTANA_RITMOS_DIAS = 7

_POTENCIA = {"": 0, "K": 1, "M": 2, "G": 3, "T": 4, "P": 5}
_TAMANO = re.compile(r"^\s*(-?\d[\d,]*(?:\.\d+)?)\s*([KMGTP]?)i?B\s*$", re.I)
_COLUMNAS = ("ratio", "subido_bytes", "bajado_bytes", "bonus", "buffer_bytes", "seeding", "leeching",
             "hit_and_runs", "grupo")


def bytes_de_texto(texto: Any) -> Optional[int]:
    """«1.2 TiB» (como lo escribe UNIT3D; el buffer puede ser negativo) a bytes; None si no es un tamaño.
    Un número ya son bytes: así lo devuelve la próxima versión de UNIT3D."""
    if isinstance(texto, (int, float)) and not isinstance(texto, bool):
        return round(texto)
    m = _TAMANO.match(texto) if isinstance(texto, str) else None
    if not m:
        return None
    return round(float(m.group(1).replace(",", "")) * 1024 ** _POTENCIA[m.group(2).upper()])


def _numero(valor: Any) -> Optional[float]:
    """«4.1» o «12345.50» a número; None si no lo es (el ratio puede ser «∞»)."""
    try:
        n = float(str(valor).replace(",", ""))
    except ValueError:
        return None
    return n if n == n and abs(n) != float("inf") else None


def _entero(valor: Any) -> Optional[int]:
    n = _numero(valor)
    return None if n is None else round(n)


def guardar_instantanea(conn: sqlite3.Connection, datos: dict[str, Any], ahora: Optional[datetime] = None, *,
                        tracker: Tracker = EMU) -> None:
    """Escribe la instantánea de hoy de `tracker` con el perfil recién leído y poda lo que pase de la retención.
    No hace commit (lo hace quien guarda el perfil) y nunca lanza: el historial no puede romper la lectura."""
    try:
        ahora = ahora or datetime.now()
        grupo = datos.get("group")
        valores = (_numero(datos.get("ratio")), bytes_de_texto(datos.get("uploaded")),
                   bytes_de_texto(datos.get("downloaded")), _numero(datos.get("seedbonus")),
                   bytes_de_texto(datos.get("buffer")), _entero(datos.get("seeding")), _entero(datos.get("leeching")),
                   _entero(datos.get("hit_and_runs")), str(grupo) if grupo else None)
        usuario = datos.get("username") or None
        nombres = ", ".join(_COLUMNAS)
        conn.execute(
            f"""INSERT INTO perfil_historial (tracker, dia, {nombres}, usuario, actualizado_at)
                VALUES ({", ".join("?" * (len(_COLUMNAS) + 4))})
                ON CONFLICT(tracker, dia) DO UPDATE SET {", ".join(f"{c} = excluded.{c}" for c in _COLUMNAS)},
                    usuario = excluded.usuario, actualizado_at = excluded.actualizado_at""",
            (tracker.id, ahora.strftime("%Y-%m-%d"), *valores, usuario, ahora.isoformat(timespec="seconds")),
        )
        # El historial es de una cuenta: si el perfil es de otro usuario (otra clave), lo anterior no se mezcla. Va
        # después de escribir la de hoy: si esa falla, no se ha borrado nada.
        if usuario:
            conn.execute("DELETE FROM perfil_historial WHERE tracker = ? AND usuario IS NOT NULL AND usuario != ?",
                         (tracker.id, usuario))
        conn.execute("DELETE FROM perfil_historial WHERE tracker = ? AND dia < ?",
                     (tracker.id, (ahora - timedelta(days=RETENCION_DIAS)).strftime("%Y-%m-%d")))
    except Exception:
        logger.warning("No se pudo guardar la instantánea de la cuenta", exc_info=True)


def serie_cuenta(conn: sqlite3.Connection, dias: int, ahora: Optional[datetime] = None, *,
                 tracker: Tracker = EMU) -> list[dict[str, Any]]:
    """Las instantáneas de `tracker` de los últimos `dias` días (hoy incluido), de la más antigua a la más reciente."""
    desde = ((ahora or datetime.now()) - timedelta(days=dias - 1)).strftime("%Y-%m-%d")
    filas = conn.execute(
        f"SELECT dia, {', '.join(_COLUMNAS)} FROM perfil_historial WHERE tracker = ? AND dia >= ? ORDER BY dia",
        (tracker.id, desde))
    return [dict(f) for f in filas]


def ritmos_cuenta(conn: sqlite3.Connection, ahora: Optional[datetime] = None, *,
                  tracker: Tracker = EMU) -> dict[str, Any]:
    """Cuánto sube tu cuenta al día (subido, bajado) y cuánto bonus ganas por hora, de las instantáneas de la última
    semana. Cada tramo entre dos instantáneas seguidas da un ritmo, y se toma la mediana: un tramo con una compra en la
    tienda de bonus (el bonus baja y el subido salta) no tuerce la cifra. En el bonus no cuentan los tramos en los que
    bajó, que son gasto y no lo que ganas. None donde no hay al menos un tramo."""
    desde = ((ahora or datetime.now()) - timedelta(days=VENTANA_RITMOS_DIAS)).strftime("%Y-%m-%d")
    filas = [dict(f) for f in conn.execute(
        """SELECT actualizado_at, subido_bytes, bajado_bytes, bonus
           FROM perfil_historial WHERE tracker = ? AND dia >= ? ORDER BY dia""", (tracker.id, desde))]

    def por_hora(campo: str, solo_subidas: bool = False) -> Optional[float]:
        tramos = []
        for antes, despues in zip(filas, filas[1:]):
            try:
                horas = (datetime.fromisoformat(despues["actualizado_at"])
                         - datetime.fromisoformat(antes["actualizado_at"])).total_seconds() / 3600
            except (TypeError, ValueError):
                continue
            if horas < 1 or antes[campo] is None or despues[campo] is None:
                continue
            cambio = despues[campo] - antes[campo]
            if solo_subidas and cambio < 0:
                continue
            tramos.append(cambio / horas)
        return median(tramos) if tramos else None

    def al_dia(campo: str) -> Optional[int]:
        ritmo = por_hora(campo)
        return None if ritmo is None else round(ritmo * 24)

    bonus = por_hora("bonus", solo_subidas=True)
    return {
        "dias": VENTANA_RITMOS_DIAS,
        "subido_dia": al_dia("subido_bytes"),
        "bajado_dia": al_dia("bajado_bytes"),
        "bonus_hora": None if bonus is None else round(bonus, 2),
    }
