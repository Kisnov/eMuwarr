"""Tus subidas en eMuwarez: seeders, leechers y veces completada de cada una, y cuánto se han bajado esta semana.

Una pasada pide `/api/torrents/filter?uploader=<tu usuario>` de 100 en 100 (una llamada por cada 100 subidas, como un
recorrido largo del limitador) cada 6 horas, con la tarea «Leer tus subidas en eMuwarez». No corre con una subida en
marcha, como el catálogo: Uploadrr-EMU gasta llamadas de la misma clave y no pasa por el limitador. eMuwarez guarda
cada consulta unos minutos, así que pedirla más a menudo no daría cifras más frescas.

UNIT3D no da las subidas anónimas al buscar por usuario, así que no salen.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

from ..database import get_json_state, get_setting_canonical, set_json_state
from ..integrations.base import IntegrationError, texto_de_error
from ..integrations.emu import emu_fetch_all_torrents

logger = logging.getLogger(__name__)

ESTADO = "emu.mis_torrents"
RETENCION_DIAS = 90
SEMANA_DIAS = 7
# 2.000 subidas: de sobra, y un tope por si el filtro no se aplicara y llegara el catálogo entero.
MAX_PAGINAS = 20


def _entero(valor: Any) -> Optional[int]:
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return None


def _fila(torrent: dict[str, Any]) -> Optional[tuple]:
    a = torrent.get("attributes") or {}
    torrent_id = _entero(torrent.get("id"))
    if torrent_id is None or not a.get("name"):
        return None
    freeleech = a.get("freeleech")
    return (torrent_id, str(a["name"]), _entero(a.get("size")), a.get("created_at"), _entero(a.get("seeders")),
            _entero(a.get("leechers")), _entero(a.get("times_completed")),
            str(freeleech) if freeleech not in (None, "", "0%") else None, 1 if a.get("double_upload") else 0)


def sincronizar(conn: sqlite3.Connection, ahora: Optional[datetime] = None) -> tuple[str, str]:
    """Lee tus subidas y guarda la foto de hoy. Devuelve (estado, resumen) para la tarea programada; `busy` con una
    subida en marcha, para reintentarlo en el siguiente minuto."""
    from .emu_catalog import motivo_subida_en_marcha

    clave = (get_setting_canonical(conn, "emu.api_key") or "").strip()
    usuario = (get_setting_canonical(conn, "emu.username") or "").strip()
    if not clave:
        return "skipped", "Sin API key de eMuwarez en Configuración"
    if not usuario:
        return "skipped", "Aún no se sabe tu usuario de eMuwarez: sale de la primera lectura de tu cuenta"
    motivo = motivo_subida_en_marcha(conn)
    if motivo:
        return "busy", motivo
    try:
        torrents = emu_fetch_all_torrents(clave, uploader=usuario, max_pages=MAX_PAGINAS)
    except IntegrationError as exc:
        return "failed", texto_de_error(exc)
    filas = [f for f in map(_fila, torrents) if f]

    ahora = ahora or datetime.now()
    dia = ahora.strftime("%Y-%m-%d")
    # La lista es entera cada vez: lo que ya no sale es que se ha borrado del tracker.
    conn.execute("DELETE FROM emu_mis_torrents")
    conn.executemany(
        """INSERT INTO emu_mis_torrents (torrent_id, nombre, tamano, subido_at, seeders, leechers, completados,
               freeleech, doble_subida) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", filas)
    conn.executemany(
        """INSERT INTO emu_mis_torrents_dia (dia, torrent_id, seeders, leechers, completados) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(torrent_id, dia) DO UPDATE SET seeders = excluded.seeders, leechers = excluded.leechers,
               completados = excluded.completados""",
        [(dia, f[0], f[4], f[5], f[6]) for f in filas])
    conn.execute("DELETE FROM emu_mis_torrents_dia WHERE dia < ?",
                 ((ahora - timedelta(days=RETENCION_DIAS)).strftime("%Y-%m-%d"),))
    conn.execute("DELETE FROM emu_mis_torrents_dia WHERE torrent_id NOT IN (SELECT torrent_id FROM emu_mis_torrents)")
    # Con su zona: la interfaz lee una hora sin zona como UTC.
    set_json_state(conn, ESTADO, {"leido_at": ahora.astimezone().isoformat(timespec="seconds"), "usuario": usuario})
    conn.commit()
    sin_seeders = sum(1 for f in filas if not f[4])
    return "completed", f"{len(filas)} subidas, {sin_seeders} sin seeders"


def resumen(conn: sqlite3.Connection, ahora: Optional[datetime] = None) -> dict[str, Any]:
    """Tus subidas de la última lectura, con lo completado en los últimos 7 días, y sus totales. Lo de la semana es
    la diferencia con la foto de hace 7 días; un torrent subido en esta semana cuenta entero, y uno más antiguo sin foto
    de entonces (las primeras lecturas) queda a None."""
    desde = ((ahora or datetime.now()) - timedelta(days=SEMANA_DIAS)).strftime("%Y-%m-%d")
    filas = [dict(f) for f in conn.execute(
        """SELECT t.torrent_id, t.nombre, t.tamano, t.subido_at, t.seeders, t.leechers, t.completados, t.freeleech,
                  t.doble_subida,
                  (SELECT d.completados FROM emu_mis_torrents_dia d
                    WHERE d.torrent_id = t.torrent_id AND d.dia <= ? ORDER BY d.dia DESC LIMIT 1) AS completados_antes
           FROM emu_mis_torrents t ORDER BY t.completados DESC, t.torrent_id DESC""", (desde,))]
    for f in filas:
        antes = f.pop("completados_antes")
        if antes is None and (f["subido_at"] or "")[:10] >= desde:
            antes = 0
        f["completados_semana"] = (f["completados"] - antes
                                   if f["completados"] is not None and antes is not None else None)
        f["doble_subida"] = bool(f["doble_subida"])
    estado = get_json_state(conn, ESTADO) or {}
    semana = [f["completados_semana"] for f in filas if f["completados_semana"] is not None]
    return {
        "leido_at": estado.get("leido_at"),
        "torrents": filas,
        "totales": {
            "subidas": len(filas),
            "seeders": sum(f["seeders"] or 0 for f in filas),
            "leechers": sum(f["leechers"] or 0 for f in filas),
            "completados": sum(f["completados"] or 0 for f in filas),
            "completados_semana": sum(semana) if semana else None,
            "sin_seeders": sum(1 for f in filas if not f["seeders"]),
        },
    }
