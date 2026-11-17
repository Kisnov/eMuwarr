"""Si TMDb responde, para «Necesita atención» del Panel: una prueba por hora como mucho, y otra al cambiar la clave."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from emuwarez_uploadrr.database import connect, db_path, get_json_state, init_db, set_json_state
from emuwarez_uploadrr.services import salud_tmdb
from emuwarez_uploadrr.services.salud_tmdb import ESTADO, error_tmdb
from emuwarez_uploadrr.services.settings import set_setting


@pytest.fixture
def conn():
    c = connect(db_path())
    init_db(c)
    set_setting(c, "tmdb.api_key", "clave")
    c.commit()
    yield c
    c.close()


@pytest.fixture
def pruebas(monkeypatch):
    """Cada prueba de TMDb hecha; responde lo que diga `resultado`."""
    hechas: list[int] = []
    resultado = {"ok": False, "message": "API key de TMDb inválida o no autorizada"}

    def falsa(*, conn):
        hechas.append(1)
        return resultado

    monkeypatch.setattr(salud_tmdb, "test_tmdb_connection", falsa)
    return hechas, resultado


def test_con_la_clave_rechazada_da_el_motivo_y_no_vuelve_a_probar_en_una_hora(conn, pruebas):
    hechas, _ = pruebas
    assert error_tmdb(conn) == "API key de TMDb inválida o no autorizada"
    assert error_tmdb(conn) == "API key de TMDb inválida o no autorizada"
    assert len(hechas) == 1


def test_si_responde_no_hay_error(conn, pruebas):
    _, resultado = pruebas
    resultado.update(ok=True, message="Conexión correcta")
    assert error_tmdb(conn) is None


def test_pasada_la_hora_vuelve_a_probar(conn, pruebas):
    hechas, resultado = pruebas
    error_tmdb(conn)
    viejo = get_json_state(conn, ESTADO)
    viejo["probado_at"] = (datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)).isoformat()
    set_json_state(conn, ESTADO, viejo)
    resultado.update(ok=True, message="Conexión correcta")
    assert error_tmdb(conn) is None
    assert len(hechas) == 2


def test_una_clave_nueva_se_prueba_en_seguida(conn, pruebas):
    hechas, resultado = pruebas
    error_tmdb(conn)
    set_setting(conn, "tmdb.api_key", "otra")
    resultado.update(ok=True, message="Conexión correcta")
    assert error_tmdb(conn) is None
    assert len(hechas) == 2


def test_el_resumen_del_panel_lleva_el_error_de_tmdb(client, auth_headers, pruebas):
    resumen = client.get("/api/dashboard/summary", headers=auth_headers).json()
    assert resumen["tmdb_error"] == "API key de TMDb inválida o no autorizada"
