"""Esquema 59: las tablas y columnas que nadie escribía ni leía se quitan al migrar, y una base nueva no las crea."""
from __future__ import annotations

import pytest

from emuwarez_uploadrr.database import _COLUMNAS_SIN_USO, _TABLAS_SIN_USO, connect, db_path, init_db, set_state


@pytest.fixture
def conn(isolated_db):
    c = connect(db_path())
    init_db(c)
    yield c
    c.close()


def _tablas(conn) -> set[str]:
    return {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columnas(conn, tabla: str) -> set[str]:
    return {f[1] for f in conn.execute(f"PRAGMA table_info({tabla})")}


def _sin_rastro(conn) -> bool:
    return not _tablas(conn) & set(_TABLAS_SIN_USO) and not any(
        _columnas(conn, tabla) & set(columnas) for tabla, columnas in _COLUMNAS_SIN_USO)


def _como_en_el_esquema_58(conn) -> None:
    """Lo que tenía de más una base anterior, con algo dentro."""
    for tabla in _TABLAS_SIN_USO:
        conn.execute(f"CREATE TABLE {tabla} (id INTEGER PRIMARY KEY, upload_job_id INTEGER, nota TEXT)")
        conn.execute(f"CREATE INDEX idx_prueba_{tabla} ON {tabla}(upload_job_id)")
        conn.execute(f"INSERT INTO {tabla} (nota) VALUES ('resto')")
    for tabla, columnas in _COLUMNAS_SIN_USO:
        for columna in columnas:
            tipo = "INTEGER NOT NULL DEFAULT 0" if columna == "is_example" else "TEXT"
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")
    conn.execute("INSERT INTO library_items (item_type, title, local_status, arr_queue_raw_json, diagnostic_reasons_json)"
                 " VALUES ('movie', 'Peli', 'Disponible', '{}', '[]')")
    conn.execute("INSERT INTO request_items (emu_id, title, emu_catalog_result) VALUES ('7', 'Real', 'found')")
    set_state(conn, "schema_version", "58")
    conn.commit()


def test_una_base_nueva_no_las_tiene(conn):
    assert _sin_rastro(conn)


def test_al_migrar_se_quitan_y_lo_demas_queda_como_estaba(conn):
    _como_en_el_esquema_58(conn)
    assert not _sin_rastro(conn)

    init_db(conn)

    assert _sin_rastro(conn)
    assert [tuple(f) for f in conn.execute("SELECT item_type, title, local_status FROM library_items")] == [
        ("movie", "Peli", "Disponible")]
    assert [tuple(f) for f in conn.execute("SELECT emu_id, title FROM request_items")] == [("7", "Real")]
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_repetir_la_cascada_no_hace_nada(conn):
    _como_en_el_esquema_58(conn)
    init_db(conn)
    antes = sorted(f[0] or "" for f in conn.execute("SELECT sql FROM sqlite_master"))

    set_state(conn, "schema_version", "58")
    conn.commit()
    init_db(conn)

    assert sorted(f[0] or "" for f in conn.execute("SELECT sql FROM sqlite_master")) == antes

