"""Cómo abre la app SQLite y cuándo suelta el bloqueo de escritura (análisis de la base de 2026-11-19, bloque 1)."""
from __future__ import annotations

from emuwarez_uploadrr.database import connect, init_db, set_state
from emuwarez_uploadrr.services import maintenance_service as mant
from emuwarez_uploadrr.services import tmdb_titles


def test_cada_conexion_sale_con_las_pragma_de_la_app(tmp_path):
    conn = connect(tmp_path / "t.db")
    pragma = {p: conn.execute(f"PRAGMA {p}").fetchone()[0]
              for p in ("journal_mode", "synchronous", "journal_size_limit", "secure_delete", "busy_timeout",
                        "foreign_keys")}
    conn.close()
    assert pragma == {"journal_mode": "wal", "synchronous": 1, "journal_size_limit": 67108864, "secure_delete": 2,
                      "busy_timeout": 5000, "foreign_keys": 1}  # synchronous 1 = NORMAL, secure_delete 2 = FAST


def test_el_mantenimiento_refresca_las_estadisticas_de_tablas_que_su_conexion_no_ha_leido(tmp_path):
    """La conexión del planificador no lee la biblioteca: con un PRAGMA optimize a secas, unas estadísticas de cuando
    la biblioteca tenía 100 filas seguían diciendo 100 con 3.000."""
    base = tmp_path / "t.db"
    conn = connect(base)
    init_db(conn)

    def peliculas(desde, hasta):
        conn.executemany("INSERT INTO library_items (item_type, title, external_id, local_status) "
                         "VALUES ('movie', ?, ?, 'Disponible')", [(f"P{i}", str(i)) for i in range(desde, hasta)])
        conn.commit()

    peliculas(0, 100)
    conn.execute("ANALYZE")
    conn.commit()
    peliculas(100, 3000)
    conn.close()

    planificador = connect(base)
    mant.optimizar_base(planificador)

    assert planificador.execute("SELECT stat FROM sqlite_stat1 WHERE idx = 'idx_library_status'").fetchone()[0] \
        .startswith("3000 ")
    planificador.close()


def test_mientras_se_espera_a_tmdb_otra_conexion_puede_escribir(tmp_path):
    """La sincronización dejaba sin confirmar el título anterior y pedía el siguiente con el bloqueo cogido."""
    base = tmp_path / "t.db"
    conn = connect(base)
    init_db(conn)
    set_state(conn, "antes", "1")  # una escritura a medias, como el INSERT del título anterior

    def pedir():
        otra = connect(base)
        otra.execute("PRAGMA busy_timeout = 0")
        set_state(otra, "durante", "1")
        otra.commit()
        otra.close()
        return "Título"

    assert tmdb_titles._resolve_title(conn, "movie", "1", "es-ES", "Fallback", pedir) == "Título"
    conn.close()
