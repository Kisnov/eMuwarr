"""Los trackers a los que sube eMuwarr (fase A del plan multitracker, `docs/PLAN.md`).

Cada fila que es de un tracker (una subida, una petición, el catálogo, «Tus subidas», el historial del perfil) lleva
su id en la columna `tracker`. Hoy solo hay uno, eMuwarez (`emu`), y es el valor por defecto de esa columna: quien solo
usa eMuwarez no ve ningún cambio. Un tracker nuevo se añade aquí y con su adaptador.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..constants import EMU_BASE_URL


@dataclass(frozen=True)
class Tracker:
    id: str               # lo que se guarda en la columna `tracker`; corto y para siempre
    nombre: str           # lo que ve el usuario
    url: str              # origen por defecto (el usuario puede cambiarlo en sus ajustes)
    plataforma: str       # "unit3d": habla con integrations/cliente_unit3d.py
    prefijo_ajustes: str  # espacio de nombres de sus ajustes: `emu` → emu.api_key, emu.username…
    uploadrr: str         # su nombre en Uploadrr-EMU: `-tk`, TRACKERS.<uploadrr> y src/trackers/<uploadrr>.py

    def clave(self, nombre: str) -> str:
        """La clave en la tabla `settings` del ajuste `nombre` de este tracker: `api_key` → `emu.api_key`."""
        return f"{self.prefijo_ajustes}.{nombre}"


EMU = Tracker(id="emu", nombre="eMuwarez", url=EMU_BASE_URL, plataforma="unit3d", prefijo_ajustes="emu",
              uploadrr="EMU")
# Fase B: dos UNIT3D más, con la clave en sus propios ajustes. Aún no se sube a ellos desde la app.
NOBS = Tracker(id="nobs", nombre="N.O.B.S", url="https://nobs.rawsmoke.net", plataforma="unit3d",
               prefijo_ajustes="nobs", uploadrr="NOBS")
MNV = Tracker(id="mnv", nombre="Milnueve", url="https://tracker.milnueve.cc", plataforma="unit3d",
              prefijo_ajustes="mnv", uploadrr="MNV")

TRACKERS: dict[str, Tracker] = {t.id: t for t in (EMU, NOBS, MNV)}

# El de las filas de antes de que hubiera varios, y el de toda llamada que aún no dice cuál.
POR_DEFECTO = EMU.id


def obtener(tracker_id: str) -> Tracker:
    """El tracker con ese id; un id desconocido es un error de programación, no un dato que se pueda callar."""
    try:
        return TRACKERS[tracker_id]
    except KeyError:
        raise ValueError(f"Tracker desconocido: {tracker_id!r}") from None


__all__ = ["Tracker", "EMU", "NOBS", "MNV", "TRACKERS", "POR_DEFECTO", "obtener"]
