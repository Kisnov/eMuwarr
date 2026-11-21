"""Tus subidas en eMuwarez: la lectura por usuario, la foto de cada día, lo de la semana y la tarea."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

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
    monkeypatch.setattr(emu, "request_json", lambda method, url, **kw: urls.append(url) or {"data": []})
    emu.emu_fetch_all_torrents("clave", uploader="Kisnov")
    emu.emu_fetch_all_torrents("clave")
    assert "uploader=Kisnov" in urls[0] and "uploader" not in urls[1]


def test_un_torrent_repetido_entre_paginas_no_tumba_la_lectura(conn, tracker):
    """Una subida nueva mientras se pagina desplaza la lista y repite un id: la clave primaria abortaba todo."""
    tracker["torrents"] = [_torrent(1, 40), _torrent(2, 3), _torrent(1, 41)]
    assert sincronizar(conn, AHORA)[0] == "completed"
    assert [tuple(f) for f in conn.execute("SELECT torrent_id, completados FROM emu_mis_torrents ORDER BY 1")] == [
        (1, 41), (2, 3)]


def test_lee_por_tu_usuario_y_guarda_la_lista(conn, tracker):
    tracker["torrents"] = [_torrent(1, 40), _torrent(2, 3, seeders=0, freeleech="50%", double_upload=True)]
    assert sincronizar(conn, AHORA) == ("completed", "2 subidas, 1 sin seeders")
    assert tracker["pedido"]["uploader"] == "Kisnov"
    r = resumen(conn)
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
    assert [t["torrent_id"] for t in resumen(conn)["torrents"]] == [1]
    assert conn.execute("SELECT COUNT(*) FROM emu_mis_torrents_dia WHERE torrent_id = 2").fetchone()[0] == 0


def test_lo_completado_en_la_semana(conn, tracker):
    tracker["torrents"] = [_torrent(1, 10), _torrent(2, 5)]
    sincronizar(conn, AHORA - timedelta(days=7))
    tracker["torrents"] = [_torrent(1, 15), _torrent(2, 5)]
    sincronizar(conn, AHORA - timedelta(days=3))
    tracker["torrents"] = [_torrent(1, 25), _torrent(2, 7), _torrent(3, 4, subido="2026-11-16T09:00:00Z"),
                           _torrent(4, 9)]
    sincronizar(conn, AHORA)
    por_id = {t["torrent_id"]: t["completados_semana"] for t in resumen(conn)["torrents"]}
    # 1 y 2 contra la foto de hace 7 días; 3 se subió esta semana (cuenta entero); 4 no tiene foto de entonces.
    assert por_id == {1: 15, 2: 2, 3: 4, 4: None}
    assert resumen(conn)["totales"]["completados_semana"] == 21


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
    assert len(resumen(conn)["torrents"]) == 1


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


def test_la_semana_se_cuenta_hasta_la_ultima_lectura_y_sin_fotos_mas_viejas(conn, tracker):
    tracker["torrents"] = [_torrent(1, 10)]
    sincronizar(conn, AHORA - timedelta(days=9))       # anterior al corte: no vale como base
    tracker["torrents"] = [_torrent(1, 30)]
    sincronizar(conn, AHORA)
    assert resumen(conn)["torrents"][0]["completados_semana"] is None
    # Abrir Estadísticas otro día sin leer de nuevo no mueve el corte: va con la fecha de la lectura.
    conn.execute("INSERT INTO emu_mis_torrents_dia VALUES (?, 1, 5, 0, 12)",
                 ((AHORA - timedelta(days=7)).strftime("%Y-%m-%d"),))
    conn.commit()
    assert resumen(conn)["torrents"][0]["completados_semana"] == 18


@pytest.fixture
def madrid(monkeypatch):
    """El servidor en Madrid (UTC+1 en noviembre): el día local de una hora UTC puede ser el siguiente."""
    monkeypatch.setenv("TZ", "Europe/Madrid")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_una_subida_de_justo_despues_del_corte_cuenta_en_su_dia_local(conn, tracker, madrid):
    corte = AHORA - timedelta(days=7)
    # Medianoche local del día siguiente al corte, escrita en UTC: su prefijo puede ser aún el día del corte.
    subido = (corte + timedelta(days=1)).replace(hour=0, minute=30).astimezone().astimezone(timezone.utc)
    tracker["torrents"] = [_torrent(1, 6, subido=subido.strftime("%Y-%m-%dT%H:%M:%S.000000Z"))]
    sincronizar(conn, AHORA)
    assert resumen(conn)["torrents"][0]["completados_semana"] == 6


def test_al_llegar_al_tope_no_se_guarda_y_queda_la_lectura_anterior(conn, tracker):
    tracker["torrents"] = [_torrent(1, 10)]
    sincronizar(conn, AHORA - timedelta(days=1))
    tracker["torrents"] = [_torrent(i, 1) for i in range(1, mis_torrents.MAX_PAGINAS * mis_torrents.POR_PAGINA + 1)]
    estado, texto = sincronizar(conn, AHORA)
    assert estado == "failed" and "no se guarda" in texto
    assert [t["torrent_id"] for t in resumen(conn)["torrents"]] == [1]


def test_si_falla_al_escribir_se_deshace_entera(conn, tracker, monkeypatch):
    tracker["torrents"] = [_torrent(1, 10), _torrent(2, 5)]
    sincronizar(conn, AHORA - timedelta(days=1))
    tracker["torrents"] = [_torrent(3, 1)]

    def falla(*a, **k):
        raise sqlite3.OperationalError("disco lleno")

    monkeypatch.setattr(mis_torrents, "set_json_state", falla)
    with pytest.raises(sqlite3.OperationalError):
        sincronizar(conn, AHORA)
    conn.commit()   # lo que haría después el planificador: no debe confirmar nada a medias
    assert [t["torrent_id"] for t in resumen(conn)["torrents"]] == [1, 2]


def test_con_otra_lectura_en_marcha_espera(conn, tracker):
    assert mis_torrents._LEYENDO.acquire(blocking=False)
    try:
        assert sincronizar(conn, AHORA)[0] == "busy"
    finally:
        mis_torrents._LEYENDO.release()
    assert tracker["pedido"] is None


def test_la_lectura_de_otro_usuario_no_se_ensena(conn, tracker):
    tracker["torrents"] = [_torrent(1, 10)]
    sincronizar(conn, AHORA)
    set_setting(conn, "emu.username", "Otro")
    conn.commit()
    r = resumen(conn)
    assert r["leido_at"] is None and r["torrents"] == [] and r["totales"]["subidas"] == 0


def test_sin_cifra_de_seeders_no_cuenta_como_sin_seeders(conn, tracker):
    tracker["torrents"] = [_torrent(1, 10, seeders=None), _torrent(2, 3, seeders=0)]
    assert sincronizar(conn, AHORA) == ("completed", "2 subidas, 1 sin seeders")
    assert resumen(conn)["totales"]["sin_seeders"] == 1


def test_una_pagina_sin_lista_no_es_el_final(monkeypatch):
    paginas = iter([{"data": [_torrent(i, 1) for i in range(100)]}, {"message": "Server Error"}])
    monkeypatch.setattr(emu, "request_json", lambda method, url, **kw: next(paginas))
    with pytest.raises(IntegrationError, match="página 2"):
        emu.emu_fetch_all_torrents("clave", uploader="Kisnov")


def test_una_subida_del_mismo_dia_del_corte_sin_foto_cuenta_entera(conn, tracker):
    corte = AHORA - timedelta(days=7)
    tracker["torrents"] = [_torrent(1, 4, subido=corte.replace(hour=12).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000000Z"))]
    sincronizar(conn, AHORA)
    assert resumen(conn)["torrents"][0]["completados_semana"] == 4


# --- Pendientes de moderación: lo que no sale en la lectura ----------------------------------------------------------

LEIDA = datetime(2026, 11, 19, 11, 0, tzinfo=timezone.utc)


def _subida(torrent_id=18532, *, terminada="2026-11-19T10:22:17+00:00", estado="completed", comando="upload.py X -tk EMU"):
    return {"status": estado, "result_torrent_id": str(torrent_id), "finished_at": terminada,
            "safe_command_json": json.dumps({"args_preview": comando})}


@pytest.mark.parametrize("subida, pendiente", [
    (_subida(), True),                                                  # terminó antes de leer y no sale
    (_subida(18434), False),                                            # sale: ya está aprobada
    (_subida(terminada="2026-11-19T11:30:00+00:00"), False),            # terminó después de la lectura: no se sabe
    (_subida(comando='upload.py "X -a Y" -tk EMU -a'), False),          # anónima: eMuwarez no la da por usuario
    (_subida(estado="failed"), False),
    ({**_subida(), "result_torrent_id": None}, False),
    ({**_subida(), "finished_at": None}, False),
], ids=["pendiente", "aprobada", "despues-de-leer", "anonima", "fallida", "sin-id", "sin-fecha"])
def test_pendiente_de_moderacion(subida, pendiente):
    assert mis_torrents.pendiente_de_moderacion(subida, (LEIDA, frozenset({18434}))) is pendiente


def test_sin_lectura_nada_esta_pendiente():
    assert mis_torrents.pendiente_de_moderacion(_subida(), None) is False


def test_un_titulo_con_menos_a_no_la_hace_anonima():
    comando = 'upload.py "/x/Peli -a 2020.mkv" -tk EMU -mt "Peli -a 2020"'
    assert mis_torrents.pendiente_de_moderacion(_subida(comando=comando), (LEIDA, frozenset())) is True


def test_la_lectura_para_moderacion_es_la_de_tu_usuario(conn, tracker):
    assert mis_torrents.lectura_para_moderacion(conn) is None
    tracker["torrents"] = [_torrent(18434, 3)]
    sincronizar(conn, AHORA)
    leido_at, ids = mis_torrents.lectura_para_moderacion(conn)
    assert ids == {18434} and leido_at.tzinfo is not None
    set_setting(conn, "emu.username", "Otro")
    conn.commit()
    assert mis_torrents.lectura_para_moderacion(conn) is None


def test_la_lista_de_subidas_marca_la_pendiente(client, auth_headers, conn, tracker):
    tracker["torrents"] = [_torrent(18434, 3)]
    filas = []
    for torrent_id in (18434, 18532):
        cur = conn.execute(
            "INSERT INTO upload_jobs (title, path, status, result_tracker, result_torrent_id, finished_at, safe_command_json) "
            "VALUES ('Peli', '/x', 'completed', ?, ?, ?, '{}')",
            (f"https://emu-warez.com/torrents/{torrent_id}", str(torrent_id), "2026-01-01T10:00:00+00:00"))
        filas.append(cur.lastrowid)
    conn.commit()
    sincronizar(conn)
    lista = {j["id"]: j["pendiente_moderacion"] for j in client.get("/api/uploads", headers=auth_headers).json()}
    assert lista == {filas[0]: False, filas[1]: True}
    assert client.get(f"/api/uploads/{filas[1]}", headers=auth_headers).json()["pendiente_moderacion"] is True
