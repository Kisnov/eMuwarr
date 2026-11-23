"""«Subir también a…»: lo que ya está en un tracker (una subida completada hecha con eMuwarr) se sube a otro con su clave
(de eMuwarez a N.O.B.S o Milnueve, o al revés) con el mismo fichero de la carpeta de subidas, el mismo nombre y las
mismas piezas, para sembrar los dos torrents desde el mismo fichero sin volver a preparar ni a hashear nada.

Cada destino es un trabajo normal de la cola (upload_queue.queue_copia_a_otro_tracker) que apunta en
`copia_de_job_id` la subida de origen: reutiliza su preparación y, si su `tmp/<nombre>` ya no tiene un BASE.torrent que
valga, Uploadrr-EMU lo hace con el torrent de otro tracker (`--base-desde <TK>:<id>`,
upload_execution.consultas.base_desde_para). Varias a la vez van como un lote (services/lotes.py) y respetan «Subidas en
paralelo» como cualquier subida: solo las que elige el usuario, nada en bloque por su cuenta.

Antes de encolar se mira en qBittorrent que el torrent de origen está entero con ese fichero como contenido: si no,
el nuevo torrent no tendría de dónde sembrarse."""
from __future__ import annotations

import os
import sqlite3

from ..database import rows_to_dicts
from . import seeding_qbit, trackers
from .lotes import MAX_ELEMENTOS_LOTE, nuevo_lote_id
from .upload_execution.consultas import tracker_del_trabajo


class SubirTambienRechazado(Exception):
    """Nada encolado: falta algo que haría fallar a todos (un tracker sin clave, una petición vacía)."""


def destinos(conn: sqlite3.Connection) -> list[trackers.Tracker]:
    """Los trackers a los que se puede subir también: los que tienen su clave puesta, eMuwarez también."""
    return [t for t in trackers.para_subir(conn) if trackers.tiene_clave(conn, t)]


def _motivo_tracker(conn: sqlite3.Connection, tracker_id: str) -> str | None:
    """trackers.motivo_para_no_subir, y además sin la clave de eMuwarez: aquí no hay asistente que la pida."""
    motivo = trackers.motivo_para_no_subir(conn, tracker_id)
    if motivo or trackers.tiene_clave(conn, trackers.obtener(tracker_id)):
        return motivo
    return f"Falta la clave de API de {trackers.obtener(tracker_id).nombre}: ponla en Configuración."


def _motivo_fuente(job: dict | None) -> str | None:
    """None si `job` es una subida completada que se puede subir también a otro tracker; si no, por qué no."""
    if not job:
        return "Subida no encontrada."
    if job.get("status") != "completed":
        return f"La subida #{job['id']} no está completada."
    if not str(job.get("result_torrent_id") or "").strip().isdecimal():
        return f"La subida #{job['id']} no tiene apuntado el id de su torrent en {tracker_del_trabajo(job).nombre}."
    if not job.get("library_item_id"):
        return f"La subida #{job['id']} ya no tiene su elemento en la biblioteca."
    if not job.get("prepared_path") or not os.path.exists(job["prepared_path"]):
        return f"El fichero de la subida #{job['id']} ya no está en la carpeta de subidas."
    return None


def _subidas_por_tracker(conn: sqlite3.Connection, item_ids: list[int]) -> dict[tuple[int, str], int]:
    """(elemento, tracker) → la última subida de ese elemento a ese tracker, en una sola consulta."""
    if not item_ids:
        return {}
    filas = conn.execute(
        f"SELECT library_item_id, tracker, MAX(id) AS id FROM upload_jobs "
        f"WHERE library_item_id IN ({','.join('?' * len(item_ids))}) GROUP BY library_item_id, tracker",
        item_ids,
    ).fetchall()
    return {(f["library_item_id"], f["tracker"] or trackers.POR_DEFECTO): f["id"] for f in filas}


def _motivo_destino(conn: sqlite3.Connection, job: dict, tracker_id: str,
                    existentes: dict[tuple[int, str], int]) -> str | None:
    """None si se puede crear la subida de `job` a `tracker_id`; si no, por qué no. Su propio tracker no sale nunca:
    `job` misma está en `existentes`."""
    motivo = _motivo_tracker(conn, tracker_id)
    if motivo:
        return motivo
    otra = existentes.get((job["library_item_id"], tracker_id))
    if otra:
        # Una fallida se reintenta desde su propia página: así sigue su cadena de reintentos.
        return f"Ya hay una subida de este contenido a {trackers.obtener(tracker_id).nombre} (#{otra})."
    return None


def ofrecidos(conn: sqlite3.Connection, job: dict) -> list[trackers.Tracker]:
    """Los trackers que el detalle de una subida ofrece en «Subir también a…» (sin mirar qBittorrent: eso, al pulsar)."""
    if _motivo_fuente(job) is not None:
        return []
    existentes = _subidas_por_tracker(conn, [job["library_item_id"]])
    return [t for t in destinos(conn) if _motivo_destino(conn, job, t.id, existentes) is None]


def candidatas(conn: sqlite3.Connection) -> list[dict]:
    """Las subidas que se pueden subir también a algún tracker, la más reciente primero: de cada elemento, la última
    completada que vale de origen, sea del tracker que sea (`origen`), con los trackers que se le ofrecen."""
    posibles = destinos(conn)
    if not posibles:
        return []
    filas = rows_to_dicts(conn.execute(
        "SELECT * FROM upload_jobs WHERE status = 'completed' AND library_item_id IS NOT NULL "
        "ORDER BY finished_at DESC, id DESC",
    ).fetchall())
    existentes = _subidas_por_tracker(conn, sorted({f["library_item_id"] for f in filas}))
    salida, vistos = [], set()
    for job in filas:
        # Una más nueva que ya no vale (sin su fichero, sin id) no tapa a otra del mismo elemento que sí.
        if job["library_item_id"] in vistos or _motivo_fuente(job) is not None:
            continue
        vistos.add(job["library_item_id"])
        libres = [t for t in posibles if _motivo_destino(conn, job, t.id, existentes) is None]
        if libres:
            salida.append({"job": job, "origen": tracker_del_trabajo(job), "trackers": libres})
    return salida


def _bloqueada(job_id: int, tracker_id: str, motivo: str) -> dict:
    return {"job_id": job_id, "tracker": tracker_id, "status": "blocked", "nuevo_job_id": None, "motivo": motivo}


def subir_tambien(conn: sqlite3.Connection, job_ids: list[int], tracker_ids: list[str]) -> dict:
    """Encola, para cada subida completada de `job_ids` y cada tracker de `tracker_ids` que no sea el suyo, la subida a
    ese tracker con el mismo fichero. Lo que no se puede se salta con su motivo y el resto sigue. Más de una van con la
    misma etiqueta de lote. Devuelve {lote_id, encolados, saltados, resultados}."""
    job_ids = list(dict.fromkeys(int(i) for i in job_ids))
    tracker_ids = list(dict.fromkeys(tracker_ids))
    if not job_ids or not tracker_ids:
        raise SubirTambienRechazado(["Elige al menos una subida y un tracker."])
    if len(job_ids) > MAX_ELEMENTOS_LOTE:
        raise SubirTambienRechazado([f"Como mucho {MAX_ELEMENTOS_LOTE} subidas a la vez."])
    sin_poder = [m for t in tracker_ids if (m := _motivo_tracker(conn, t))]
    if sin_poder:
        raise SubirTambienRechazado(sin_poder)

    from .upload_execution import get_upload_job
    from .upload_queue import queue_copia_a_otro_tracker

    fuentes = {i: get_upload_job(i, conn=conn) for i in job_ids}
    # A su propio tracker no se sube «también»: con todos los trackers marcados, esos pares no cuentan.
    pares = [(i, t) for i in job_ids for t in tracker_ids if not fuentes[i] or t != tracker_del_trabajo(fuentes[i]).id]
    if not pares:
        raise SubirTambienRechazado(["Elige un tracker distinto del de esas subidas."])
    validas = {i: j for i, j in fuentes.items() if _motivo_fuente(j) is None}
    # Una sola lectura de qBittorrent para todas: el torrent de origen tiene que estar entero con ese contenido.
    en_qbit = seeding_qbit.completo_en_qbit(
        conn, {j["prepared_path"]: tracker_del_trabajo(j).nombre for j in validas.values()}) if validas else {}
    existentes = _subidas_por_tracker(conn, sorted({j["library_item_id"] for j in validas.values()}))
    lote_id = nuevo_lote_id() if len(pares) > 1 else None

    resultados: list[dict] = []
    for job_id, tracker_id in pares:
        fuente = fuentes[job_id]
        motivo = (_motivo_fuente(fuente) or en_qbit.get(fuente["prepared_path"])
                  or _motivo_destino(conn, fuente, tracker_id, existentes))
        if motivo:
            resultados.append(_bloqueada(job_id, tracker_id, motivo))
            continue
        r = queue_copia_a_otro_tracker(conn, fuente, tracker_id, lote_id=lote_id)
        conn.commit()
        nuevo = r.get("job") if r.get("status") == "queued" else None
        if nuevo is None:
            resultados.append(_bloqueada(job_id, tracker_id, "; ".join(r.get("errors") or []) or "No se pudo encolar."))
            continue
        existentes[(fuente["library_item_id"], tracker_id)] = nuevo["id"]
        resultados.append({"job_id": job_id, "tracker": tracker_id, "status": "queued", "nuevo_job_id": nuevo["id"],
                           "motivo": None})
    encolados = sum(1 for r in resultados if r["status"] == "queued")
    return {"lote_id": lote_id if encolados else None, "encolados": encolados,
            "saltados": len(resultados) - encolados, "resultados": resultados}


__all__ = ["SubirTambienRechazado", "destinos", "ofrecidos", "candidatas", "subir_tambien"]
