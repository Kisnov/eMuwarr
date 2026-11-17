"""Si TMDb responde, para «Necesita atención» del Panel.

Uploadrr-EMU necesita TMDb para subir: con la clave caducada el Panel decía «Todo en orden» y la subida fallaba
después. Se prueba como mucho una vez por hora, como el perfil de eMuwarez (services/emu_perfil.py), y al cambiar la
clave en la siguiente carga del Panel.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from ..database import get_json_state, get_setting_canonical, set_json_state
from .integration_tests import test_tmdb_connection

ESTADO = "tmdb.salud"
DURACION = timedelta(hours=1)

# Con el Panel abierto en varias pestañas, solo una prueba TMDb: las demás esperan y se llevan lo que guarde.
_PROBANDO = threading.Lock()


def error_tmdb(conn: sqlite3.Connection) -> str | None:
    """El fallo de la última prueba de TMDb, o None si respondió."""
    huella = hashlib.sha256((get_setting_canonical(conn, "tmdb.api_key") or "").strip().encode()).hexdigest()
    with _PROBANDO:
        guardado = get_json_state(conn, ESTADO) or {}
        if guardado.get("huella") != huella or _caducado(guardado):
            r = test_tmdb_connection(conn=conn)
            guardado = {"huella": huella, "probado_at": datetime.now(timezone.utc).isoformat(),
                        "error": None if r["ok"] else r["message"]}
            set_json_state(conn, ESTADO, guardado)
            conn.commit()
    return guardado["error"]


def _caducado(guardado: dict) -> bool:
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(guardado["probado_at"]) >= DURACION
    except (KeyError, TypeError, ValueError):
        return True
