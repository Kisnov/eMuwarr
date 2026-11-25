"""El catálogo de cada tracker con clave (N.O.B.S, Milnueve) junto al de eMuwarez: se lee con su cupo de la API, se
guarda con su `tracker` y la comparación del asistente lo lee por tracker; las columnas de la biblioteca siguen siendo
de eMuwarez."""
from __future__ import annotations

import json
import time

import pytest

from emuwarez_uploadrr.database import connect, db_path, get_state, init_db
from emuwarez_uploadrr.integrations import limitador_compartido as lc
from emuwarez_uploadrr.integrations.base import IntegrationError
from emuwarez_uploadrr.integrations.emu_limitador import Limitador
from emuwarez_uploadrr.services import emu_catalog as ec
from emuwarez_uploadrr.services.settings import set_setting
from emuwarez_uploadrr.services.trackers import EMU, NOBS


@pytest.fixture
def db_conn(isolated_db):
    conn = connect(db_path())
    init_db(conn)
    set_setting(conn, "emu.api_key", "KEY-EMU")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def descargas(monkeypatch):
    """Lo que devuelve cada tracker (por su nombre en el limitador) y con qué se le llamó."""
    class Falsa:
        torrents: dict[str, list] = {}
        llamadas: list[dict] = []
        fallo: dict[str, Exception] = {}

    Falsa.torrents, Falsa.llamadas, Falsa.fallo = {}, [], {}

    def conexion(api_key, base_url=None, *, interactiva=True, tracker="EMU"):
        if tracker in Falsa.fallo:
            raise Falsa.fallo[tracker]
        return {"data": []}

    def descarga(api_key, base_url=None, progress=None, stop_after_id=None, tracker="EMU", **kw):
        Falsa.llamadas.append({"api_key": api_key, "base_url": base_url, "tracker": tracker})
        return Falsa.torrents.get(tracker, [])

    monkeypatch.setattr(ec.emu, "emu_test_connection", conexion)
    monkeypatch.setattr(ec.emu, "emu_fetch_all_torrents", descarga)
    return Falsa


def _peli(tid, tmdb, resolution="1080p"):
    return {"id": tid, "attributes": {"name": f"Peli (2020) {resolution} WEB-DL", "category": "Películas",
                                      "tmdb_id": tmdb, "resolution": resolution, "type": "WEB-DL"}}


def _pelicula(conn, tmdb):
    item = conn.execute("INSERT INTO library_items(item_type, title, tmdb_id, resolution, source, local_status) "
                        "VALUES ('movie', 'Peli', ?, '1080p', 'WEB-DL', 'Disponible')", (tmdb,)).lastrowid
    conn.commit()
    return item


def test_el_catalogo_de_otro_tracker_va_con_su_clave_su_url_y_su_cupo(db_conn, descargas):
    set_setting(db_conn, "nobs.api_key", "KEY-NOBS")
    item = _pelicula(db_conn, "7")
    descargas.torrents = {"EMU": [_peli(1, 7)], "NOBS": [_peli(1, 7, "2160p"), _peli(2, 8)]}
    ec.sync_emu_catalog(db_conn, force=True, mode="full")
    marcas = {k: get_state(db_conn, k) for k in (ec.SYNC_STATE_KEY, ec.FULL_SYNC_STATE_KEY)}

    r = ec.sync_emu_catalog(db_conn, force=True, mode="incremental", tracker=NOBS)

    assert r["torrents"] == 2 and r["incremental"] is False  # sin nada suyo guardado, la primera es completa
    assert descargas.llamadas[-1] == {"api_key": "KEY-NOBS", "base_url": NOBS.url, "tracker": "NOBS"}
    por_tracker = dict(db_conn.execute("SELECT tracker, COUNT(*) FROM emu_catalog GROUP BY tracker").fetchall())
    assert por_tracker == {"emu": 1, "nobs": 2}
    assert get_state(db_conn, f"{ec.MAX_ID_STATE_KEY}.nobs") == "2"
    assert get_state(db_conn, f"{ec.SYNC_COMPLETED_STATE_KEY}.nobs")
    # La biblioteca y los límites de la tanda siguen siendo los de eMuwarez.
    assert db_conn.execute("SELECT emu_in_catalog FROM library_items WHERE id = ?", (item,)).fetchone()[0] == 1
    assert {k: get_state(db_conn, k) for k in marcas} == marcas


def test_la_comparacion_lee_el_catalogo_del_tracker_pedido(db_conn, descargas):
    set_setting(db_conn, "nobs.api_key", "KEY-NOBS")
    item = _pelicula(db_conn, "7")
    descargas.torrents = {"EMU": [_peli(1, 7)], "NOBS": [_peli(5, 7, "2160p")]}
    ec.sync_emu_catalog(db_conn, force=True, mode="full")

    sin_leer = ec.get_item_comparison(db_conn, item, tracker=NOBS)
    assert sin_leer == {"available": False, "matches": [],
                        "message": "El catálogo de N.O.B.S se lee al sincronizar el de eMuwarez."}

    ec.sync_emu_catalog(db_conn, force=True, tracker=NOBS)
    emu, nobs = (ec.get_item_comparison(db_conn, item, tracker=t) for t in (EMU, NOBS))
    assert [(m["resolution"], m["veredicto"]) for m in emu["matches"]] == [("1080p", "igual")]
    assert [(m["resolution"], m["veredicto"]) for m in nobs["matches"]] == [("2160p", "peor")]
    assert nobs["synced_at"] == get_state(db_conn, f"{ec.SYNC_COMPLETED_STATE_KEY}.nobs")


def _esperar_fin(plazo=5.0):
    limite = time.monotonic() + plazo
    while not ec.get_sync_progress()["done"] and time.monotonic() < limite:
        time.sleep(0.02)


@pytest.fixture
def progreso_limpio(monkeypatch):
    monkeypatch.setattr(ec, "publicar", lambda *a, **kw: None)
    ec._set_progress(active=False, done=False, error=None, phase="")
    yield
    ec._set_progress(active=False, done=False, error=None, phase="")


def test_la_tanda_lee_emuwarez_y_luego_los_trackers_con_clave(db_conn, descargas, progreso_limpio):
    set_setting(db_conn, "mnv.api_key", "KEY-MNV")  # N.O.B.S sin clave: no se lee
    db_conn.commit()
    descargas.torrents = {"EMU": [_peli(1, 7)]}
    descargas.fallo = {"MNV": IntegrationError("no responde")}

    assert ec.start_emu_sync_background(mode="full") == {"status": "started"}
    _esperar_fin()

    assert [c["tracker"] for c in descargas.llamadas] == ["EMU"]  # Milnueve falló al conectar
    progreso = ec.get_sync_progress()
    assert progreso["error"] is None and progreso["error_otros"].startswith("Milnueve: No se pudo conectar con Milnueve")
    assert progreso["torrents"] == 1  # el aviso del final cuenta los de eMuwarez
    # Lo de eMuwarez vale: su fallo no deshace la marca de la tanda.
    conn = connect(db_path())
    try:
        assert get_state(conn, ec.SYNC_COMPLETED_STATE_KEY) and get_state(conn, ec.FULL_SYNC_STATE_KEY)
    finally:
        conn.close()


def test_la_ruta_de_la_comparacion_valida_el_tracker(client, auth_headers):
    assert client.get("/api/library/1/emu-comparison?tracker=xyz", headers=auth_headers).status_code == 422
    r = client.get("/api/library/1/emu-comparison?tracker=nobs", headers=auth_headers)
    assert r.status_code == 200 and r.json()["available"] is False


@pytest.mark.skipif(lc.fcntl is None, reason="sin fcntl no hay límite entre procesos")
def test_las_peticiones_a_otro_tracker_cuentan_en_su_fichero(tmp_path):
    """El mismo fichero que usa Uploadrr-EMU al subir a N.O.B.S con esa clave."""
    directorio = tmp_path / "limitador"
    Limitador(directorio=lambda: directorio).esperar_turno("KEY-NOBS", tracker="NOBS")
    assert len(json.loads(lc.ruta(directorio, "NOBS", "KEY-NOBS").read_text())["marcas"]) == 1
    assert not lc.ruta(directorio, "EMU", "KEY-NOBS").exists()


def test_las_categorias_de_series_de_los_otros_trackers_son_de_series():
    """N.O.B.S llama «TV» a las series: un pack sin temporada en el nombre no es una película con el mismo tmdb."""
    fila = {"category": "TV", "name": "Chernobyl (2019) 1080p WEB-DL", "season_number": None}
    assert ec._is_series(fila) and not ec._is_series({**fila, "category": "Movies"})
