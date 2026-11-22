"""Fase A del plan multitracker: cada subida, petición, fila del catálogo, de «Tus subidas» y del historial del perfil
lleva su tracker (esquema 60). Las filas de antes son de eMuwarez, el mismo id en dos trackers son dos filas y rehacer
`request_items` no se lleva por delante sus cruces."""
from __future__ import annotations

import sqlite3

import pytest

from emuwarez_uploadrr import database
from emuwarez_uploadrr.database import SCHEMA_VERSION, TRACKER_POR_DEFECTO, db, olvidar_esquema, set_state
from emuwarez_uploadrr.services import trackers


@pytest.fixture(autouse=True)
def _esquema_limpio():
    olvidar_esquema()
    yield
    olvidar_esquema()


# Las cuatro tablas tal como las dejaba el esquema 59 (sin `tracker`, con la clave de un solo id).
_ESQUEMA_59 = {
    "request_items": """CREATE TABLE request_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emu_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            category TEXT,
            requested_source TEXT,
            requested_resolution TEXT,
            emu_status TEXT,
            points INTEGER,
            comments INTEGER,
            created_at TEXT,
            tmdb_id TEXT,
            imdb_id TEXT,
            url TEXT,
            author TEXT,
            author_visible INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        , reviewed_at TEXT, last_sync_at TEXT, votes INTEGER, req_scope TEXT, req_season INTEGER, req_episode INTEGER,
        readiness_status TEXT, readiness_action TEXT, readiness_best_library_item_id INTEGER, readiness_updated_at TEXT,
        mal_id TEXT, tvdb_id TEXT, last_seen_at TEXT, tmdb_poster_url TEXT, tmdb_backdrop_url TEXT,
        aviso_pendiente INTEGER NOT NULL DEFAULT 0)""",
    "emu_mis_torrents": """CREATE TABLE emu_mis_torrents (
            torrent_id INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            tamano INTEGER,
            subido_at TEXT,
            seeders INTEGER,
            leechers INTEGER,
            completados INTEGER,
            freeleech TEXT,
            doble_subida INTEGER NOT NULL DEFAULT 0
        )""",
    "emu_mis_torrents_dia": """CREATE TABLE emu_mis_torrents_dia (
            dia TEXT NOT NULL,              -- YYYY-MM-DD, local
            torrent_id INTEGER NOT NULL,
            seeders INTEGER,
            leechers INTEGER,
            completados INTEGER,
            PRIMARY KEY (torrent_id, dia)
        )""",
    "perfil_historial": """CREATE TABLE perfil_historial (
            dia TEXT PRIMARY KEY,          -- YYYY-MM-DD, local
            ratio REAL,
            subido_bytes INTEGER,
            bajado_bytes INTEGER,
            bonus REAL,
            usuario TEXT,                  -- de qué cuenta es
            actualizado_at TEXT NOT NULL
        , buffer_bytes INTEGER, seeding INTEGER, leeching INTEGER, hit_and_runs INTEGER, grupo TEXT)""",
}


def _base_del_esquema_59() -> None:
    """Una base del esquema 59 con datos: una petición con su cruce, una subida tuya con su foto y un día de perfil."""
    with db() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        for tabla, sql in _ESQUEMA_59.items():
            conn.execute(f"DROP TABLE {tabla}")
            conn.execute(sql)
        conn.execute("CREATE INDEX idx_requests_tmdb ON request_items(tmdb_id)")
        conn.execute("ALTER TABLE upload_jobs DROP COLUMN tracker")
        conn.execute("ALTER TABLE emu_catalog DROP COLUMN tracker")
        conn.execute("INSERT INTO request_items (id, emu_id, title, tmdb_id) VALUES (7, '123', 'Pedida', '550')")
        conn.execute("INSERT INTO request_items (id, emu_id, title) VALUES (9, '124', 'Borrada')")
        conn.execute("DELETE FROM request_items WHERE id = 9")
        conn.execute("INSERT INTO instances (id, type, name, url, api_key) VALUES (1, 'radarr', 'R', 'http://r', 'k')")
        conn.execute("""INSERT INTO library_items (id, instance_id, instance_type, instance_name, item_type, external_id,
                        title, local_status) VALUES (1, 1, 'radarr', 'R', 'movie', '1', 'Pedida', 'ok')""")
        conn.execute("INSERT INTO request_matches (library_item_id, request_item_id, status) VALUES (1, 7, 'match')")
        conn.execute("INSERT INTO emu_mis_torrents (torrent_id, nombre, completados) VALUES (55, 'Mía', 3)")
        conn.execute("INSERT INTO emu_mis_torrents_dia (dia, torrent_id, completados) VALUES ('2026-11-20', 55, 1)")
        conn.execute("INSERT INTO perfil_historial (dia, ratio, actualizado_at) VALUES ('2026-11-20', 1.5, 'x')")
        conn.execute("INSERT INTO emu_catalog (torrent_id, name) VALUES ('55', 'Mía')")
        set_state(conn, "schema_version", str(SCHEMA_VERSION - 1))
    olvidar_esquema()


def _clave(conn: sqlite3.Connection, tabla: str) -> list[str]:
    """Las columnas de la clave primaria, o las del índice único si la clave es el id."""
    pk = [f[1] for f in sorted(conn.execute(f"PRAGMA table_info({tabla})"), key=lambda f: f[5]) if f[5]]
    if pk != ["id"]:
        return pk
    for indice in conn.execute(f"PRAGMA index_list({tabla})"):
        if indice["unique"] and indice["origin"] == "u":
            return [c[2] for c in conn.execute(f"PRAGMA index_info({indice['name']})")]
    return pk


def test_el_por_defecto_de_la_base_es_el_de_los_trackers():
    assert TRACKER_POR_DEFECTO == trackers.POR_DEFECTO == trackers.EMU.id
    assert trackers.obtener("emu") is trackers.EMU
    with pytest.raises(ValueError, match="desconocido"):
        trackers.obtener("nobs")


def test_una_base_nueva_ya_tiene_el_tracker_en_cada_tabla():
    with db() as conn:
        for tabla in ("request_items", "emu_mis_torrents", "emu_mis_torrents_dia", "perfil_historial",
                      "upload_jobs", "emu_catalog"):
            assert "tracker" in database._columnas(conn, tabla), tabla
        assert _clave(conn, "request_items") == ["tracker", "emu_id"]
        assert _clave(conn, "emu_mis_torrents") == ["tracker", "torrent_id"]
        assert _clave(conn, "emu_mis_torrents_dia") == ["tracker", "torrent_id", "dia"]
        assert _clave(conn, "perfil_historial") == ["tracker", "dia"]


def test_la_migracion_deja_lo_de_antes_en_emuwarez_y_amplia_las_claves():
    _base_del_esquema_59()
    with db() as conn:
        assert database.get_state(conn, "schema_version") == str(SCHEMA_VERSION)
        for tabla in ("request_items", "emu_mis_torrents", "emu_mis_torrents_dia", "perfil_historial",
                      "upload_jobs", "emu_catalog"):
            assert {f["tracker"] for f in conn.execute(f"SELECT tracker FROM {tabla}")} <= {"emu"}, tabla
        assert _clave(conn, "request_items") == ["tracker", "emu_id"]
        assert _clave(conn, "perfil_historial") == ["tracker", "dia"]
        assert dict(conn.execute("SELECT id, emu_id, title, tmdb_id FROM request_items").fetchone()) == {
            "id": 7, "emu_id": "123", "title": "Pedida", "tmdb_id": "550"}
        # El cruce sigue (soltar la tabla con las claves foráneas encendidas lo habría borrado en cascada)…
        assert conn.execute("SELECT request_item_id FROM request_matches").fetchone()[0] == 7
        # …el índice de la tabla vuelve, las claves foráneas siguen encendidas y no se reutiliza el id borrado.
        assert "idx_requests_tmdb" in {i["name"] for i in conn.execute("PRAGMA index_list(request_items)")}
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        nuevo = conn.execute("INSERT INTO request_items (emu_id, title) VALUES ('200', 'Nueva')").lastrowid
        assert nuevo == 10
        # El mismo id de petición en otro tracker es otra fila; en el mismo, choca.
        conn.execute("INSERT INTO request_items (tracker, emu_id, title) VALUES ('nobs', '123', 'Otra')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO request_items (emu_id, title) VALUES ('123', 'Repetida')")
        conn.execute("INSERT INTO emu_mis_torrents (tracker, torrent_id, nombre) VALUES ('nobs', 55, 'Otra')")
        conn.execute("INSERT INTO perfil_historial (tracker, dia, actualizado_at) VALUES ('nobs', '2026-11-20', 'x')")


def test_la_migracion_dos_veces_no_rehace_nada():
    _base_del_esquema_59()
    with db() as conn:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'request_items'").fetchone()[0]
        database._migrate_tracker_en_tablas(conn)
        assert conn.execute("SELECT sql FROM sqlite_master WHERE name = 'request_items'").fetchone()[0] == sql
        assert conn.execute("SELECT COUNT(*) FROM request_matches").fetchone()[0] == 1


def test_si_falla_a_medias_no_queda_nada_rehecho(monkeypatch):
    _base_del_esquema_59()
    original = database._rehacer_con_tracker

    def falla_en_el_perfil(conn, tabla, clave):
        if tabla == "perfil_historial":
            raise sqlite3.OperationalError("disk I/O error")
        original(conn, tabla, clave)

    monkeypatch.setattr(database, "_rehacer_con_tracker", falla_en_el_perfil)
    with pytest.raises(sqlite3.OperationalError):
        with db():
            pass
    monkeypatch.setattr(database, "_rehacer_con_tracker", original)
    olvidar_esquema()
    conn = database.connect()
    try:
        assert "tracker" not in database._columnas(conn, "request_items")
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
    with db() as conn:  # el siguiente arranque la termina
        assert "tracker" in database._columnas(conn, "request_items")
        assert conn.execute("SELECT COUNT(*) FROM request_matches").fetchone()[0] == 1


def test_lo_que_se_escribe_hoy_va_a_emuwarez():
    from datetime import datetime

    from emuwarez_uploadrr.models import RequestItem
    from emuwarez_uploadrr.services import perfil_historial, requests

    with db() as conn:
        # Una petición de otro tracker con el mismo id, creada antes: el upsert no la toca ni devuelve su id.
        otra = conn.execute("INSERT INTO request_items (tracker, emu_id, title) VALUES ('nobs', '1', 'Ajena')").lastrowid
        primera = requests.upsert_request(conn, RequestItem(id=None, emu_id="1", title="Una"))
        segunda = requests.upsert_request(conn, RequestItem(id=None, emu_id="1", title="Una, corregida"))
        assert primera == segunda != otra
        assert [tuple(f) for f in conn.execute("SELECT tracker, emu_id, title FROM request_items ORDER BY id")] == [
            ("nobs", "1", "Ajena"), ("emu", "1", "Una, corregida")]
        perfil_historial.guardar_instantanea(conn, {"ratio": 2, "username": "yo"}, datetime(2026, 11, 22, 10))
        perfil_historial.guardar_instantanea(conn, {"ratio": 3, "username": "yo"}, datetime(2026, 11, 22, 11))
        assert [tuple(f) for f in conn.execute("SELECT tracker, dia, ratio FROM perfil_historial")] == [
            ("emu", "2026-11-22", 3.0)]
        # El historial de otro tracker no lo borra el cambio de usuario de eMuwarez.
        conn.execute("INSERT INTO perfil_historial (tracker, dia, usuario, actualizado_at) "
                     "VALUES ('nobs', '2026-11-22', 'otro', 'x')")
        perfil_historial.guardar_instantanea(conn, {"ratio": 1, "username": "yo2"}, datetime(2026, 11, 22, 12))
        assert conn.execute("SELECT COUNT(*) FROM perfil_historial WHERE tracker = 'nobs'").fetchone()[0] == 1
        assert [f["dia"] for f in perfil_historial.serie_cuenta(conn, 7, datetime(2026, 11, 22, 13))] == [
            "2026-11-22"]


# ---- lo que sincroniza eMuwarez solo toca filas de eMuwarez ----

def test_el_catalogo_de_emuwarez_no_pisa_ni_cuenta_el_de_otro_tracker():
    from emuwarez_uploadrr.services import emu_catalog

    with db() as conn:
        conn.execute("INSERT INTO emu_catalog (tracker, torrent_id, category, tmdb_id, name) "
                     "VALUES ('nobs', '1', 'movie', '550', 'Ajena 1080p')")
        emu_catalog._store_catalog(conn, [{"torrent_id": "1", "category": "movie", "tmdb_id": "550", "tvdb_id": None,
                                           "season_number": None, "resolution": "1080p", "source": "WEB-DL",
                                           "codec": None, "has_spanish": 1, "size": 1, "name": "Mía 1080p"}])
        emu_catalog._append_catalog(conn, [{"torrent_id": "1", "category": "movie", "tmdb_id": "550",
                                            "tvdb_id": None, "season_number": None, "resolution": "2160p",
                                            "source": "WEB-DL", "codec": None, "has_spanish": 1, "size": 1,
                                            "name": "Mía 2160p"}])
        assert [tuple(f) for f in conn.execute("SELECT tracker, name FROM emu_catalog ORDER BY tracker")] == [
            ("emu", "Mía 2160p"), ("nobs", "Ajena 1080p")]
        pelicula = {"item_type": "movie", "tmdb_id": "550"}
        assert [f["name"] for f in emu_catalog.filas_del_catalogo(conn, pelicula)] == ["Mía 2160p"]


def test_sincronizar_las_peticiones_de_emuwarez_no_borra_las_de_otro_tracker(monkeypatch):
    from emuwarez_uploadrr.services import requests as reqs
    from emuwarez_uploadrr.services.settings import set_setting

    fila = {"id": 1, "name": "Pedida", "category_id": 1, "tmdb": 550, "status": "unfilled", "bounty": "10.00",
            "created": "2025-01-01T00:00:00+00:00"}
    monkeypatch.setattr(reqs.emu, "emu_fetch_all_pages", lambda *a, **kw: ([fila], 1))
    with db() as conn:
        set_setting(conn, "emu.api_key", "clave")
        conn.execute("INSERT INTO request_items (tracker, emu_id, title) VALUES ('nobs', '99', 'Ajena')")
        conn.commit()
        reqs.sync_emu_requests(conn, force=True)
        assert sorted(tuple(f) for f in conn.execute("SELECT tracker, emu_id FROM request_items")) == [
            ("emu", "1"), ("nobs", "99")]


def test_tu_usuario_de_emuwarez_no_te_hace_dueno_de_la_reclamacion_de_otro_tracker():
    import json

    from emuwarez_uploadrr.services.compatibility import estado_emu_de, reclamada_por

    raw = json.dumps({"claimed": True, "claimed_by": "yo"})
    mia = {"tracker": "emu", "emu_status": "claimed", "raw_json": raw}
    ajena = {"tracker": "nobs", "emu_status": "claimed", "raw_json": raw}
    assert reclamada_por(mia, "yo") == "ti" and estado_emu_de(mia, "yo") is None
    assert reclamada_por(ajena, "yo") == "desconocido"
    assert estado_emu_de(ajena, "yo") == "Reclamado"
