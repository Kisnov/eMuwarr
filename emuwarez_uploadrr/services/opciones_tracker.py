"""Anónimo y Personal release de una subida.

En Configuración, cada grupo de subida (el tag tras el último «-» del nombre) se puede marcar como Personal release,
como anónimo o las dos cosas. Una subida con ese grupo lleva la marca sin que nadie la toque: también las de un lote o
las automáticas. En el Resumen del asistente las dos casillas salen con lo que diga el grupo y se pueden cambiar
para esa subida; lo elegido a mano se guarda en el trabajo (`upload_jobs.anonimo` / `personal_release`, NULL = lo
que diga el grupo) y gana a la regla.

Llega a Uploadrr-EMU como `-a` / `-pr` (uploadrr_contract.build_uploadrr_emu_command), que el tracker UNIT3D manda
como `anonymous` / `personal_release`.

Ajustes (JSON con la lista de grupos):
- upload.tags_personal_release
- upload.tags_anonimos

Son los de eMuwarez. Otro tracker tiene los suyos (`<prefijo>.tags_personal_release` / `<prefijo>.tags_anonimos`, por
`ajuste_de_tracker`); sin guardar, sigue los de eMuwarez.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable, Optional

from .settings import ajuste_de_tracker, get_setting_canonical, set_setting
from .trackers import EMU, POR_DEFECTO, obtener

CLAVE_PERSONAL_RELEASE = "upload.tags_personal_release"
CLAVE_ANONIMOS = "upload.tags_anonimos"


def _leer_lista(conn: sqlite3.Connection, clave: str, tracker: str = POR_DEFECTO) -> list[str]:
    propia = None if tracker == EMU.id else ajuste_de_tracker(conn, clave.removeprefix("upload."), tracker)
    try:
        valor = json.loads(propia or get_setting_canonical(conn, clave) or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(valor, list):
        return []
    return limpiar_grupos(valor)


def limpiar_grupos(grupos: Iterable) -> list[str]:
    """Sin vacíos ni repetidos (sin distinguir mayúsculas), en el orden dado."""
    vistos: set[str] = set()
    salida: list[str] = []
    for g in grupos or []:
        texto = str(g or "").strip()
        if texto and texto.casefold() not in vistos:
            vistos.add(texto.casefold())
            salida.append(texto)
    return salida


def grupos_personal_release(conn: sqlite3.Connection, tracker: str = POR_DEFECTO) -> list[str]:
    return _leer_lista(conn, CLAVE_PERSONAL_RELEASE, tracker)


def grupos_anonimos(conn: sqlite3.Connection, tracker: str = POR_DEFECTO) -> list[str]:
    return _leer_lista(conn, CLAVE_ANONIMOS, tracker)


def marcas_propias(conn: sqlite3.Connection, tracker: str) -> Optional[dict[str, list[str]]]:
    """Las marcas guardadas de otro tracker, o None si sigue las de eMuwarez."""
    if ajuste_de_tracker(conn, "tags_anonimos", tracker) is None:
        return None
    return {"personal_release": grupos_personal_release(conn, tracker), "anonimos": grupos_anonimos(conn, tracker)}


def guardar_grupos(
    conn: sqlite3.Connection, *, personal_release: Optional[Iterable] = None, anonimos: Optional[Iterable] = None,
) -> None:
    """None deja la lista como está."""
    if personal_release is not None:
        set_setting(conn, CLAVE_PERSONAL_RELEASE, json.dumps(limpiar_grupos(personal_release), ensure_ascii=False))
    if anonimos is not None:
        set_setting(conn, CLAVE_ANONIMOS, json.dumps(limpiar_grupos(anonimos), ensure_ascii=False))


def guardar_marcas_propias(conn: sqlite3.Connection, tracker: str, marcas: Optional[dict]) -> None:
    """Las marcas de otro tracker; None vuelve a las de eMuwarez."""
    t = obtener(tracker)
    for nombre in ("personal_release", "anonimos"):
        valor = "" if marcas is None else json.dumps(limpiar_grupos(marcas.get(nombre)), ensure_ascii=False)
        set_setting(conn, t.clave(f"tags_{nombre}"), valor)


def _en(grupo: Optional[str], lista: list[str]) -> bool:
    texto = (grupo or "").strip().casefold()
    return bool(texto) and any(texto == g.casefold() for g in lista)


def opciones_de_subida(
    conn: sqlite3.Connection, grupo: Optional[str], *, tracker: str = POR_DEFECTO,
    anonimo: Optional[bool] = None, personal_release: Optional[bool] = None,
) -> dict[str, bool]:
    """{"anon", "personal_release"} de una subida a `tracker` con el grupo `grupo`. `anonimo` / `personal_release`
    son lo elegido a mano en el asistente (None: lo que diga el grupo en ese tracker)."""
    return {
        "anon": bool(anonimo) if anonimo is not None else _en(grupo, grupos_anonimos(conn, tracker)),
        "personal_release": (bool(personal_release) if personal_release is not None
                             else _en(grupo, grupos_personal_release(conn, tracker))),
    }


__all__ = [
    "CLAVE_PERSONAL_RELEASE", "CLAVE_ANONIMOS", "limpiar_grupos", "grupos_personal_release", "grupos_anonimos",
    "marcas_propias", "guardar_grupos", "guardar_marcas_propias", "opciones_de_subida",
]
