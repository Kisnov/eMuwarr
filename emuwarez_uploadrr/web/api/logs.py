"""Registros recientes de la aplicación — GET /api/logs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ..dependencies import require_auth

router = APIRouter(tags=["logs"])


@router.get("/logs")
def logs(
    limit: int = Query(500, ge=1, le=5000),
    level: str | None = Query(None),
    search: str | None = Query(None),
    since: int | None = Query(None, ge=0, description="`seq` de la última línea que el cliente ya tiene"),
    current_user: dict = Depends(require_auth),
) -> JSONResponse:
    """Recent application logs (newest first) from the in-memory capture buffer.

    Everything any module logs — plus every HTTP request — is captured here and
    mirrored to CONFIG_DIR/logs/app.log.

    Cada línea lleva un `seq` creciente. Con `since=<seq>` solo vuelven las posteriores (con los mismos `level` y
    `search`), también de la más nueva a la más vieja, y `truncated` dice si había más de `limit`: entonces `items`
    son las `limit` más nuevas y el cliente, que se ha quedado sin un tramo, debe volver a leer la lista entera.
    Sin `since` la respuesta es la de siempre.
    """
    from ..logging_setup import get_recent_logs
    if since is None:
        items = get_recent_logs(limit=limit, level=level, search=search)
        return JSONResponse({"items": items, "count": len(items)})
    items = get_recent_logs(limit=limit + 1, level=level, search=search, since=since)
    truncado = len(items) > limit
    items = items[:limit]
    return JSONResponse({"items": items, "count": len(items), "truncated": truncado})
