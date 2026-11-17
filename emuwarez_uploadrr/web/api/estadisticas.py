"""Estadísticas: la evolución de tu cuenta de eMuwarez a partir de las instantáneas diarias."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from ..dependencies import get_db, require_auth
from ..schemas import EstadisticasCuentaResponse, MisTorrentsResponse

router = APIRouter(prefix="/estadisticas", tags=["estadisticas"])


@router.get("/cuenta", response_model=EstadisticasCuentaResponse)
def estadisticas_cuenta(
    dias: int = Query(default=90, ge=1, le=730),
    current_user: dict = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
) -> EstadisticasCuentaResponse:
    """Una instantánea por día de tu cuenta, de la más antigua a la más reciente, y los ritmos de la última semana."""
    from emuwarez_uploadrr.services.perfil_historial import ritmos_cuenta, serie_cuenta
    return EstadisticasCuentaResponse(dias=dias, puntos=serie_cuenta(conn, dias), ritmos=ritmos_cuenta(conn))


@router.get("/mis-subidas", response_model=MisTorrentsResponse)
def estadisticas_mis_subidas(
    current_user: dict = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
) -> MisTorrentsResponse:
    """Tus subidas en eMuwarez de la última lectura (la tarea «Leer tus subidas en eMuwarez»), sin llamar al tracker."""
    from emuwarez_uploadrr.services.mis_torrents import resumen
    return MisTorrentsResponse(**resumen(conn))
