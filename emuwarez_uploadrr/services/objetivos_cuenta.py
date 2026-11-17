"""Tus objetivos en eMuwarez (subido, ratio, bonus, seeding o antigüedad) y tu fecha de alta, para Estadísticas.

La API de UNIT3D no da la fecha de alta, así que la escribes tú; los objetivos también. Se guardan tal cual en el estado
de la aplicación: el progreso y cuándo llegarás los calcula la interfaz con la última instantánea y los ritmos de la
semana. Unidades del `valor`: subido en TB, antigüedad en meses, el resto en su cifra.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..database import get_json_state, set_json_state

ESTADO = "estadisticas.objetivos"


def leer(conn: sqlite3.Connection) -> dict[str, Any]:
    guardado = get_json_state(conn, ESTADO) or {}
    return {"fecha_alta": guardado.get("fecha_alta"), "objetivos": guardado.get("objetivos") or []}


def guardar(conn: sqlite3.Connection, fecha_alta: str | None, objetivos: list[dict[str, Any]]) -> dict[str, Any]:
    set_json_state(conn, ESTADO, {"fecha_alta": fecha_alta, "objetivos": objetivos})
    conn.commit()
    return leer(conn)
