"""Tus subidas en eMuwarez: la lectura por usuario, la foto de cada día, lo de la semana y la tarea."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from emuwarez_uploadrr.database import SCHEMA_VERSION, connect, db_path, get_state, init_db, set_state
from emuwarez_uploadrr.integrations import emu
from emuwarez_uploadrr.integrations.base import IntegrationError
from emuwarez_uploadrr.services import mis_torrents, scheduler
from emuwarez_uploadrr.services.mis_torrents import resumen, sincronizar
from emuwarez_uploadrr.services.settings import set_setting

AHORA = datetime(2026, 11, 18, 12, 0)


def _torrent(tid: int, completados: int, seeders: int = 5, subido: str = "2026-01-01T10:00:00.000000Z", **extra):
    # Lo que devuelve UNIT3D (app/Http/Resources/TorrentResource.php).
    return {"type": "torrent", "id": str(tid), "attributes": {
        "name": f"Torrent {tid}", "size": 1024 ** 3, "seeders": seeders, "leechers": 1, "times_completed": completados,
        "freeleech": "0%", "double_upload": False, "created_at": subido, **extra}}


@pytest.fixture
def conn(isolated_db):
    c = connect(db_path())
    init_db(c)
    set_setting(c, "emu.api_key", "clave")
    set_setting(c, "emu.username", "Kisnov")
    c.commit()
    yield c
    c.close()


@pytest.fixture
def tracker(monkeypatch):
    """Lo que devuelve eMuwarez en la próxima lectura, y con qué se pidió."""
    estado = {"torrents": [], "pedido": None}

    def leer(api_key, **kwargs):
        estado["pedido"] = kwargs
        if isinstance(estado["torrents"], Exception):
            raise estado["torrents"]
        return estado["torrents"]

    monkeypatch.setattr(mis_torrents, "emu_fetch_all_torrents", leer)
    return estado


def test_esquema_57_trae_las_tablas(conn):
    assert get_state(conn, "schema_version") == str(SCHEMA_VERSION)
    for tabla in ("emu_mis_torrents", "emu_mis_torrents_dia"):
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = ?", (tabla,)).fetchone()
    conn.execute("DROP TABLE emu_mis_torrents")
    set_state(conn, "schema_version", "56")
    conn.commit()
    init_db(conn)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'emu_mis_torrents'").fetchone()


def test_el_filtro_por_usuario_va_en_la_url(monkeypatch):
    urls = []
    monkeypatch.setattr(emu, "_pedir_pagina", lambda url, api_key: urls.append(url) or {"data": []})
    emu.emu_fetch_all_torrents("clave", uploader="Kisnov")
    emu.emu_fetch_all_torrents("clave")
    assert "uploader=Kisnov" in urls[0] and "uploader" not in urls[1]


def test_lee_por_tu_usuario_y_guarda_la_lista(conn, tracker):
    tracker["torrents"] = [_torrent(1, 40), _torrent(2, 3, seeders=0, freeleech="50%", double_upload=True)]
    assert sincronizar(conn, AHORA) == ("completed", "2 subidas, 1 sin seeders")
    assert tracker["pedido"]["uploader"] == "Kisnov"
    r = resumen(conn, AHORA)
    assert [t["torrent_id"] for t in r["torrents"]] == [1, 2]          # las más completadas primero
    assert r["torrents"][1]["freeleech"] == "50%" and r["torrents"][1]["doble_subida"] is True
    assert r["torrents"][0]["freeleech"] is None
    assert r["totales"] == {"subidas": 2, "seeders": 5, "leechers": 2, "completados": 43,
                            "completados_semana": None, "sin_seeders": 1}
    assert r["leido_at"] == AHORA.astimezone().isoformat(timespec="seconds")


def test_lo_que_ya_no_sale_se_ha_borrado_del_tracker(conn, tracker):
    tracker["torrents"] = [_torrent(1, 40), _torrent(2, 3)]
    sincronizar(conn, AHORA - timedelta(days=1))
    tracker["torrents"] = [_torrent(1, 41)]
    sincronizar(conn, AHORA)
    assert [t["torrent_id"] for t in resumen(conn, AHORA)["torrents"]] == [1]
    assert conn.execute("SELECT COUNT(*) FROM emu_mis_torrents_dia WHERE torrent_id = 2").fetchone()[0] == 0


def test_lo_completado_en_la_semana(conn, tracker):
    tracker["torrents"] = [_torrent(1, 10), _torrent(2, 5)]
    sincronizar(conn, AHORA - timedelta(days=8))
    tracker["torrents"] = [_torrent(1, 15), _torrent(2, 5)]
    sincronizar(conn, AHORA - timedelta(days=3))
    tracker["torrents"] = [_torrent(1, 25), _torrent(2, 7), _torrent(3, 4, subido="2026-11-16T09:00:00Z"),
                           _torrent(4, 9)]
    sincronizar(conn, AHORA)
    por_id = {t["torrent_id"]: t["completados_semana"] for t in resumen(conn, AHORA)["torrents"]}
    # 1 y 2 contra la foto de hace 8 días; 3 se subió esta semana (cuenta entero); 4 no tiene foto de entonces.
    assert por_id == {1: 15, 2: 2, 3: 4, 4: None}
    assert resumen(conn, AHORA)["totales"]["completados_semana"] == 21


def test_la_foto_del_dia_se_sobrescribe_y_se_poda_la_antigua(conn, tracker):
    tracker["torrents"] = [_torrent(1, 10)]
    sincronizar(conn, AHORA - timedelta(days=mis_torrents.RETENCION_DIAS + 1))
    sincronizar(conn, AHORA.replace(hour=6))
    tracker["torrents"] = [_torrent(1, 12)]
    sincronizar(conn, AHORA)
    filas = conn.execute("SELECT dia, completados FROM emu_mis_torrents_dia").fetchall()
    assert [tuple(f) for f in filas] == [(AHORA.strftime("%Y-%m-%d"), 12)]


@pytest.mark.parametrize("ajuste, motivo", [("emu.api_key", "Sin API key"), ("emu.username", "usuario")])
def test_sin_clave_o_sin_usuario_se_salta(conn, tracker, ajuste, motivo):
    set_setting(conn, ajuste, "")
    conn.commit()
    estado, resumen_ = sincronizar(conn, AHORA)
    assert estado == "skipped" and motivo in resumen_
    assert tracker["pedido"] is None


def test_con_una_subida_en_marcha_espera(conn, tracker, monkeypatch):
    from emuwarez_uploadrr.services import emu_catalog
    monkeypatch.setattr(emu_catalog, "motivo_subida_en_marcha", lambda c: "Hay una subida en marcha")
    assert sincronizar(conn, AHORA)[0] == "busy"
    assert tracker["pedido"] is None


def test_un_fallo_de_emuwarez_no_toca_lo_guardado(conn, tracker):
    tracker["torrents"] = [_torrent(1, 10)]
    sincronizar(conn, AHORA - timedelta(days=1))
    tracker["torrents"] = IntegrationError("sin red")
    assert sincronizar(conn, AHORA) == ("failed", "sin red")
    assert len(resumen(conn, AHORA)["torrents"]) == 1


def test_la_tarea_cada_seis_horas(conn, tracker):
    tarea = scheduler.get_scheduled_task(conn, scheduler.TASK_EMU_MIS_SUBIDAS)
    assert (tarea["interval_minutes"], tarea["min_interval_minutes"]) == (360, 180)
    tracker["torrents"] = [_torrent(1, 10)]
    assert scheduler.run_scheduled_task(conn, scheduler.TASK_EMU_MIS_SUBIDAS)["status"] == "completed"


def test_endpoint(client, auth_headers, conn, tracker):
    assert client.get("/api/estadisticas/mis-subidas").status_code == 401
    vacio = client.get("/api/estadisticas/mis-subidas", headers=auth_headers).json()
    assert vacio["leido_at"] is None and vacio["torrents"] == [] and vacio["totales"]["subidas"] == 0
    tracker["torrents"] = [_torrent(1, 10)]
    sincronizar(conn)
    cuerpo = client.get("/api/estadisticas/mis-subidas", headers=auth_headers).json()
    assert cuerpo["torrents"][0]["nombre"] == "Torrent 1" and cuerpo["torrents"][0]["completados"] == 10
