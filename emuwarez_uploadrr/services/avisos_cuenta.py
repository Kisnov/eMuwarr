"""Avisos de tu cuenta de eMuwarez: lo que cambia entre una lectura del perfil y la siguiente (cada hora, con la tarea
«Leer tu cuenta de eMuwarez»).

Sin llamadas nuevas: compara el perfil recién leído con el anterior. Cada aviso salta una vez, cuando cambia algo, y no
en cada lectura mientras sigue igual. Van a la campana y, si se activan en Ajustes → Notificaciones, a Discord/Telegram.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional

from .notification_center import emit
from .perfil_historial import bytes_de_texto

logger = logging.getLogger(__name__)

EVENTO_HR = "cuenta_hr_nuevo"
EVENTO_GRUPO = "cuenta_grupo_cambia"
EVENTO_BUFFER = "cuenta_buffer_negativo"
EVENTO_SEEDING = "cuenta_seeding_baja"
# Un bajón de seeding entre dos lecturas avisa si es de al menos esta parte de lo que sembrabas y este número de
# torrents: con 10 sembrando, perder 2 es normal; con 800, perder 200 es que qBittorrent se ha parado.
CAIDA_SEEDING = 0.2
CAIDA_SEEDING_MINIMA = 10


def _entero(valor: Any) -> Optional[int]:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def avisar_cambios(conn: sqlite3.Connection, antes: Optional[dict[str, Any]], ahora: dict[str, Any]) -> None:
    """Compara dos lecturas del perfil (CAMPOS de emu_perfil) y avisa de lo que ha cambiado a peor o de grupo.
    Sin lectura anterior, o si es de otra cuenta, no hay con qué comparar. Nunca lanza: un aviso no puede romper la
    lectura del perfil."""
    if not antes or antes.get("username") != ahora.get("username"):
        return
    try:
        hr_antes, hr_ahora = _entero(antes.get("hit_and_runs")), _entero(ahora.get("hit_and_runs"))
        if hr_antes is not None and hr_ahora is not None and hr_ahora > hr_antes:
            nuevos = hr_ahora - hr_antes
            emit(conn, EVENTO_HR,
                 "Tienes un H&R nuevo en eMuwarez" if nuevos == 1 else f"Tienes {nuevos} H&R nuevos en eMuwarez",
                 f"Ahora tienes {hr_ahora}. Mira en eMuwarez de qué torrent es y siémbralo hasta cumplir el tiempo "
                 "mínimo.", level="warning")

        grupo_antes, grupo_ahora = antes.get("group"), ahora.get("group")
        if grupo_antes and grupo_ahora and grupo_antes != grupo_ahora:
            emit(conn, EVENTO_GRUPO, "Tu grupo en eMuwarez ha cambiado", f"De {grupo_antes} a {grupo_ahora}.")

        # El buffer de UNIT3D es lo que puedes bajar sin quedar por debajo del ratio mínimo del tracker.
        buffer_antes, buffer_ahora = bytes_de_texto(antes.get("buffer")), bytes_de_texto(ahora.get("buffer"))
        if buffer_antes is not None and buffer_ahora is not None and buffer_antes >= 0 > buffer_ahora:
            emit(conn, EVENTO_BUFFER, "Tu buffer en eMuwarez es negativo",
                 f"Estás por debajo del ratio mínimo del tracker ({ahora.get('buffer')}). Sube o siembra antes de "
                 "bajar nada más.", level="error")

        seeding_antes, seeding_ahora = _entero(antes.get("seeding")), _entero(ahora.get("seeding"))
        if seeding_antes is not None and seeding_ahora is not None:
            caida = seeding_antes - seeding_ahora
            if caida >= max(CAIDA_SEEDING_MINIMA, seeding_antes * CAIDA_SEEDING):
                emit(conn, EVENTO_SEEDING, "Siembras menos torrents en eMuwarez",
                     f"{seeding_ahora} ahora, {seeding_antes} en la lectura anterior. ¿Se ha parado qBittorrent o ha "
                     "perdido torrents?", level="warning")
    except Exception:
        logger.warning("No se pudieron comprobar los avisos de la cuenta", exc_info=True)
