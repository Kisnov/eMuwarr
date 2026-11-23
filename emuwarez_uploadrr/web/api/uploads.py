"""API endpoints for upload jobs and controlled execution (Phase 6C/6D/10F)."""
from __future__ import annotations

import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ...database import connect, db, init_db
from ...services.library import content_display_title
from ...services.upload_execution import (
    get_upload_job as exec_get_job,
    list_upload_jobs as exec_list_jobs,
    count_upload_jobs,
    get_upload_job_logs,
    get_upload_job_artifacts,
    start_upload_job,
    cancel_upload_job,
    delete_upload_job,
    retry_upload_job,
    DELETE_CONFIRMATION_TOKEN,
    compute_progress_phase,
    puede_reenviar_qbit,
    reenviar_a_qbittorrent,
)
from ...services.subida_pasos import NOMBRES, pasos_de_subida, reanudar_desde, subpasos_que_reutilizaria
from ...services import lotes, subir_tambien, trackers
from ...services.mis_torrents import lectura_para_moderacion, pendiente_de_moderacion
from ...services.lotes import PATRON_LOTE_ID, contar_por_lote
from ...services.trailer_check import marcar_trailer_a_mano
from ...services.upload_queue import get_queue_positions
from ..dependencies import require_auth
from ..runtime import _db_path
from ..schemas import (
    LoteAccionResponse,
    LoteBorrarResponse,
    LoteCancelarBody,
    LoteReintentarBody,
    LoteResumen,
    TrailerAManoBody,
    TrailerAManoResponse,
    UploadLoteBody,
    UploadLoteResponse,
    OkResponse,
    UploadArtifact,
    UploadArtifactsResponse,
    UploadDeleteRequest,
    UploadJobDetail,
    UploadJobLog,
    UploadJobLogsResponse,
    UploadQueueBody,
    UploadQueueResponse,
    UploadRetryRequest,
    UploadStartRequest,
    UploadStartResult,
    TrackerParaSubir,
    ReglasDeTracker,
    ElegibilidadSubida,
    SubirTambienBody,
    SubirTambienCandidata,
    SubirTambienCandidatas,
    SubirTambienResponse,
)

router = APIRouter(tags=["uploads"])


def _get_conn():
    conn = connect(_db_path())
    init_db(conn)
    return conn


def _extract_runtime_type(j: dict) -> str | None:
    raw = j.get("uploadrr_command_json")
    if not raw:
        return None
    try:
        return json.loads(raw).get("runtime_type")
    except Exception:
        return None


def _extract_command_preview(j: dict) -> str | None:
    """Prefer the safe (redacted) preview captured at execution start; fall
    back to the preflight preview (never contains secrets either way)."""
    safe_raw = j.get("safe_command_json")
    if safe_raw:
        try:
            preview = json.loads(safe_raw).get("args_preview")
            if preview:
                return preview
        except Exception:
            pass
    preflight_raw = j.get("uploadrr_command_json")
    if preflight_raw:
        try:
            return json.loads(preflight_raw).get("command_preview")
        except Exception:
            return None
    return None


def _extract_json_dict(j: dict, key: str) -> dict | None:
    raw = j.get(key)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _con_ficha(j: dict, ficha: dict | None, **kw) -> UploadJobDetail:
    """El detalle con los campos de su ficha de la biblioteca o, si ya no está, con los que la subida guardó al
    borrarse (database.borrar_biblioteca)."""
    f = ficha or _extract_json_dict(j, "library_snapshot_json") or {}
    return _job_to_detail(
        j, f.get("item_type"), f.get("poster_url") or None, f.get("backdrop_url") or None,
        f.get("instance_type") or None, f.get("instance_name") or None, f.get("request_status") or None,
        f.get("year") or None, f.get("instance_id") or None, f.get("tmdb_id") or None, f.get("title_slug") or None,
        release_groups=f.get("release_groups") or [],
        **kw,
    )


def _job_to_detail(
    j: dict,
    media_type: str | None = None,
    poster_url: str | None = None,
    backdrop_url: str | None = None,
    instance_type: str | None = None,
    instance_name: str | None = None,
    request_status: str | None = None,
    year: int | None = None,
    instance_id: int | None = None,
    tmdb_id: str | None = None,
    title_slug: str | None = None,
    display_title: str | None = None,
    queue_position: int | None = None,
    queue_total: int | None = None,
    release_groups: list[str] | None = None,
    lote_total: int | None = None,
    pendiente_moderacion: bool = False,
) -> UploadJobDetail:
    prepared = j.get("prepared_path")
    source = j.get("source_path") or j.get("path")
    using_prepared = bool(prepared) if (prepared or source) else None
    tracker = trackers.TRACKERS.get(j.get("tracker") or trackers.POR_DEFECTO, trackers.EMU)
    return UploadJobDetail(
        media_type=media_type,
        poster_url=poster_url,
        backdrop_url=backdrop_url,
        year=year,
        display_title=display_title,
        release_groups=release_groups or [],
        instance_type=instance_type,
        instance_name=instance_name,
        instance_id=instance_id,
        tmdb_id=tmdb_id,
        title_slug=title_slug,
        request_status=request_status,
        id=j.get("id"),
        library_item_id=j.get("library_item_id"),
        request_item_id=j.get("request_item_id"),
        request_title=j.get("request_title"),
        request_url=j.get("request_url"),
        status=j.get("status", "draft"),
        stage=j.get("stage"),
        title=j.get("title"),
        source_path=source,
        prepared_path=prepared,
        using_prepared_path=using_prepared,
        error_message=j.get("error_message"),
        exit_code=j.get("exit_code"),
        process_id=j.get("process_id"),
        started_at=j.get("started_at"),
        finished_at=j.get("finished_at"),
        created_at=j.get("created_at"),
        updated_at=j.get("updated_at"),
        # Phase 6D
        result_url=j.get("result_url"),
        result_torrent_id=j.get("result_torrent_id"),
        result_tracker=j.get("result_tracker"),
        result_category=j.get("result_category"),
        duration_seconds=j.get("duration_seconds"),
        retry_count=j.get("retry_count") or 0,
        retry_of_job_id=j.get("retry_of_job_id"),
        safe_command_json=j.get("safe_command_json"),
        last_success_at=j.get("last_success_at"),
        last_error_at=j.get("last_error_at"),
        last_log_at=j.get("last_log_at"),
        runtime_type=_extract_runtime_type(j),
        command_preview=_extract_command_preview(j),
        progress_phase=compute_progress_phase(j.get("status", ""), j.get("stage")),
        tracker=tracker.id,
        tracker_nombre=tracker.nombre,
        pasos=pasos_de_subida(j.get("status"), j.get("stage"), j.get("etapa_parada"), j.get("pasos_uploadrr_json"),
                              tracker.nombre),
        reintento_reutiliza=subpasos_que_reutilizaria(j.get("status"), j.get("etapa_parada"), j.get("pasos_uploadrr_json")),
        reintento_desde=_reintento_desde(j),
        reintento_desde_nombre=NOMBRES.get(_reintento_desde(j) or ""),
        queue_position=queue_position,
        queue_total=queue_total,
        lote_id=j.get("lote_id"),
        lote_total=lote_total if j.get("lote_id") else None,
        trailer_estado=j.get("trailer_estado"),
        trailer_url=j.get("trailer_url"),
        reenvio_qbit_estado=j.get("reenvio_qbit_estado"),
        reenvio_qbit_at=j.get("reenvio_qbit_at"),
        puede_reenviar_qbit=puede_reenviar_qbit(j),
        copia_de_job_id=j.get("copia_de_job_id"),
        pendiente_moderacion=pendiente_moderacion,
        nomenclature_snapshot=_extract_json_dict(j, "nomenclature_snapshot_json"),
        remux_snapshot=_extract_json_dict(j, "remux_snapshot_json"),
        checks_snapshot=_extract_json_dict(j, "checks_snapshot_json"),
        progress=_extract_json_dict(j, "progress_json"),
    )


def _reintento_desde(j: dict) -> Optional[str]:
    return reanudar_desde(j.get("status"), j.get("etapa_parada"))


@router.post("/library/{item_id}/upload/checks")
def upload_checks(
    item_id: int,
    scope: str = Query(default="all", pattern="^(all|content|environment)$"),
    provider: str | None = Query(default=None),
    source: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    title: str | None = Query(default=None),
    idiomas: str | None = Query(default=None),
    tracker: str | None = Query(default=None),
    current_user: dict = Depends(require_auth),
) -> dict:
    """Run the pre-upload checks (fail-fast, eMuwarez last) for the 'Subida a
    eMuwarez' step. Returns each check's result + a `ready` flag. Can take
    ~30s (real network calls) — poll GET .../checks/progress concurrently to
    show each check landing as it completes instead of a static wait.

    `scope` parte la pasada en dos para una selección de varias temporadas o
    capítulos: "content" son las del elemento y van una por elemento;
    "environment" son las compartidas (directorio de salida, qBittorrent,
    hosts de imágenes, eMuwarez) y se piden UNA vez para toda la tanda, que
    con 30 capítulos es la diferencia entre 4 comprobaciones de red y 120.
    "all" (por defecto) las hace todas, que es lo que usa un solo elemento.

    Una temporada acepta además lo que el asistente editó a mano para el pack (`source`, `tag`, `title` e
    `idiomas`, un JSON {"2": "spa"}): así el `nombre` que devuelve es el que saldrá. Sin ellos, todo igual. Un fichero
    suelto, solo `idiomas`: los elegidos en «Ver pistas», que cuentan para sus pistas sin idioma y sus normas.

    `tracker` (emu, nobs, mnv): de quién son las normas de idioma (la comprobación «spanish» y la de cada capítulo de
    un pack). Sin él, eMuwarez; uno desconocido es un 422."""
    from emuwarez_uploadrr.services.upload_checks import run_upload_checks
    tracker = _tracker_valido(tracker)
    elegidos = _idiomas(idiomas)
    ajustes = {k: v.strip() for k, v in (("source", source), ("tag", tag), ("title", title)) if v and v.strip()}
    if elegidos:
        ajustes["idiomas"] = elegidos
    with _get_conn() as conn:
        # `provider` de una temporada: el elegido en el asistente ("" = Sin proveedor).
        return run_upload_checks(conn, item_id, scope=scope,
                                 provider_override=None if provider is None else provider.strip(),
                                 **({"ajustes": ajustes} if ajustes else {}),
                                 **({} if tracker == trackers.POR_DEFECTO else {"tracker": tracker}))


def _idiomas(crudo: str | None) -> dict | None:
    """Los idiomas elegidos en «Ver pistas» ({"2": "spa"}, por posición) de la query; None si no viene uno válido."""
    try:
        elegidos = json.loads(crudo) if crudo else None
    except ValueError:
        return None
    return elegidos if isinstance(elegidos, dict) else None


def _tracker_valido(tracker: str | None) -> str:
    """El id del tracker pedido (eMuwarez sin él), o un 422 si no existe."""
    try:
        return trackers.obtener(tracker or trackers.POR_DEFECTO).id
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Tracker desconocido: {tracker}.") from None


@router.get("/library/{item_id}/upload/reglas", response_model=ReglasDeTracker)
def reglas_para_subir(
    item_id: int,
    tracker: str = Query(...),
    idiomas: str | None = Query(default=None),
    current_user: dict = Depends(require_auth),
):
    """Si las normas de idioma de `tracker` admiten el elemento, por qué, y la categoría que le toca allí
    (services/reglas_tracker.py; una temporada, capítulo a capítulo). Barato, sin red: el Resumen lo pide para cada
    tracker que ofrece. `idiomas` ({"2": "spa"}): los elegidos en «Ver pistas» para las pistas sin idioma."""
    from ...services.library import get_library_item
    from ...services.reglas_tracker import reglas_para_subir as reglas
    tracker = _tracker_valido(tracker)
    with _get_conn() as conn:
        item = get_library_item(conn, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Elemento no encontrado.")
        r = reglas(conn, item, tracker, idiomas=_idiomas(idiomas))
    return ReglasDeTracker(tracker=tracker, permitido=r.permitido, motivo=r.motivo, variante=r.variante)


@router.get("/library/{item_id}/upload/checks/progress")
def upload_checks_progress(
    item_id: int,
    current_user: dict = Depends(require_auth),
) -> dict:
    """Live progress of a checks run in flight for this item — {checks, done,
    ready}, `checks` growing one at a time. Meant to be polled while the
    POST above is running; returns the empty/initial shape if none is running."""
    from emuwarez_uploadrr.services.upload_checks import get_checks_progress
    return get_checks_progress(item_id)


@router.get("/library/{item_id}/upload/elegibilidad", response_model=ElegibilidadSubida)
def elegibilidad_para_subir(item_id: int, current_user: dict = Depends(require_auth)):
    """Qué trackers de los que se puede subir admiten el elemento por sus normas y, de una temporada, cada capítulo
    (services/reglas_tracker.py::elegibilidad). Sin red: el asistente lo pide al abrirse, con más de un tracker."""
    from ...services.library import get_library_item
    from ...services.reglas_tracker import elegibilidad
    with _get_conn() as conn:
        item = get_library_item(conn, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Elemento no encontrado.")
        return elegibilidad(conn, item)


@router.get("/upload/trackers", response_model=list[TrackerParaSubir])
def trackers_para_subir(current_user: dict = Depends(require_auth)):
    """Los trackers que el Resumen ofrece: eMuwarez y los que tienen su clave puesta."""
    with _get_conn() as conn:
        return [TrackerParaSubir(id=t.id, nombre=t.nombre) for t in trackers.para_subir(conn)]


@router.post("/library/{item_id}/upload/queue", response_model=UploadQueueResponse)
def queue_upload(
    item_id: int,
    body: UploadQueueBody,
    current_user: dict = Depends(require_auth),
):
    """Queue "Subir a eMuwarez": creates the upload_jobs row in status='queued'
    and returns immediately (the caller navigates to Subidas right after).
    The real work — integrity check, nomenclature rename, track remux, then
    the actual Uploadrr start — runs server-side at once, or as soon as a
    "Subidas en paralelo" slot frees up; it can be cancelled at any step
    (see services/upload_queue.py)."""
    from ...services.upload_queue import queue_upload_for_item
    overrides = None
    if body.remux_overrides:
        overrides = {}
        for raw_id, ov in body.remux_overrides.items():
            try:
                overrides[int(raw_id)] = {"new_name": ov.new_name, "default_after": ov.default_after,
                                          "language": ov.language}
            except (TypeError, ValueError):
                continue
    checks = body.checks.model_dump() if body.checks else None
    tracker = body.tracker or trackers.POR_DEFECTO
    with _get_conn() as conn:
        motivo = trackers.motivo_para_no_subir(conn, tracker)
        if motivo:
            raise HTTPException(status_code=422, detail=motivo)
        result = queue_upload_for_item(
            item_id, request_item_id=body.request_item_id,
            remux_overrides=overrides, remux_snapshot=body.remux_snapshot, checks=checks, conn=conn,
            source_override=body.source, provider_override=body.provider, tag_override=body.tag,
            title_override=body.title, idiomas_audio=body.idiomas, lote_id=body.lote_id,
            anonimo=body.anonimo, personal_release=body.personal_release, tracker=tracker,
        )

    job = result.get("job")
    return UploadQueueResponse(
        status=result.get("status", "blocked"),
        job=_job_to_detail(job) if job else None,
        errors=result.get("errors", []),
    )


@router.get("/uploads", response_model=list[UploadJobDetail])
def get_uploads(
    status: str | None = Query(default=None),
    hide_preparatory: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None),
    library_item_id: int | None = Query(default=None),
    request_item_id: int | None = Query(default=None),
    lote_id: str | None = Query(default=None, pattern=PATRON_LOTE_ID),
    sin_trailer: bool = Query(default=False),
    current_user: dict = Depends(require_auth),
):
    filters = {
        "status": status,
        "hide_preparatory": hide_preparatory,
        "limit": limit,
        "offset": offset,
        "search": search,
        "library_item_id": library_item_id,
        "request_item_id": request_item_id,
        "lote_id": lote_id,
        "sin_trailer": sin_trailer,
    }
    queue_positions = get_queue_positions()
    with _get_conn() as conn:
        jobs = exec_list_jobs(filters=filters, conn=conn)
        # Las fichas de todas las subidas en una sola consulta.
        from ...services.library import library_display_fields
        fichas = library_display_fields({j.get("library_item_id") for j in jobs}, conn)
        # El «Lote · N» de cada fila, en una sola consulta para toda la página.
        lotes = contar_por_lote(conn, (j.get("lote_id") for j in jobs))
        lectura = lectura_para_moderacion(conn)
    return [
        _con_ficha(
            j, fichas.get(j.get("library_item_id")),
            queue_position=queue_positions.get(j.get("id"), (None, None))[0],
            queue_total=queue_positions.get(j.get("id"), (None, None))[1],
            lote_total=lotes.get(j.get("lote_id")),
            pendiente_moderacion=pendiente_de_moderacion(j, lectura),
        )
        for j in jobs
    ]


# Antes de /uploads/{job_id}: si no, "count" se leería como un id.
@router.get("/uploads/count")
def count_uploads(
    status: str | None = Query(default=None),
    hide_preparatory: bool = Query(default=False),
    search: str | None = Query(default=None),
    library_item_id: int | None = Query(default=None),
    request_item_id: int | None = Query(default=None),
    lote_id: str | None = Query(default=None, pattern=PATRON_LOTE_ID),
    sin_trailer: bool = Query(default=False),
    current_user: dict = Depends(require_auth),
) -> dict:
    """Cuántas subidas hay con esos filtros, sin el límite de la lista: el «M» de «N de M»."""
    filters = {
        "status": status,
        "hide_preparatory": hide_preparatory,
        "search": search,
        "library_item_id": library_item_id,
        "request_item_id": request_item_id,
        "lote_id": lote_id,
        "sin_trailer": sin_trailer,
    }
    with _get_conn() as conn:
        return {"total": count_upload_jobs(filters=filters, conn=conn)}


@router.post("/uploads/lote", response_model=UploadLoteResponse)
def encolar_lote(body: UploadLoteBody, current_user: dict = Depends(require_auth)):
    """«Subir por lotes»: encola cada elemento en el orden dado, con la misma etiqueta de lote. El que no se puede
    subir se salta con su motivo; si falta algo que haría fallar a todos (claves, Uploadrr-EMU), 409 y nada encolado."""
    with db(_db_path()) as conn:
        try:
            return UploadLoteResponse(**lotes.encolar_lote(conn, [e.model_dump() for e in body.elementos]))
        except lotes.LoteRechazado as e:
            raise HTTPException(status_code=409, detail=e.motivos) from e


def _tracker_para_subir(t: trackers.Tracker) -> TrackerParaSubir:
    return TrackerParaSubir(id=t.id, nombre=t.nombre)


@router.get("/uploads/subir-tambien", response_model=SubirTambienCandidatas)
def candidatas_subir_tambien(current_user: dict = Depends(require_auth)):
    """«Subir también a…»: las subidas a eMuwarez que se pueden subir también a N.O.B.S o Milnueve, y a cuáles."""
    with _get_conn() as conn:
        destinos = subir_tambien.destinos(conn)
        filas = subir_tambien.candidatas(conn)
    return SubirTambienCandidatas(
        trackers=[_tracker_para_subir(t) for t in destinos],
        subidas=[SubirTambienCandidata(
            job_id=f["job"]["id"], title=f["job"].get("title"),
            nombre=os.path.basename(f["job"]["prepared_path"]), result_url=f["job"].get("result_url"),
            finished_at=f["job"].get("finished_at"), trackers=[_tracker_para_subir(t) for t in f["trackers"]],
        ) for f in filas],
    )


@router.post("/uploads/subir-tambien", response_model=SubirTambienResponse)
def encolar_subir_tambien(body: SubirTambienBody, current_user: dict = Depends(require_auth)):
    """«Subir también a…»: una subida por cada subida a eMuwarez y tracker elegidos, con el mismo fichero y el mismo
    torrent. La que no se puede (sin el torrent entero en qBittorrent, ya subida a ese tracker…) se salta con su
    motivo; si falta algo que haría fallar a todas (la clave del tracker), 422 y nada encolado."""
    with db(_db_path()) as conn:
        try:
            return SubirTambienResponse(**subir_tambien.subir_tambien(conn, body.job_ids, body.trackers))
        except subir_tambien.SubirTambienRechazado as e:
            raise HTTPException(status_code=422, detail=" ".join(e.args[0])) from e


def _resumen_o_404(conn, lote_id: str) -> LoteResumen:
    resumen = lotes.resumen_de_lote(conn, lote_id)
    if resumen is None:
        raise HTTPException(status_code=404, detail="Lote no encontrado.")
    return LoteResumen(**resumen)


@router.get("/uploads/lotes/{lote_id}", response_model=LoteResumen)
def get_lote(lote_id: str = Path(pattern=PATRON_LOTE_ID), current_user: dict = Depends(require_auth)):
    """Cuántos elementos del lote hay en cada estado. Solo lee la base local."""
    with db(_db_path()) as conn:
        return _resumen_o_404(conn, lote_id)


@router.post("/uploads/lotes/{lote_id}/cancelar", response_model=LoteAccionResponse)
def cancelar_lote(
    body: LoteCancelarBody,
    lote_id: str = Path(pattern=PATRON_LOTE_ID),
    current_user: dict = Depends(require_auth),
):
    """Cancela las que esperan en la cola (y, con solo_pendientes=false, también las que ya están en marcha)."""
    with db(_db_path()) as conn:
        _resumen_o_404(conn, lote_id)
        hecho = lotes.cancelar_lote(conn, lote_id, solo_pendientes=body.solo_pendientes)
        return LoteAccionResponse(**hecho, resumen=_resumen_o_404(conn, lote_id))


@router.post("/uploads/lotes/{lote_id}/reintentar", response_model=LoteAccionResponse)
def reintentar_lote(
    body: LoteReintentarBody,
    lote_id: str = Path(pattern=PATRON_LOTE_ID),
    current_user: dict = Depends(require_auth),
):
    """Reencola cada elemento fallido o cancelado del lote, en su orden."""
    with db(_db_path()) as conn:
        _resumen_o_404(conn, lote_id)
        hecho = lotes.reintentar_fallidas(conn, lote_id, desde_el_principio=body.desde_el_principio)
        return LoteAccionResponse(**hecho, resumen=_resumen_o_404(conn, lote_id))


@router.post("/uploads/lotes/{lote_id}/borrar", response_model=LoteBorrarResponse)
def borrar_lote(
    body: UploadDeleteRequest,
    lote_id: str = Path(pattern=PATRON_LOTE_ID),
    current_user: dict = Depends(require_auth),
):
    """«Borrar lote»: borra cada elemento terminado del lote como «Borrar» en una fila suelta (con su cadena de
    intentos entera); los que siguen en cola o en marcha no se tocan. Pide el mismo token que el borrado suelto."""
    if body.confirmation != DELETE_CONFIRMATION_TOKEN:
        raise HTTPException(
            status_code=400,
            detail={"message": f"Confirmación requerida: {DELETE_CONFIRMATION_TOKEN}", "error": "confirmation_required"},
        )
    with db(_db_path()) as conn:
        _resumen_o_404(conn, lote_id)
        hecho = lotes.borrar_lote(conn, lote_id)
        resumen = lotes.resumen_de_lote(conn, lote_id)
        return LoteBorrarResponse(**hecho, resumen=LoteResumen(**resumen) if resumen else None)


@router.get("/uploads/{job_id}", response_model=UploadJobDetail)
def get_upload(job_id: int, current_user: dict = Depends(require_auth)):
    with _get_conn() as conn:
        job = exec_get_job(job_id, conn=conn)
        if not job:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        queue_position, queue_total = get_queue_positions().get(job_id, (None, None))
        from ...services.library import library_display_fields_one
        row = library_display_fields_one(job["library_item_id"], conn) if job.get("library_item_id") else None
        display_title = content_display_title(conn, row) if row else None
        lote_total = contar_por_lote(conn, [job["lote_id"]]).get(job["lote_id"]) if job.get("lote_id") else None
        pendiente = pendiente_de_moderacion(job, lectura_para_moderacion(conn))
        ofrecidos = subir_tambien.ofrecidos(conn, job)
    detalle = _con_ficha(job, row, display_title=display_title, queue_position=queue_position, queue_total=queue_total,
                         lote_total=lote_total, pendiente_moderacion=pendiente)
    detalle.subir_tambien_a = [TrackerParaSubir(id=t.id, nombre=t.nombre) for t in ofrecidos]
    return detalle


@router.get("/uploads/{job_id}/logs", response_model=UploadJobLogsResponse)
def get_upload_logs(
    job_id: int,
    # A season with many episodes (real Uploadrr-EMU output per chapter —
    # hashing/screenshot progress, per-host retries) easily produces many
    # more than 2000 lines; the old cap silently hid everything before the
    # last 2000, with no way for "Ver Logs" to ever show the full activity.
    limit: int = Query(default=500, ge=1, le=200000),
    offset: int = Query(default=0, ge=0),
    tail: bool = Query(default=False),
    # Solo lo posterior a esa línea (su `sequence`): quien sigue la subida en vivo no baja el registro entero.
    after_sequence: int | None = Query(default=None, ge=0),
    current_user: dict = Depends(require_auth),
):
    with _get_conn() as conn:
        job = exec_get_job(job_id, conn=conn)
        if not job:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        result = get_upload_job_logs(
            job_id, limit=limit, offset=offset, tail=tail, after_sequence=after_sequence, conn=conn,
        )

    logs = [
        UploadJobLog(
            id=log.get("id"),
            upload_job_id=log["upload_job_id"],
            level=log["level"],
            message=log["message"],
            stream=log.get("stream"),
            created_at=log["created_at"],
            sequence=log.get("sequence"),
        )
        for log in result.get("logs", [])
    ]
    return UploadJobLogsResponse(
        job_id=job_id,
        logs=logs,
        total=result.get("total", 0),
        limit=limit,
        offset=offset,
    )


@router.get("/uploads/{job_id}/artifacts", response_model=UploadArtifactsResponse)
def get_upload_artifacts(job_id: int, current_user: dict = Depends(require_auth)):
    with _get_conn() as conn:
        job = exec_get_job(job_id, conn=conn)
        if not job:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        artifacts = get_upload_job_artifacts(job_id, conn=conn)

    return UploadArtifactsResponse(
        job_id=job_id,
        artifacts=[
            UploadArtifact(
                id=a.get("id"),
                upload_job_id=a["upload_job_id"],
                artifact_type=a["artifact_type"],
                name=a.get("name"),
                path=a.get("path"),
                url=a.get("url"),
                value=a.get("value"),
                created_at=a["created_at"],
            )
            for a in artifacts
        ],
    )


@router.post("/uploads/{job_id}/start", response_model=UploadStartResult)
def start_upload(
    job_id: int,
    body: UploadStartRequest,
    current_user: dict = Depends(require_auth),
):
    with _get_conn() as conn:
        result = start_upload_job(
            job_id,
            dry_run=body.dry_run,
            start=body.start,
            confirmation=body.confirmation,
            conn=conn,
        )

    if result.get("error") in ("confirmation_required", "validation_failed", "blocked", "unsupported_runtime"):
        raise HTTPException(
            status_code=400,
            detail={
                "message": result.get("message") or "Bloqueado",
                "errors": result.get("errors", []),
                "error": result.get("error"),
            },
        )

    return UploadStartResult(
        job_id=job_id,
        action=result.get("action", "unknown"),
        status=result.get("status", "unknown"),
        stage=result.get("stage"),
        dry_run=result.get("dry_run", True),
        command_preview=result.get("command_preview"),
        message=result.get("message"),
        error=result.get("error"),
    )


@router.post("/uploads/{job_id}/retry", response_model=UploadStartResult)
def retry_upload(
    job_id: int,
    body: UploadRetryRequest,
    current_user: dict = Depends(require_auth),
):
    result = retry_upload_job(
        job_id,
        dry_run=body.dry_run,
        start=body.start,
        confirmation=body.confirmation,
    )

    if result.get("error") in ("confirmation_required", "validation_failed", "blocked", "invalid_status", "not_found"):
        status_code = 404 if result.get("error") == "not_found" else 400
        raise HTTPException(
            status_code=status_code,
            detail={
                "message": result.get("message") or "Bloqueado",
                "error": result.get("error"),
            },
        )

    new_job_id = result.get("new_job_id") or job_id
    return UploadStartResult(
        job_id=new_job_id,
        action=result.get("action", "unknown"),
        status=result.get("status", "unknown"),
        stage=result.get("stage"),
        dry_run=result.get("dry_run", True),
        command_preview=result.get("command_preview"),
        message=result.get("message"),
        error=result.get("error"),
    )


@router.post("/uploads/{job_id}/retry-queued", response_model=UploadQueueResponse)
def retry_upload_queued(job_id: int, desde_el_principio: bool = False, current_user: dict = Depends(require_auth)):
    """Retry straight into the same queued flow "Subir a eMuwarez" uses — no confirmation token, since it only ever creates a
    brand new job row (the original failed/cancelled job is never touched)."""
    from ...services.upload_queue import queue_retry_for_job
    with _get_conn() as conn:
        result = queue_retry_for_job(job_id, conn=conn, desde_el_principio=desde_el_principio)

    job = result.get("job")
    if result.get("status") != "queued":
        raise HTTPException(
            status_code=400,
            detail={"message": "; ".join(result.get("errors", [])) or "No se pudo reintentar."},
        )
    return UploadQueueResponse(
        status=result.get("status", "blocked"),
        job=_job_to_detail(job) if job else None,
        errors=result.get("errors", []),
    )


@router.post("/uploads/{job_id}/cancel")
def cancel_upload(job_id: int, current_user: dict = Depends(require_auth)):
    with _get_conn() as conn:
        result = cancel_upload_job(job_id, conn=conn)
    return result


@router.post("/uploads/{job_id}/trailer-a-mano", response_model=TrailerAManoResponse)
def trailer_a_mano(job_id: int, body: TrailerAManoBody, current_user: dict = Depends(require_auth)):
    """«Ya lo he añadido»: la subida deja de avisar de que le falta el tráiler (`puesto=false` lo deshace)."""
    with _get_conn() as conn:
        result = marcar_trailer_a_mano(job_id, puesto=body.puesto, conn=conn)
    if not result["ok"]:
        if result["error"] == "not_found":
            raise HTTPException(status_code=404, detail={"message": "Subida no encontrada.", "error": "not_found"})
        raise HTTPException(
            status_code=409,
            detail={"message": "Esta subida no tiene ningún aviso de tráiler que marcar.", "error": result["error"]},
        )
    return TrailerAManoResponse(trailer_estado=result["trailer_estado"])


@router.post("/uploads/{job_id}/reenviar-qbit", response_model=OkResponse)
def reenviar_qbit(job_id: int, current_user: dict = Depends(require_auth)):
    """«Reenviar a qBittorrent»: vuelve a añadir el torrent de eMuwarez de una subida completada, sin subir nada."""
    with _get_conn() as conn:
        result = reenviar_a_qbittorrent(job_id, conn=conn)
    if not result["ok"]:
        # El texto tal cual: la interfaz lo enseña junto al botón.
        raise HTTPException(status_code=404 if result["error"] == "not_found" else 409, detail=result["message"])
    return OkResponse(message=result["message"])


@router.post("/uploads/{job_id}/delete", response_model=OkResponse)
def delete_upload(
    job_id: int,
    body: UploadDeleteRequest,
    current_user: dict = Depends(require_auth),
):
    with _get_conn() as conn:
        result = delete_upload_job(job_id, confirmation=body.confirmation, conn=conn)

    if not result.get("ok"):
        status_code = 404 if result.get("error") == "not_found" else 400
        raise HTTPException(
            status_code=status_code,
            detail={"message": result.get("message") or "No se pudo borrar la subida.", "error": result.get("error")},
        )

    return OkResponse(message=result.get("message", "Subida eliminada."))
