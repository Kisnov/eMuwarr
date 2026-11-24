"""Tus subidas en eMuwarez y en cada otro tracker con clave: seeders, leechers y veces completada de cada una, y cuánto
se han bajado esta semana.

Una pasada pide a cada tracker `/api/torrents/filter?uploader=<tu usuario allí>` de 100 en 100 (una llamada por cada 100
subidas, como un recorrido largo del limitador, con el cupo de ese tracker) cada 6 horas, con la tarea «Leer tus subidas
en eMuwarez». No corre con una subida en
marcha, como el catálogo: Uploadrr-EMU gasta llamadas de la misma clave y no pasa por el limitador. eMuwarez guarda
cada consulta unos minutos, así que pedirla más a menudo no daría cifras más frescas.

UNIT3D no da las subidas anónimas al buscar por usuario, así que no salen. Tampoco las que siguen pendientes de
moderación (en eMuwarez, hasta que un moderador las aprueba, a veces un día): `pendiente_de_moderacion` lo deduce de
eso, con la lectura del tracker de cada subida, sin pedir nada más.
"""
from __future__ import annotations

import json
import logging
import shlex
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

from ..database import get_json_state, set_json_state
from ..integrations.base import IntegrationError, texto_de_error
from ..integrations.emu import emu_fetch_all_torrents
from .trackers import EMU, Tracker, para_subir
from .settings import ajuste_de_tracker

logger = logging.getLogger(__name__)

ESTADO = "mis_torrents"  # la clave de estado es `<tracker>.mis_torrents`: la de eMuwarez, la de siempre
RETENCION_DIAS = 90
SEMANA_DIAS = 7
# 2.000 subidas: de sobra, y un tope por si el filtro no se aplicara y llegara el catálogo entero. Si se llega, la
# lectura no está completa y no se guarda (borraría las que faltan).
MAX_PAGINAS = 20
POR_PAGINA = 100

# «Ejecutar ahora» y el reloj no leen a la vez: los dos sustituirían la lista entera.
_LEYENDO = threading.Lock()


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
    """Lee tus subidas en eMuwarez y luego en cada otro tracker con clave, y guarda la foto de hoy de cada uno. Devuelve
    (estado, resumen) para la tarea programada; `busy` con una subida en marcha o con otra lectura en curso, para
    reintentarlo en el siguiente minuto. Un tracker que falla no para a los demás ni borra lo suyo."""
    from .emu_catalog import motivo_subida_en_marcha

    if not _LEYENDO.acquire(blocking=False):
        return "busy", "Ya hay una lectura de tus subidas en marcha"
    try:
        motivo = motivo_subida_en_marcha(conn)
        if motivo:
            return "busy", motivo
        lecturas = [(t, *_sincronizar(conn, ahora, t)) for t in para_subir(conn)]
    finally:
        _LEYENDO.release()
    if len(lecturas) == 1:
        return lecturas[0][1:]
    estados = {estado for _, estado, _ in lecturas}
    estado = "failed" if "failed" in estados else "completed" if "completed" in estados else "skipped"
    return estado, " · ".join(f"{t.nombre}: {texto}" for t, _, texto in lecturas)


def _sincronizar(conn: sqlite3.Connection, ahora: Optional[datetime], tracker: Tracker) -> tuple[str, str]:
    clave = (ajuste_de_tracker(conn, "api_key", tracker.id) or "").strip()
    usuario = (ajuste_de_tracker(conn, "username", tracker.id) or "").strip()
    if not clave:
        return "skipped", f"Sin API key de {tracker.nombre} en Configuración"
    if not usuario:
        return "skipped", f"Aún no se sabe tu usuario de {tracker.nombre}: sale de la primera lectura de tu cuenta"
    try:
        torrents = emu_fetch_all_torrents(clave, tracker.url, uploader=usuario, per_page=POR_PAGINA,
                                          max_pages=MAX_PAGINAS, tracker=tracker.uploadrr)
    except IntegrationError as exc:
        return "failed", texto_de_error(exc)
    if len(torrents) >= MAX_PAGINAS * POR_PAGINA:
        return "failed", (f"Tienes {MAX_PAGINAS * POR_PAGINA} subidas o más y la lectura se corta ahí: no se guarda, "
                          "para no borrar las que faltan")
    # Una por torrent: una subida nueva mientras se pagina repite un id, y la clave primaria tumbaba la lectura entera.
    filas = list({f[0]: f for f in map(_fila, torrents) if f}.values())

    ahora = ahora or datetime.now()
    dia = ahora.strftime("%Y-%m-%d")
    try:
        # La lista es entera cada vez: lo que ya no sale es que se ha borrado del tracker.
        conn.execute("DELETE FROM emu_mis_torrents WHERE tracker = ?", (tracker.id,))
        conn.executemany(
            """INSERT INTO emu_mis_torrents (tracker, torrent_id, nombre, tamano, subido_at, seeders, leechers,
                   completados, freeleech, doble_subida) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(tracker.id, *f) for f in filas])
        conn.executemany(
            """INSERT INTO emu_mis_torrents_dia (tracker, dia, torrent_id, seeders, leechers, completados)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(tracker, torrent_id, dia) DO UPDATE SET seeders = excluded.seeders,
                   leechers = excluded.leechers, completados = excluded.completados""",
            [(tracker.id, dia, f[0], f[4], f[5], f[6]) for f in filas])
        conn.execute("DELETE FROM emu_mis_torrents_dia WHERE tracker = ? AND dia < ?",
                     (tracker.id, (ahora - timedelta(days=RETENCION_DIAS)).strftime("%Y-%m-%d")))
        conn.execute(
            "DELETE FROM emu_mis_torrents_dia WHERE tracker = ? AND torrent_id NOT IN "
            "(SELECT torrent_id FROM emu_mis_torrents WHERE tracker = ?)", (tracker.id, tracker.id))
        # Con su zona: la interfaz lee una hora sin zona como UTC.
        set_json_state(conn, f"{tracker.id}.{ESTADO}",
                       {"leido_at": ahora.astimezone().isoformat(timespec="seconds"), "usuario": usuario})
        conn.commit()
    except Exception:
        conn.rollback()   # a medias no: se queda la lectura anterior entera
        raise
    sin_seeders = sum(1 for f in filas if f[4] == 0)
    return "completed", f"{len(filas)} subidas, {sin_seeders} sin seeders"


def _estado_vigente(conn: sqlite3.Connection, tracker: Tracker) -> Optional[dict[str, Any]]:
    """El estado de la última lectura en `tracker`, o None si no hay una del usuario de sus ajustes."""
    estado = get_json_state(conn, f"{tracker.id}.{ESTADO}") or {}
    usuario = (ajuste_de_tracker(conn, "username", tracker.id) or "").strip()
    return estado if estado.get("leido_at") and estado.get("usuario") == usuario else None


def lectura_para_moderacion(conn: sqlite3.Connection) -> dict[str, tuple[datetime, frozenset[int]]]:
    """{tracker: (cuándo, ids)} de la última lectura de tus subidas en cada tracker que la tiene, para
    `pendiente_de_moderacion`. Se pide una vez para toda la lista de subidas."""
    lecturas = {}
    for tracker in para_subir(conn):
        estado = _estado_vigente(conn, tracker)
        if estado:
            ids = frozenset(fila[0] for fila in conn.execute(
                "SELECT torrent_id FROM emu_mis_torrents WHERE tracker = ?", (tracker.id,)))
            lecturas[tracker.id] = (datetime.fromisoformat(estado["leido_at"]).astimezone(), ids)
    return lecturas


def _anonima(job: dict[str, Any]) -> bool:
    """Si se subió con `-a`: eMuwarez no la da al buscar por usuario, salga o no de moderación."""
    try:
        texto = json.loads(job.get("safe_command_json") or "{}").get("args_preview") or ""
    except (ValueError, AttributeError):
        return False
    try:
        return "-a" in shlex.split(texto)
    except ValueError:
        return "-a" in texto.split()


def pendiente_de_moderacion(job: dict[str, Any], lecturas: dict[str, tuple[datetime, frozenset[int]]]) -> bool:
    """Una subida completada, con su torrent en el tracker y no anónima, que no sale en una lectura de tus subidas en ese
    tracker hecha después de terminar: UNIT3D no da las que un moderador aún no ha aprobado. Una rechazada o borrada se
    ve igual, y una recién aprobada sigue así hasta la lectura siguiente (cada 6 horas)."""
    lectura = lecturas.get(job.get("tracker") or EMU.id)
    if lectura is None or job.get("status") != "completed" or _anonima(job):
        return False
    torrent_id = _entero(job.get("result_torrent_id"))
    try:
        terminada = datetime.fromisoformat(str(job.get("finished_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if torrent_id is None or terminada.tzinfo is None:
        return False
    leido_at, ids = lectura
    return terminada < leido_at and torrent_id not in ids


def _dia_local(fecha_iso: Optional[str]) -> str:
    """El día local (el de las fotos) de una fecha ISO con zona, como el `created_at` de UNIT3D («…Z»)."""
    try:
        return datetime.fromisoformat((fecha_iso or "").replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return ""


VACIO = {"subidas": 0, "seeders": 0, "leechers": 0, "completados": 0, "completados_semana": None, "sin_seeders": 0}


def resumen(conn: sqlite3.Connection, tracker: Tracker = EMU) -> dict[str, Any]:
    """Tus subidas en `tracker` de la última lectura, con lo completado en los 7 días anteriores a esa lectura, y sus
    totales. Lo de la semana es la diferencia con la foto del día de corte; un torrent subido ese día o después, sin foto
    de entonces, cuenta entero, y uno más antiguo sin foto de ese día queda a None. Si la lectura es de otro usuario que
    el de Ajustes, no se enseña. `url` es la del tracker, para enlazar cada ficha."""
    estado = _estado_vigente(conn, tracker)
    if not estado:
        return {"leido_at": None, "url": tracker.url, "torrents": [], "totales": dict(VACIO)}
    leido = datetime.fromisoformat(estado["leido_at"]).astimezone()
    corte = (leido - timedelta(days=SEMANA_DIAS)).strftime("%Y-%m-%d")
    filas = [dict(f) for f in conn.execute(
        """SELECT t.torrent_id, t.nombre, t.tamano, t.subido_at, t.seeders, t.leechers, t.completados, t.freeleech,
                  t.doble_subida,
                  (SELECT d.completados FROM emu_mis_torrents_dia d
                    WHERE d.tracker = t.tracker AND d.torrent_id = t.torrent_id AND d.dia = ?) AS completados_antes
           FROM emu_mis_torrents t WHERE t.tracker = ?
           ORDER BY t.completados DESC, t.torrent_id DESC""", (corte, tracker.id))]
    for f in filas:
        antes = f.pop("completados_antes")
        if antes is None and _dia_local(f["subido_at"]) >= corte:
            antes = 0
        f["completados_semana"] = (f["completados"] - antes
                                   if f["completados"] is not None and antes is not None else None)
        f["doble_subida"] = bool(f["doble_subida"])
    semana = [f["completados_semana"] for f in filas if f["completados_semana"] is not None]
    return {
        "leido_at": estado["leido_at"],
        "url": tracker.url,
        "torrents": filas,
        "totales": {
            "subidas": len(filas),
            "seeders": sum(f["seeders"] or 0 for f in filas),
            "leechers": sum(f["leechers"] or 0 for f in filas),
            "completados": sum(f["completados"] or 0 for f in filas),
            "completados_semana": sum(semana) if semana else None,
            "sin_seeders": sum(1 for f in filas if f["seeders"] == 0),
        },
    }
