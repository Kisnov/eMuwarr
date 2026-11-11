"""«Reenviar a qBittorrent»: vuelve a añadir el torrent de una subida que ya está en eMuwarez, sin subir nada.

No crea otra fila (contaría doble en el Panel, el seeding, los reintentos y los lotes): el reenvío vive en dos
columnas de la subida original (`reenvio_qbit_estado`, `reenvio_qbit_at`) y escribe en su registro. Su `status`,
`finished_at`, `result_url`, el tráiler y los avisos no se tocan.

No pasa por «Subidas en paralelo»: dura segundos (un .torrent de eMuwarez y una llamada a qBittorrent, que hace la
comprobación por su cuenta) y la puerta da turno por id, así que el id de una subida vieja se colaría en la cola.
"""
from __future__ import annotations

import os
import threading

from .. import seeding_qbit
from ..eventos import EVENTO_SUBIDA_ESTADO, publicar
from ..uploadrr_contract import RUNTIME_EMU, get_runtime_settings
from ..uploadrr_progress import parse_uploadrr_progress
from .comun import QBIT_AÑADIDO, _collect_secrets, _get_db_conn, _now, logger, redact_secrets
from .config import _acquire_staged_config, _release_staged_config
from .consultas import build_uploadrr_execution_command, get_upload_job
from .ejecucion import _lanzar_uploadrr, _leer_y_esperar, _soltar_proceso
from .progreso import mark_phase_done
from .registro import append_upload_log
from ...integrations.base import texto_de_error

EN_CURSO, OK, FALLIDO = "en_curso", "ok", "fallido"
# Solo baja un .torrent y se lo da a qBittorrent: si en cinco minutos no ha terminado, no va a terminar.
_TIMEOUT = 300


def _motivo_para_no_reenviar(job: dict | None) -> tuple[str, str] | None:
    """(error, mensaje) si esa subida no se puede reenviar; None si se puede."""
    if not job:
        return "not_found", "Subida no encontrada."
    if job.get("status") != "completed":
        return "invalid_status", "Solo se reenvía a qBittorrent una subida completada."
    if not job.get("result_torrent_id"):
        return "sin_torrent_id", "La subida no tiene apuntado el id de su torrent en eMuwarez."
    if not job.get("prepared_path") or not os.path.exists(job["prepared_path"]):
        return "sin_ficheros", "Los ficheros de la subida ya no están en la carpeta de subidas."
    if job.get("reenvio_qbit_estado") == EN_CURSO:
        return "en_curso", "Ya se está reenviando a qBittorrent."
    return None


def puede_reenviar_qbit(job: dict) -> bool:
    return _motivo_para_no_reenviar(job) is None


def _escribir_estado(job_id: int, estado: str, *, solo_si_libre: bool = False, conn=None) -> bool:
    """El único sitio que escribe `reenvio_qbit_estado` (salvo el arranque, ver reconcile_interrupted_jobs).
    `solo_si_libre` lo toma solo si la subida sigue completada y no hay otro reenvío en curso."""
    condicion = " AND status = 'completed' AND reenvio_qbit_estado IS NOT 'en_curso'" if solo_si_libre else ""
    propia = conn is None
    conn = conn or _get_db_conn()
    try:
        cur = conn.execute(
            f"UPDATE upload_jobs SET reenvio_qbit_estado = ?, reenvio_qbit_at = ? WHERE id = ?{condicion}",
            (estado, _now(), job_id),
        )
        conn.commit()
    finally:
        if propia:
            conn.close()
    if cur.rowcount:
        publicar(EVENTO_SUBIDA_ESTADO, {"job_id": job_id})
    return cur.rowcount > 0


def reenviar_a_qbittorrent(job_id: int, *, conn=None, wait: bool = False) -> dict:
    """Comprueba que se puede, marca 'en_curso' y lanza Uploadrr-EMU con `--solo-cliente` en un hilo.
    Devuelve {ok, error, message}. `wait` espera a que termine (tests)."""
    propia = conn is None
    conn = conn or _get_db_conn()
    try:
        job = get_upload_job(job_id, conn=conn)
        motivo = _motivo_para_no_reenviar(job)
        if motivo is None and get_runtime_settings(conn)["runtime_type"] != RUNTIME_EMU:
            motivo = "unsupported_runtime", f"Reenviar a qBittorrent necesita runtime_type={RUNTIME_EMU}."
        if motivo is not None:
            return {"ok": False, "error": motivo[0], "message": motivo[1]}
        torrent_id = str(job["result_torrent_id"])
        cmd = build_uploadrr_execution_command(job, conn=conn, solo_cliente=torrent_id)
        secrets = _collect_secrets(conn)
        if not _escribir_estado(job_id, EN_CURSO, solo_si_libre=True, conn=conn):
            return {"ok": False, "error": "en_curso", "message": "Ya se está reenviando a qBittorrent."}
    finally:
        if propia:
            conn.close()
    hilo = threading.Thread(target=_reenviar, args=(job_id, torrent_id, cmd, secrets), daemon=True)
    hilo.start()
    logger.info("Reenvío a qBittorrent (job %s, torrent %s)", job_id, torrent_id)
    if wait:
        hilo.join()
    return {"ok": True, "error": None, "message": "Reenviando a qBittorrent."}


def _avisar_de_qbit(job_id: int, line: str, secrets: list[str]) -> None:
    """Lo que Uploadrr-EMU dice de qBittorrent, en una línea clara del registro."""
    try:
        marca = parse_uploadrr_progress(line)
        if not marca or marca[0] != "qbittorrent_add" or marca[2] is None:
            return
        if marca[2]:
            append_upload_log(job_id, "info", QBIT_AÑADIDO, stream="system")
        elif "Torrent client add failed:" in line:
            motivo = redact_secrets(line.split("Torrent client add failed:", 1)[1].strip(), secrets)
            append_upload_log(job_id, "error", f"qBittorrent: falló al añadir el torrent ({motivo}).", stream="system")
        elif "INCORRECT QBIT LOGIN CREDENTIALS" in line:
            append_upload_log(job_id, "error", "qBittorrent: credenciales incorrectas. Revisa la configuración de "
                              "qBittorrent en Ajustes.", stream="system")
    except Exception:
        logger.exception("Reenvío %s: no se pudo registrar lo que dijo qBittorrent", job_id)


def _lanzar(job_id: int, torrent_id: str, cmd: dict, secrets: list[str]) -> tuple[str, str]:
    """Prepara el config.yaml, lanza Uploadrr-EMU y espera. Devuelve (resultado, línea final del registro)."""
    append_upload_log(job_id, "info", f"Reenvío a qBittorrent: se vuelve a añadir el torrent {torrent_id} de "
                      "eMuwarez, sin subir nada.", stream="system")
    append_upload_log(job_id, "info", f"Comando: {redact_secrets(cmd.get('command_preview', ''), secrets)}",
                      stream="system")
    cwd = cmd.get("cwd") or "."
    config = None
    proc = None
    try:
        if os.path.isdir(cwd):
            config = _acquire_staged_config(job_id, cwd)
        proc = _lanzar_uploadrr(job_id, [cmd["executable"]] + cmd["args"], cwd)
        codigo, sin_tiempo, _out, _err = _leer_y_esperar(
            job_id, proc, _TIMEOUT, secrets, lambda linea: _avisar_de_qbit(job_id, linea, secrets))
    finally:
        _soltar_proceso(job_id, proc)
        _release_staged_config(job_id, config)
    if sin_tiempo:
        return FALLIDO, f"Reenvío a qBittorrent fallido: no terminó en {_TIMEOUT} s."
    if codigo != 0:
        return FALLIDO, f"Reenvío a qBittorrent fallido (exit code {codigo}). La subida a eMuwarez sigue igual."
    return OK, "Reenvío a qBittorrent hecho: qBittorrent comprueba los ficheros antes de sembrar."


def _reenviar(job_id: int, torrent_id: str, cmd: dict, secrets: list[str]) -> None:
    try:
        resultado, final = _lanzar(job_id, torrent_id, cmd, secrets)
    except Exception as exc:
        resultado = FALLIDO
        final = f"Reenvío a qBittorrent fallido: {redact_secrets(texto_de_error(exc), secrets)}"
    try:
        append_upload_log(job_id, "info" if resultado == OK else "error", final, stream="system")
        if resultado == OK:
            mark_phase_done(job_id, "qbittorrent_add")
        _escribir_estado(job_id, resultado)
        if resultado == OK:
            seeding_qbit.olvidar_cache()
    except Exception:
        logger.exception("Reenvío %s: no se pudo guardar el resultado (%s)", job_id, resultado)
