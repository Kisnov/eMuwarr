"""Estadísticas: la evolución de tu cuenta en cada tracker a partir de las instantáneas diarias."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ...services.trackers import TRACKERS, Tracker
from ..dependencies import get_db, require_auth
from ..schemas import EstadisticasCuentaResponse, MisTorrentsResponse, ObjetivosCuenta

router = APIRouter(prefix="/estadisticas", tags=["estadisticas"])


def _tracker(tracker: str = "emu") -> Tracker:
    """El tracker de `?tracker=` (eMuwarez sin él), o un 422 si no existe."""
    if tracker not in TRACKERS:
        raise HTTPException(status_code=422, detail=f"Tracker desconocido: {tracker}.")
    return TRACKERS[tracker]


@router.get("/cuenta", response_model=EstadisticasCuentaResponse)
def estadisticas_cuenta(
    dias: int = Query(default=90, ge=1, le=730),
    tracker: Tracker = Depends(_tracker),
    current_user: dict = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
) -> EstadisticasCuentaResponse:
    """Una instantánea por día de tu cuenta en `tracker`, de la más antigua a la más reciente, y los ritmos de la última
    semana."""
    from emuwarez_uploadrr.services.perfil_historial import ritmos_cuenta, serie_cuenta
    return EstadisticasCuentaResponse(dias=dias, puntos=serie_cuenta(conn, dias, tracker=tracker),
                                      ritmos=ritmos_cuenta(conn, tracker=tracker))


@router.get("/objetivos", response_model=ObjetivosCuenta)
def estadisticas_objetivos(
    current_user: dict = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
) -> ObjetivosCuenta:
    """Tu fecha de alta en eMuwarez y tus objetivos."""
    from emuwarez_uploadrr.services.objetivos_cuenta import leer
    return ObjetivosCuenta(**leer(conn))


@router.put("/objetivos", response_model=ObjetivosCuenta)
def guardar_objetivos(
    body: ObjetivosCuenta,
    current_user: dict = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
) -> ObjetivosCuenta:
    """Sustituye tu fecha de alta y tus objetivos por los que llegan."""
    from emuwarez_uploadrr.services.objetivos_cuenta import guardar
    return ObjetivosCuenta(**guardar(conn, body.fecha_alta, [o.model_dump() for o in body.objetivos]))


@router.get("/mis-subidas", response_model=MisTorrentsResponse)
def estadisticas_mis_subidas(
    tracker: Tracker = Depends(_tracker),
    current_user: dict = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
) -> MisTorrentsResponse:
    """Tus subidas en `tracker` de la última lectura (la tarea «Leer tus subidas»), sin llamar al tracker."""
    from emuwarez_uploadrr.services.mis_torrents import resumen
    return MisTorrentsResponse(**resumen(conn, tracker))
