"""Lectura de trabajos de subida (get/list/count), validación previa al arranque y construcción del comando de Uploadrr."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ...database import get_setting_canonical, row_to_dict, rows_to_dicts
from ..upload_states import PREPARATORY_STATUSES
from ..uploadrr_eventos import admite_config, admite_eventos, ruta_eventos
from ..uploadrr_contract import (
    RUNTIME_EMU,
    build_uploadrr_emu_command,
    get_runtime_settings,
    render_command_preview,
)
from .. import trackers
from .comun import _get_db_conn
from .config import ruta_config


VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts",
    ".wmv", ".flv", ".webm", ".iso", ".m4v",
}


def get_upload_job(job_id: int, conn=None) -> dict | None:
    own = conn is None
    conn = conn or _get_db_conn()
    try:
        row = conn.execute("SELECT * FROM upload_jobs WHERE id = ?", (job_id,)).fetchone()
        return row_to_dict(row)
    finally:
        if own:
            conn.close()


# Una fila es la punta de su cadena de reintentos si ninguna otra la reintenta (la misma regla que
# upload_queue._find_latest_job_for_item). Siempre sobre toda la tabla: un intento posterior de otro lote o suelto
# también la deja atrás.
CONDICION_PUNTA = "id NOT IN (SELECT retry_of_job_id FROM upload_jobs WHERE retry_of_job_id IS NOT NULL)"


def _upload_jobs_where(filters: dict) -> tuple[str, list[Any]]:
    conditions = []
    params: list[Any] = []

    # Un estado o varios separados por coma («running,preparing»).
    estados = [e.strip() for e in (filters.get("status") or "").split(",") if e.strip()]
    if estados:
        conditions.append(f"status IN ({','.join('?' * len(estados))})")
        params.extend(estados)

    if filters.get("hide_preparatory"):
        # No son subidas, y filtrados después ocuparían sitio en la página.
        ocultos = sorted(PREPARATORY_STATUSES)
        conditions.append(f"status NOT IN ({','.join('?' * len(ocultos))})")
        params.extend(ocultos)

    search = filters.get("search")
    if search:
        conditions.append("title LIKE ?")
        params.append(f"%{search}%")

    library_item_id = filters.get("library_item_id")
    if library_item_id is not None:
        conditions.append("library_item_id = ?")
        params.append(library_item_id)

    request_item_id = filters.get("request_item_id")
    if request_item_id is not None:
        conditions.append("request_item_id = ?")
        params.append(request_item_id)

    if filters.get("sin_trailer"):
        # El aviso de Subidas: las que salieron sin tráiler y nadie ha dicho todavía que lo puso a mano.
        conditions.append("trailer_estado = 'falta'")

    lote_id = filters.get("lote_id")
    if lote_id:
        # Solo la punta de cada cadena: un elemento del lote es su último intento, así que la lista, el «N de M»
        # y el resumen del lote cuentan lo mismo (services/lotes.py usa la misma condición).
        conditions.append(f"lote_id = ? AND {CONDICION_PUNTA}")
        params.append(lote_id)

    return (f"WHERE {' AND '.join(conditions)}" if conditions else ""), params


def list_upload_jobs(filters: dict | None = None, conn=None) -> list[dict]:
    """List upload jobs with optional filters: status, hide_preparatory, limit, offset, search."""
    own = conn is None
    conn = conn or _get_db_conn()
    try:
        filters = filters or {}
        where, params = _upload_jobs_where(filters)
        limit = int(filters.get("limit", 200))
        offset = int(filters.get("offset", 0))

        rows = conn.execute(
            f"SELECT * FROM upload_jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return rows_to_dicts(rows)
    finally:
        if own:
            conn.close()


def count_upload_jobs(filters: dict | None = None, conn=None) -> int:
    """Cuántas subidas cumplen los mismos filtros que list_upload_jobs, sin límite: el «M» de «N de M»."""
    own = conn is None
    conn = conn or _get_db_conn()
    try:
        where, params = _upload_jobs_where(filters or {})
        return conn.execute(f"SELECT COUNT(*) FROM upload_jobs {where}", params).fetchone()[0]
    finally:
        if own:
            conn.close()


def validate_upload_job_start(job_id: int, conn=None) -> dict:
    """Validate all preconditions for starting a job. Returns {valid, errors, warnings, job}."""
    own = conn is None
    conn = conn or _get_db_conn()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        job = get_upload_job(job_id, conn=conn)
        if not job:
            return {"valid": False, "errors": [f"Job {job_id} no encontrado."], "warnings": [], "job": None}

        status = job.get("status", "")
        if status in ("running",):
            errors.append(f"El job ya está en ejecución (status={status}).")
        elif status in ("completed",):
            errors.append("El job ya está completado. Crea un nuevo preflight para reintentar.")
        elif status in ("blocked", "failed"):
            errors.append(f"El job está en estado {status}. Crea un nuevo preflight.")
        elif status in ("cancelled",):
            errors.append("El job fue cancelado. Crea un nuevo preflight para reintentar.")
        elif status != "ready":
            errors.append(f"El job no está listo (status={status}). Solo se puede iniciar desde 'ready'.")

        prepared = job.get("prepared_path")
        source = job.get("source_path") or job.get("path") or ""

        active_path = prepared or source
        if not active_path:
            errors.append("No hay ruta de archivo registrada en el job.")
        else:
            from ..carpeta_subida import en_carpeta_de_subidas
            p = Path(active_path)
            if not p.exists():
                errors.append(f"El archivo no existe en disco: {active_path}")
            elif p.is_symlink() and not en_carpeta_de_subidas(active_path):
                # Los de la carpeta de subidas sí valen: con /config y /data en
                # montajes distintos no cabe un enlace duro y se hace simbólico.
                errors.append(f"El archivo es un symlink y no puede usarse: {active_path}")
            elif p.is_dir():
                # A season pack's prepared_path is the shared hardlink folder,
                # not a single file — nothing more to check here (each
                # episode inside it was already validated during the
                # queue's own integrity/nomenclature/remux stages).
                pass
            elif not p.is_file():
                errors.append(f"La ruta no es un archivo regular: {active_path}")
            elif p.suffix.lower() not in VIDEO_EXTENSIONS:
                warnings.append(f"El archivo no tiene extensión de vídeo reconocida: {p.suffix}")

        # Falls back to the bundled runtime's own default the same way
        # get_runtime_settings() always does — it needs no configuration.
        uploadrr_path = get_runtime_settings(conn)["uploadrr_path"]
        if not uploadrr_path:
            errors.append("Ruta de Uploadrr no configurada (uploadrr.path).")
        else:
            script = Path(uploadrr_path) / "upload.py"
            if not script.exists():
                errors.append(f"Script de Uploadrr no encontrado: {script}")

        destino = tracker_del_trabajo(job)
        if not trackers.tiene_clave(conn, destino):
            errors.append(f"API key de {destino.nombre} no configurada.")

        tmdb_key = get_setting_canonical(conn, "tmdb.api_key")
        if not tmdb_key:
            warnings.append("API key de TMDb no configurada.")

        if prepared:
            pass  # good
        elif source:
            warnings.append("Nomenclatura no aplicada: se usaría el archivo original.")

        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings, "job": job}
    finally:
        if own:
            conn.close()


def tracker_del_trabajo(job: dict) -> trackers.Tracker:
    """El tracker al que va una subida (`upload_jobs.tracker`); las de antes de la columna, eMuwarez."""
    return trackers.obtener(job.get("tracker") or trackers.POR_DEFECTO)


def subida_con_el_mismo_fichero(job: dict, conn) -> dict | None:
    """La subida completada del mismo elemento a otro tracker que siembra el mismo fichero de la carpeta de subidas
    (`prepared_path`) y tiene apuntado su torrent: la que da las piezas a una subida que reutiliza su preparación. La de
    «Subir también a…» (`copia_de_job_id`) primero: es la que el usuario eligió."""
    if not job.get("prepared_path") or not job.get("library_item_id"):
        return None
    return row_to_dict(conn.execute(
        "SELECT * FROM upload_jobs WHERE library_item_id = ? AND prepared_path = ? AND tracker != ? AND id != ? "
        "AND status = 'completed' AND result_torrent_id IS NOT NULL ORDER BY id = ? DESC, id LIMIT 1",
        (job["library_item_id"], job["prepared_path"], job.get("tracker") or trackers.POR_DEFECTO, job.get("id") or 0,
         job.get("copia_de_job_id") or 0),
    ).fetchone())


def base_torrent_de(uploadrr_path: str, prepared_path: str) -> Path:
    """El BASE.torrent que Uploadrr-EMU deja en su `tmp/<nombre>` (nombre = el del fichero o carpeta que sube)."""
    return Path(uploadrr_path) / "tmp" / Path(prepared_path).name / "BASE.torrent"


def base_reutilizable(uploadrr_path: str, prepared_path: str) -> bool:
    """Si Uploadrr-EMU reutilizará con --resume el BASE.torrent de `tmp/<nombre>` (torrentman.handle_torrent_creation):
    solo si su meta.json tiene marcado el paso `torrent`, que se marca después de comprobar que el fichero es un .torrent
    válido. Uno sin la marca (un `--base-desde` cortado entre escribirlo y marcarlo, un hash a medias) lo rehace."""
    base = base_torrent_de(uploadrr_path, prepared_path)
    try:
        pasos = json.loads((base.parent / "meta.json").read_text(encoding="utf-8")).get("pasos_hechos")
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(pasos, list) and "torrent" in pasos and base.is_file()


def base_desde_para(job: dict, conn, uploadrr_path: str) -> str | None:
    """`--base-desde` («EMU:<id>») para una subida que reutiliza la preparación de otra a otro tracker cuando su
    BASE.torrent ya no vale (el mantenimiento borra `tmp/<nombre>` a los pocos días, o quedó sin la marca `torrent`):
    sin él, Uploadrr-EMU lo volvería a hashear, y otro tamaño de pieza haría un torrent que no se siembra junto al otro.
    None si no hace falta."""
    if not job.get("reanuda_uploadrr") or not uploadrr_path or not job.get("prepared_path"):
        return None
    if base_reutilizable(uploadrr_path, job["prepared_path"]):
        return None
    otra = subida_con_el_mismo_fichero(job, conn)
    if otra is None:
        return None
    torrent_id = str(otra["result_torrent_id"]).strip()
    return f"{tracker_del_trabajo(otra).uploadrr}:{torrent_id}" if torrent_id.isdecimal() else None


def build_uploadrr_execution_command(job: dict, conn=None, *, solo_cliente: str | None = None) -> dict:
    """Build the actual command list for subprocess. Resolves paths from settings at runtime.

    Returns {"executable": str, "args": list[str], "cwd": str, "command_preview": str,
    "timeout": int, "runtime_type": str}. Does NOT include secrets in the returned dict.

    For runtime_type=uploadrr_emu (the default), builds the real Uploadrr-EMU
    argv (-tk/-mt/-tmdb/-ua/-c/-res) via uploadrr_contract.py, using only
    prepared_path/applied_path — never the raw job "source_path" field name.

    `solo_cliente`: el id de eMuwarez para «Reenviar a qBittorrent» (ver uploadrr_contract).
    """
    own = conn is None
    conn = conn or _get_db_conn()
    try:
        runtime = get_runtime_settings(conn)
        uploadrr_path = runtime["uploadrr_path"]
        script = str(Path(uploadrr_path) / "upload.py") if uploadrr_path else ""

        prepared = job.get("prepared_path")
        source = job.get("source_path") or job.get("path") or ""
        active_path = prepared or source
        using_prepared = bool(prepared)

        preflight_raw = job.get("uploadrr_command_json")
        cmd_data: dict = {}
        if preflight_raw:
            try:
                cmd_data = json.loads(preflight_raw)
            except Exception:
                pass

        category = cmd_data.get("category") or "movie"
        tmdb_id = cmd_data.get("tmdb_id") or job.get("tmdb_id")
        resolution = cmd_data.get("resolution") or ""
        title = cmd_data.get("title") or job.get("title") or "Sin título"
        season = cmd_data.get("season")
        episode = cmd_data.get("episode")

        # Los eventos solo si el checkout de Uploadrr-EMU los admite (trae su contrato); si no, se lanza como siempre.
        eventos = None
        if runtime["runtime_type"] == RUNTIME_EMU and job.get("id") and admite_eventos(uploadrr_path):
            eventos = str(ruta_eventos(job["id"], reenvio=bool(solo_cliente)))
        # Igual el config de esta subida: sin `--config` se prepara el config.yaml compartido (config.py).
        config = None
        if runtime["runtime_type"] == RUNTIME_EMU and job.get("id") and admite_config(uploadrr_path):
            config = str(ruta_config(job["id"], reenvio=bool(solo_cliente)))

        if runtime["runtime_type"] == RUNTIME_EMU:
            python_path = runtime["python_path"] or sys.executable
            full_argv = build_uploadrr_emu_command(
                python_path=python_path,
                uploadrr_script_path=script,
                prepared_path=active_path,
                torrent_name=title,
                tmdb_id=tmdb_id,
                category=category,
                resolution=resolution,
                season=season,
                episode=episode,
                release_format=cmd_data.get("release_format"),
                reanudar=bool(job.get("reanuda_uploadrr")),
                # Los decidió el paso que dejó el trabajo en 'ready' (upload_queue._marcar_lista).
                anonimo=bool(cmd_data.get("anon")),
                personal_release=bool(cmd_data.get("personal_release")),
                solo_cliente=solo_cliente,
                base_desde=None if solo_cliente else base_desde_para(job, conn, uploadrr_path),
                eventos=eventos,
                config=config,
                tracker=tracker_del_trabajo(job).uploadrr,
                # La guardó upload_queue._populate_ready con sus normas (reglas_tracker); eMuwarez nunca la lleva.
                variante=None if tracker_del_trabajo(job).id == trackers.EMU.id else cmd_data.get("variante"),
            )
            executable = full_argv[0]
            args = full_argv[1:]
        else:
            tracker = tracker_del_trabajo(job).uploadrr
            executable = sys.executable
            args = [script, active_path, "--tracker", tracker, "--category", category]
            if tmdb_id:
                args += ["--tmdb", str(tmdb_id)]
            if resolution:
                args += ["--resolution", resolution]

        preview = render_command_preview([executable] + args)

        return {
            "executable": executable,
            "args": args,
            "cwd": uploadrr_path,
            "command_preview": preview,
            "using_prepared_path": using_prepared,
            "source_path": active_path,
            "timeout": runtime["timeout"],
            "runtime_type": runtime["runtime_type"],
            "eventos": eventos,
            "config": config,
        }
    finally:
        if own:
            conn.close()
