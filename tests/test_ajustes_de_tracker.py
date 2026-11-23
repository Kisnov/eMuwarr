"""Fase A del plan multitracker: las credenciales de un tracker se leen con `settings.ajuste_de_tracker`, no con la
clave escrita a mano. Así el segundo adaptador solo tiene que dar su prefijo de ajustes."""
from __future__ import annotations

import re
from pathlib import Path

from emuwarez_uploadrr.database import db
from emuwarez_uploadrr.services import trackers
from emuwarez_uploadrr.services.settings import ajuste_de_tracker, set_setting

PAQUETE = Path(__file__).resolve().parents[1] / "emuwarez_uploadrr"

# Donde la clave tiene que estar escrita: el registro de ajustes la declara y database.py guarda su alias antiguo.
PERMITIDOS = {"services/ajustes_registro.py", "database.py"}

# Los de todos los trackers, no solo eMuwarez: NOBS y Milnueve también se leen por `ajuste_de_tracker`.
PREFIJOS = "|".join(re.escape(t.prefijo_ajustes) for t in trackers.TRACKERS.values())
CLAVE_A_MANO = re.compile(rf"""["'](?:{PREFIJOS})\.(api_key|username)["']""")


def test_nadie_escribe_a_mano_la_clave_o_el_usuario_de_un_tracker():
    a_mano = []
    for ruta in sorted(PAQUETE.rglob("*.py")):
        relativa = ruta.relative_to(PAQUETE).as_posix()
        if relativa in PERMITIDOS:
            continue
        for n, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
            if CLAVE_A_MANO.search(linea):
                a_mano.append(f"{relativa}:{n}: {linea.strip()}")
    assert not a_mano, "Usa settings.ajuste_de_tracker o trackers.EMU.clave(...):\n" + "\n".join(a_mano)


def test_la_clave_de_cada_ajuste_sale_del_prefijo_del_tracker():
    assert trackers.EMU.clave("api_key") == "emu.api_key"
    assert trackers.EMU.clave("username") == "emu.username"
    assert trackers.NOBS.clave("api_key") == "nobs.api_key"
    assert trackers.MNV.clave("api_key") == "mnv.api_key"


def test_lee_el_ajuste_del_tracker_y_su_alias_antiguo():
    with db() as conn:
        assert ajuste_de_tracker(conn, "api_key") is None
        set_setting(conn, "emu_api_key", "vieja")          # el alias de antes de los espacios de nombres
        assert ajuste_de_tracker(conn, "api_key") == "vieja"
        set_setting(conn, "emu.api_key", "nueva")
        assert ajuste_de_tracker(conn, "api_key", trackers.EMU.id) == "nueva"
        set_setting(conn, "emu.username", "yo")
        assert ajuste_de_tracker(conn, "username") == "yo"
        set_setting(conn, "nobs.api_key", "de-nobs")
        assert ajuste_de_tracker(conn, "api_key", trackers.NOBS.id) == "de-nobs"
        assert ajuste_de_tracker(conn, "api_key", trackers.MNV.id) is None


def test_la_url_de_un_tracker_es_la_de_sus_ajustes_o_la_de_siempre():
    with db() as conn:
        assert trackers.url_de(conn, trackers.NOBS) == "https://nobs.rawsmoke.net"
        set_setting(conn, "nobs.url", "https://nobs.example")
        assert trackers.url_de(conn, trackers.NOBS) == "https://nobs.example"
        set_setting(conn, "nobs.url", "")
        assert trackers.url_de(conn, trackers.NOBS) == "https://nobs.rawsmoke.net"
        assert trackers.url_de(conn, trackers.EMU) == trackers.EMU.url
