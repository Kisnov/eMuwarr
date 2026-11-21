"""Lo guardado no se queda atrás de lo que hay: el cruce con las peticiones tras cada sincronización de biblioteca,
qué petición se lleva una fila que casa con varias y un catálogo de eMuwarez sin torrents repetidos.

Bloque 5 del análisis de la base de 2026-11-19 (INT-1, INT-2, INT-3). INT-4 está en test_mis_torrents.py e INT-5 en
test_library_sync.py.
"""
from __future__ import annotations

import pytest

from emuwarez_uploadrr.database import connect, db_path, init_db
from emuwarez_uploadrr.models import RequestItem
from emuwarez_uploadrr.services import emu_catalog
from emuwarez_uploadrr.services import requests as reqs
from emuwarez_uploadrr.services.library.sync import recompute_after_sync
from emuwarez_uploadrr.services.settings import set_setting


@pytest.fixture
def conn(isolated_db):
    c = connect(db_path())
    init_db(c)
    yield c
    c.close()


def _pelicula(conn, tmdb: str, resolucion: str = "1080p") -> int:
    return conn.execute(
        "INSERT INTO library_items(item_type, title, year, local_status, tmdb_id, resolution, source, video_codec) "
        "VALUES ('movie', 'Peli', 2020, 'Disponible', ?, ?, 'WEB-DL', 'x264')", (tmdb, resolucion)).lastrowid


def _peticion(conn, emu_id: str, tmdb: str, resolucion: str = "1080p") -> int:
    reqs.upsert_request(conn, RequestItem(
        id=None, emu_id=emu_id, title="Peli", category="Películas", tmdb_id=tmdb,
        requested_resolution=resolucion, requested_source="WEB-DL"))
    conn.commit()
    return conn.execute("SELECT id FROM request_items WHERE emu_id = ?", (emu_id,)).fetchone()[0]


def _fila(conn, item_id: int):
    return conn.execute("SELECT request_status, request_id FROM library_items WHERE id = ?", (item_id,)).fetchone()


# ---------------------------------------------------------------------------
# INT-1: la sincronización de biblioteca vuelve a cruzar las peticiones
# ---------------------------------------------------------------------------

def test_tras_sincronizar_la_biblioteca_lo_recien_bajado_ya_sale_cruzado(conn):
    peticion = _peticion(conn, "r1", "900")
    peli = _pelicula(conn, "900")  # lo que Radarr acaba de bajar
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM request_matches").fetchone()[0] == 0

    recompute_after_sync(conn)

    cruce = conn.execute("SELECT library_item_id, request_item_id FROM request_matches").fetchone()
    assert tuple(cruce) == (peli, peticion)
    assert _fila(conn, peli)["request_status"] != "Sin petición" and _fila(conn, peli)["request_id"] == peticion
    assert conn.execute("SELECT readiness_status FROM request_items").fetchone()[0] is not None


def test_con_una_sincronizacion_de_peticiones_en_marcha_lo_recien_bajado_se_cruza_igual(conn):
    _peticion(conn, "r1", "900")
    _pelicula(conn, "900")
    conn.commit()

    with reqs._SYNC_EN_CURSO:
        reqs.volver_a_cruzar_tras_la_biblioteca(conn)

    assert conn.execute("SELECT COUNT(*) FROM request_matches").fetchone()[0] == 1


def test_el_cruce_va_de_uno_en_uno_y_los_posters_no_lo_retienen(conn, monkeypatch):
    set_setting(conn, "emu.api_key", "clave")
    conn.commit()
    visto = []
    monkeypatch.setattr(reqs.emu, "emu_fetch_all_pages", lambda *a, **kw: ([], 0))
    monkeypatch.setattr(reqs, "_cruzar_todas", lambda c: visto.append(("cruce", reqs._CRUZANDO.locked())) or (0, set()))
    monkeypatch.setattr(
        reqs, "_notify_new_compatible_matches", lambda c, nuevas: visto.append(("avisos", reqs._CRUZANDO.locked())))
    monkeypatch.setattr(reqs, "rellenar_posters", lambda c: visto.append(("pósters", reqs._CRUZANDO.locked())) or 0)

    monkeypatch.setattr(reqs, "_casar", lambda *a, **kw: visto.append(("una sola", reqs._CRUZANDO.locked())))

    reqs._importar_peticiones(conn)
    peticion = _peticion(conn, "r1", "900")
    reqs.volver_a_cruzar_tras_la_biblioteca(conn)
    reqs.match_single_request(conn, peticion)

    assert visto == [
        ("cruce", True), ("avisos", False), ("pósters", False), ("cruce", True), ("avisos", False), ("una sola", True)]


def test_si_el_cruce_falla_la_sincronizacion_de_biblioteca_no_se_cae(conn, monkeypatch, caplog):
    _peticion(conn, "r1", "900")

    def revienta(_conn):
        raise RuntimeError("base ocupada")

    monkeypatch.setattr(reqs, "_cruzar_todas", revienta)

    recompute_after_sync(conn)

    assert "No se pudieron volver a cruzar las peticiones" in caplog.text


# ---------------------------------------------------------------------------
# INT-2: de dos peticiones a la misma fila, se queda la de mejor cruce
# ---------------------------------------------------------------------------

def test_de_dos_peticiones_a_la_misma_fila_se_queda_la_de_mejor_cruce_venga_en_el_orden_que_venga(conn):
    primera, segunda = _pelicula(conn, "901"), _pelicula(conn, "902")
    buena_antes = _peticion(conn, "a", "901", "1080p")
    _peticion(conn, "b", "901", "2160p")
    _peticion(conn, "c", "902", "2160p")
    buena_despues = _peticion(conn, "d", "902", "1080p")

    reqs.match_all_requests(conn)

    estados = dict(conn.execute("SELECT request_item_id, status FROM request_matches").fetchall())
    assert estados[buena_antes] == estados[buena_despues] == "Compatible"
    assert "Compatible" not in (estados[buena_antes + 1], estados[buena_despues - 1])
    assert tuple(_fila(conn, primera)) == ("Compatible", buena_antes)
    assert tuple(_fila(conn, segunda)) == ("Compatible", buena_despues)


def test_recalcular_una_sola_peticion_no_le_quita_la_fila_a_otra_de_mejor_cruce(conn):
    peli = _pelicula(conn, "903")
    buena = _peticion(conn, "a", "903", "1080p")
    peor = _peticion(conn, "b", "903", "2160p")
    reqs.match_all_requests(conn)
    assert tuple(_fila(conn, peli)) == ("Compatible", buena)

    reqs.match_single_request(conn, peor)
    assert tuple(_fila(conn, peli)) == ("Compatible", buena)

    reqs.match_single_request(conn, buena)
    assert tuple(_fila(conn, peli)) == ("Compatible", buena)
    assert conn.execute("SELECT COUNT(*) FROM request_matches").fetchone()[0] == 2


def test_si_la_que_tenia_la_fila_empeora_o_deja_de_casar_la_fila_pasa_a_la_siguiente(conn):
    peli = _pelicula(conn, "904")
    antigua = _peticion(conn, "a", "904", "1080p")
    nueva = _peticion(conn, "b", "904", "1080p")
    reqs.match_all_requests(conn)
    assert tuple(_fila(conn, peli)) == ("Compatible", antigua)

    conn.execute("UPDATE request_items SET requested_resolution = '2160p' WHERE id = ?", (antigua,))
    reqs.match_single_request(conn, antigua)
    assert tuple(_fila(conn, peli)) == ("Compatible", nueva)

    conn.execute("UPDATE request_items SET requested_resolution = '1080p' WHERE id = ?", (antigua,))
    reqs.match_single_request(conn, antigua)
    assert tuple(_fila(conn, peli)) == ("Compatible", antigua)

    conn.execute("UPDATE request_items SET tmdb_id = '999', title = 'Otra cosa' WHERE id = ?", (antigua,))
    assert reqs.match_single_request(conn, antigua) is None
    assert tuple(_fila(conn, peli)) == ("Compatible", nueva)


def test_una_peticion_de_temporada_se_reparte_la_fila_de_su_temporada(conn):
    serie = conn.execute(
        "INSERT INTO library_items(item_type, title, year, local_status, tmdb_id) "
        "VALUES ('series', 'Serie', 2020, 'Disponible', '905')").lastrowid
    temporada = conn.execute(
        "INSERT INTO library_items(item_type, title, year, local_status, tmdb_id, parent_id, season_number) "
        "VALUES ('season', 'Serie', 2020, 'Disponible', '905', ?, 2)", (serie,)).lastrowid
    for emu_id in ("a", "b"):
        reqs.upsert_request(conn, RequestItem(
            id=None, emu_id=emu_id, title="Serie", category="Series", tmdb_id="905", req_scope="season", req_season=2))
    conn.commit()
    antigua, nueva = (r[0] for r in conn.execute("SELECT id FROM request_items ORDER BY id"))
    reqs.match_all_requests(conn)
    assert _fila(conn, temporada)["request_id"] == antigua and _fila(conn, serie)["request_id"] is None

    conn.execute("UPDATE request_items SET tmdb_id = '999', title = 'Otra cosa' WHERE id = ?", (antigua,))
    reqs.match_single_request(conn, antigua)

    assert _fila(conn, temporada)["request_id"] == nueva


# ---------------------------------------------------------------------------
# INT-3: el catálogo no guarda dos veces el mismo torrent
# ---------------------------------------------------------------------------

def _torrent(torrent_id: str, nombre: str = "Peli") -> dict:
    return {"torrent_id": torrent_id, "category": "Películas", "tmdb_id": "900", "tvdb_id": None,
            "season_number": None, "resolution": "1080p", "source": "WEB-DL", "codec": "x264", "has_spanish": 1,
            "size": 1, "name": nombre}


def _catalogo(conn) -> list[tuple]:
    return [tuple(f) for f in conn.execute("SELECT torrent_id, name FROM emu_catalog ORDER BY torrent_id")]


def test_una_descarga_completa_con_un_torrent_repetido_lo_guarda_una_vez(conn):
    emu_catalog._store_catalog(conn, [_torrent("1"), _torrent("2"), _torrent("2"), _torrent("3")])
    assert _catalogo(conn) == [("1", "Peli"), ("2", "Peli"), ("3", "Peli")]


def test_una_lectura_incremental_no_repite_lo_que_ya_estaba(conn):
    emu_catalog._store_catalog(conn, [_torrent("1"), _torrent("2")])

    emu_catalog._append_catalog(conn, [_torrent("2", "Peli renombrada"), _torrent("3"), _torrent("3")])

    assert _catalogo(conn) == [("1", "Peli"), ("2", "Peli renombrada"), ("3", "Peli")]
