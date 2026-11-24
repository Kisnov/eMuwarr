"""Tu cuenta de eMuwarez, para el Panel y la barra superior, y la de cada otro tracker con clave, para el Panel.

Sale de GET /api/user y se guarda una hora: la clave la comparten Sonarr,
Radarr, qui, autobrr y las subidas, con 30 llamadas por minuto entre todos.
Mensajes y notificaciones no están en la API (solo en la web, con sesión), así
que de ellos la interfaz solo pone el enlace.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from ..database import get_json_state, set_json_state
from ..integrations.base import IntegrationError, texto_de_error
from ..integrations.emu import emu_get_user
from .avisos_cuenta import avisar_cambios
from .perfil_historial import guardar_instantanea
from .settings import set_setting
from .trackers import EMU, TRACKERS, Tracker, tiene_clave
from .settings import ajuste_de_tracker

logger = logging.getLogger(__name__)

PERFIL_STATE_KEY = "emu.perfil"  # el de eMuwarez; el de otro tracker es `<id>.perfil`
DURACION = timedelta(hours=1)
# «Actualizar» a mano, como mucho una vez por minuto, igual que «Probar conexión».
ESPERA_ACTUALIZAR = timedelta(minutes=1)
CAMPOS = ("username", "group", "uploaded", "downloaded", "ratio", "buffer",
          "seeding", "leeching", "seedbonus", "hit_and_runs")

# Con el Panel abierto en varias pestañas, solo una va a eMuwarez: las demás esperan y se llevan lo que guarde.
_PIDIENDO = threading.Lock()


def perfiles(conn: sqlite3.Connection, *, actualizar: bool = False) -> dict[str, Any]:
    """El perfil de eMuwarez con los de los demás trackers con clave en `otros`, de uno en uno."""
    emu = perfil_emu(conn, actualizar=actualizar)
    return {**emu, "otros": [perfil_emu(conn, actualizar=actualizar, tracker=t)
                             for t in TRACKERS.values() if t is not EMU and tiene_clave(conn, t)]}


def perfil_emu(conn: sqlite3.Connection, *, actualizar: bool = False, tracker: Tracker = EMU) -> dict[str, Any]:
    """El perfil guardado de `tracker`; antes lo vuelve a pedir si el último intento tiene más de una hora
    (o de un minuto, con `actualizar`). Un fallo se guarda en `error` junto al último perfil bueno."""
    clave = (ajuste_de_tracker(conn, "api_key", tracker.id) or "").strip()
    guardado: dict[str, Any] = {}
    if clave:
        with _PIDIENDO:
            guardado = get_json_state(conn, f"{tracker.id}.perfil") or {}
            if guardado.get("huella") != _huella(clave):
                guardado = {}  # era de otra clave: se pide ya con la nueva
            if _caducado(guardado, ESPERA_ACTUALIZAR if actualizar else DURACION):
                guardado = _pedir(conn, clave, guardado, tracker)
    perfil = guardado.get("perfil") or {}
    return {
        "configured": bool(clave),
        "tracker": tracker.id,
        "nombre": tracker.nombre,
        "url": tracker.url,
        **{campo: perfil.get(campo) for campo in CAMPOS},
        # Sin perfil todavía, el usuario de Ajustes basta para los enlaces.
        "username": perfil.get("username") or ajuste_de_tracker(conn, "username", tracker.id) or None,
        "updated_at": guardado.get("leido_at"),
        "error": guardado.get("error"),
    }


def _caducado(guardado: dict[str, Any], plazo: timedelta) -> bool:
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(guardado["intento_at"]) >= plazo
    except (KeyError, TypeError, ValueError):
        return True


def _huella(clave: str) -> str:
    return hashlib.sha256(clave.encode()).hexdigest()


def _pedir(conn: sqlite3.Connection, clave: str, guardado: dict[str, Any], tracker: Tracker) -> dict[str, Any]:
    nuevo = {**guardado, "intento_at": datetime.now(timezone.utc).isoformat(), "huella": _huella(clave)}
    cambios = None
    try:
        datos = emu_get_user(clave, tracker.url, tracker=tracker.uploadrr)
    except IntegrationError as exc:
        logger.warning("No se pudo leer el perfil de %s: %s", tracker.nombre, texto_de_error(exc))
        nuevo["error"] = texto_de_error(exc)
    else:
        nuevo.update(perfil={campo: datos.get(campo) for campo in CAMPOS}, leido_at=nuevo["intento_at"], error=None)
        # «Mis peticiones» y «Tus subidas» cuentan por este nombre: si no está en Ajustes, se pone el de la cuenta. El de
        # los otros no se edita en Ajustes: sigue siempre a su clave.
        if tracker is not EMU or not ajuste_de_tracker(conn, "username", tracker.id):
            set_setting(conn, tracker.clave("username"), str(datos["username"]))
        guardar_instantanea(conn, datos, tracker=tracker)
        # Los avisos de la cuenta son los de eMuwarez.
        cambios = (guardado.get("perfil"), nuevo["perfil"]) if tracker is EMU else None
    set_json_state(conn, f"{tracker.id}.perfil", nuevo)
    conn.commit()
    # Después del commit: cada aviso confirma el suyo, y Discord/Telegram no se esperan con la transacción abierta.
    if cambios:
        avisar_cambios(conn, *cambios)
    return nuevo
