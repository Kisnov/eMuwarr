"""library_items.raw_json guarda solo lo que se lee de él (esquema 58): cada capítulo llevaba la serie entera de Sonarr
y 28.000 filas ocupaban 427 MB. La sincronización escribe la lista blanca y la migración recorta lo que ya había."""
from __future__ import annotations

import json
import re
import sqlite3
import types
from pathlib import Path

import pytest

import emuwarez_uploadrr
from emuwarez_uploadrr import database
from emuwarez_uploadrr.database import connect, init_db, set_state
from emuwarez_uploadrr.integrations.radarr import movie_to_library_item
from emuwarez_uploadrr.integrations.sonarr import (
    episode_to_library_item, season_to_library_item, series_to_library_item,
)
from emuwarez_uploadrr.services.library import get_dashboard_recommendations
from emuwarez_uploadrr.services.nomenclature import anio_de_temporada

_RELLENO = "x" * 4000  # lo que pesa de verdad una ficha de *Arr: sinopsis, imágenes, estadísticas…

PELICULA = {"id": 42, "title": "Pelicula", "year": 2020, "tmdbId": 1, "added": "2024-03-01T10:00:00Z",
            "monitored": True, "status": "released", "overview": _RELLENO,
            "images": [{"coverType": "poster", "remoteUrl": "https://img/p.jpg"}],
            "movieFile": {"id": 9, "path": "/m/Pelicula.mkv", "size": 1, "mediaInfo": {"videoCodec": "x264"}}}
SERIE = {"id": 7, "title": "Serie", "year": 2019, "tmdbId": 2, "added": "2023-05-01T10:00:00Z", "monitored": True,
         "status": "continuing", "overview": _RELLENO, "images": [{"coverType": "poster", "remoteUrl": "https://img/s.jpg"}],
         "seasons": [{"seasonNumber": 1, "monitored": True, "statistics": {"episodeCount": 3, "episodeFileCount": 0}}]}
TEMPORADA = SERIE["seasons"][0]


def _capitulo(numero, **campos):
    return {"id": 100 + numero, "seriesId": 7, "seasonNumber": 1, "episodeNumber": numero, "title": f"Cap {numero}",
            "airDate": f"2021-0{numero}-01", "airDateUtc": f"2021-0{numero}-01T20:00:00Z", "monitored": True,
            "hasFile": False, "overview": _RELLENO, **campos}


CAPITULOS = [_capitulo(1), _capitulo(2, monitored=False), _capitulo(3, title="TBA"),
             _capitulo(4, airDate="2999-01-01", airDateUtc="2999-01-01T20:00:00Z")]
_INSTANCIA = {"instance_id": 1, "instance_name": "Arr"}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    c = connect(tmp_path / "test.db")
    init_db(c)
    yield c
    c.close()


_COLUMNAS = ("item_type", "title", "year", "instance_type", "external_id", "tmdb_id", "season_number",
             "episode_number", "parent_id", "local_status")


def _insertar(conn, item, **extra) -> int:
    fila = {**{c: getattr(item, c) for c in _COLUMNAS}, "emu_in_catalog": 0, **extra}
    return conn.execute(f"INSERT INTO library_items ({', '.join(fila)}) VALUES ({', '.join('?' * len(fila))})",
                        tuple(fila.values())).lastrowid


def _biblioteca_como_antes(conn) -> dict[str, int]:
    """Las filas con el raw_json que escribía la sincronización hasta el esquema 57."""
    pelicula = _insertar(conn, movie_to_library_item(PELICULA, **_INSTANCIA), raw_json=json.dumps(PELICULA),
                         local_status="Faltante")
    serie = _insertar(conn, series_to_library_item(SERIE, **_INSTANCIA), raw_json=json.dumps(SERIE))
    temporada = _insertar(conn, season_to_library_item(SERIE, TEMPORADA, parent_id=serie, **_INSTANCIA),
                          raw_json=json.dumps({"series_id": 7, "season": TEMPORADA}))
    for cap in CAPITULOS:
        _insertar(conn, episode_to_library_item(cap, None, SERIE, parent_id=temporada, **_INSTANCIA),
                  raw_json=json.dumps({"series": SERIE, "episode": cap, "episodeFile": None}))
    conn.commit()
    return {"movie": pelicula, "series": serie, "season": temporada}


def _migrar_desde_57(conn) -> None:
    set_state(conn, "schema_version", "57")
    conn.commit()
    init_db(conn)


def _raw(conn, item_type) -> list:
    return [json.loads(f[0]) if f[0] else None for f in conn.execute(
        "SELECT raw_json FROM library_items WHERE item_type = ? ORDER BY id", (item_type,))]


def _lo_que_se_lee(conn, ids) -> tuple:
    temporada = dict(conn.execute("SELECT * FROM library_items WHERE id = ?", (ids["season"],)).fetchone())
    return get_dashboard_recommendations(conn), anio_de_temporada(temporada, conn)


def test_la_sincronizacion_guarda_solo_lo_que_se_lee():
    assert json.loads(movie_to_library_item(PELICULA, **_INSTANCIA).raw_json) == {
        "added": "2024-03-01T10:00:00Z", "monitored": True, "status": "released"}
    assert json.loads(series_to_library_item(SERIE, **_INSTANCIA).raw_json) == {"added": "2023-05-01T10:00:00Z"}
    assert season_to_library_item(SERIE, TEMPORADA, parent_id=1, **_INSTANCIA).raw_json is None
    assert json.loads(episode_to_library_item(CAPITULOS[0], None, SERIE, **_INSTANCIA).raw_json) == {"episode": {
        "airDate": "2021-01-01", "airDateUtc": "2021-01-01T20:00:00Z", "monitored": True, "title": "Cap 1"}}


def test_la_migracion_deja_lo_mismo_que_la_sincronizacion_y_se_lee_igual(conn):
    ids = _biblioteca_como_antes(conn)
    antes = _lo_que_se_lee(conn, ids)
    recs, anio = antes
    assert anio == 2021
    assert {(r["rec_group"], r["title"]) for r in recs} >= {(3, "Pelicula")}
    assert any(r["rec_group"] == 3 and r.get("missing_count") == 1 for r in recs)  # solo el capítulo 1 cuenta

    _migrar_desde_57(conn)

    assert _lo_que_se_lee(conn, ids) == antes
    assert _raw(conn, "movie") == [json.loads(movie_to_library_item(PELICULA, **_INSTANCIA).raw_json)]
    assert _raw(conn, "series") == [json.loads(series_to_library_item(SERIE, **_INSTANCIA).raw_json)]
    assert _raw(conn, "season") == [None]
    assert _raw(conn, "episode") == [json.loads(episode_to_library_item(c, None, SERIE, **_INSTANCIA).raw_json)
                                     for c in CAPITULOS]


def test_un_raw_json_roto_se_vacia_sin_parar_la_migracion(conn):
    conn.execute("INSERT INTO library_items (item_type, title, local_status, raw_json) "
                 "VALUES ('movie', 'Rota', 'Faltante', '{no es json')")
    _migrar_desde_57(conn)
    assert _raw(conn, "movie") == [None]


def _tamano(conn) -> int:
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return Path(conn.execute("PRAGMA database_list").fetchone()[2]).stat().st_size


def _libres(conn) -> int:
    return conn.execute("PRAGMA freelist_count").fetchone()[0]


def test_la_migracion_compacta_la_base(conn):
    for _ in range(60):
        _biblioteca_como_antes(conn)
    antes = _tamano(conn)

    _migrar_desde_57(conn)

    assert _libres(conn) == 0
    assert _tamano(conn) < antes / 5


def _disco_libre(monkeypatch, *libres):
    """El sitio libre que ve cada comprobación, por orden: la del recorte y la del VACUUM."""
    quedan = iter(libres)
    monkeypatch.setattr(database.shutil, "disk_usage", lambda _ruta: types.SimpleNamespace(free=next(quedan)))


def test_sin_sitio_para_el_wal_no_recorta_y_arranca_igual(conn, monkeypatch, caplog):
    _biblioteca_como_antes(conn)
    _disco_libre(monkeypatch, 0)

    _migrar_desde_57(conn)

    assert conn.execute("SELECT value FROM app_state WHERE key = 'schema_version'").fetchone()[0] == "58"
    assert _raw(conn, "series") == [SERIE]
    assert "No hay sitio para recortar" in caplog.text


def test_sin_sitio_para_compactar_recorta_pero_no_compacta(conn, monkeypatch, caplog):
    for _ in range(20):
        _biblioteca_como_antes(conn)
    _disco_libre(monkeypatch, 2**40, 0)

    _migrar_desde_57(conn)

    assert _raw(conn, "series")[0] == {"added": "2023-05-01T10:00:00Z"}
    assert _libres(conn) > 0
    assert "No hay sitio para compactar" in caplog.text
    assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1  # el recorte lo deja como estaba


def _falla(monkeypatch, cual, error="database or disk is full"):
    """La sentencia que cumpla `cual` falla como lo haría con el disco lleno a medias."""
    original = database._Conexion.execute

    def execute(self, sql, *args):
        if cual(sql.strip()):
            raise sqlite3.OperationalError(error)
        return original(self, sql, *args)
    monkeypatch.setattr(database._Conexion, "execute", execute)


def _version(conn) -> str:
    return conn.execute("SELECT value FROM app_state WHERE key = 'schema_version'").fetchone()[0]


def test_si_el_disco_se_llena_al_recortar_arranca_igual_y_no_deshace_lo_de_antes(conn, monkeypatch, caplog):
    _biblioteca_como_antes(conn)
    conn.execute("UPDATE library_items SET arr_queue_raw_json = '{}'")  # lo vacía otra migración de la cascada (48)
    _falla(monkeypatch, lambda sql: sql.startswith("UPDATE library_items SET raw_json"))

    _migrar_desde_57(conn)

    assert _version(conn) == "58"
    assert conn.execute("SELECT COUNT(*) FROM library_items WHERE arr_queue_raw_json IS NOT NULL").fetchone()[0] == 0
    assert _raw(conn, "series") == [SERIE]
    assert "No se pudo recortar raw_json" in caplog.text


@pytest.mark.parametrize("sentencia, aviso", [("VACUUM", "No se pudo compactar"),
                                              ("PRAGMA wal_checkpoint(TRUNCATE)", "No se pudo vaciar el WAL")])
def test_si_falla_la_compactacion_arranca_igual(conn, monkeypatch, caplog, sentencia, aviso):
    _biblioteca_como_antes(conn)
    _falla(monkeypatch, lambda sql: sql == sentencia, "disk I/O error")

    _migrar_desde_57(conn)

    assert _version(conn) == "58"
    assert _raw(conn, "series") == [{"added": "2023-05-01T10:00:00Z"}]
    assert aviso in caplog.text


# raw_json de otras tablas, que esta lista blanca no toca: request_items (claimed_by) y nomenclature_proposals.
_OTRAS_TABLAS_SQL = re.compile(r"^\$\.claimed_by\b")
_OTRAS_TABLAS_PY = re.compile(r"json\.loads\((req|proposal)\.get\(\"raw_json\"\)")


def _caminos(d: dict, prefijo: str = "$"):
    for clave, valor in d.items():
        yield f"{prefijo}.{clave}"
        if isinstance(valor, dict):
            yield from _caminos(valor, f"{prefijo}.{clave}")


def test_la_lista_blanca_cubre_todo_lo_que_el_codigo_lee_de_raw_json():
    guardado = set()
    for item in (movie_to_library_item(PELICULA, **_INSTANCIA), series_to_library_item(SERIE, **_INSTANCIA),
                 episode_to_library_item(CAPITULOS[0], None, SERIE, **_INSTANCIA)):
        guardado |= set(_caminos(json.loads(item.raw_json)))

    leido_sql, leido_py = set(), set()
    for fichero in Path(emuwarez_uploadrr.__file__).parent.rglob("*.py"):
        texto = fichero.read_text(encoding="utf-8")
        leido_sql |= {c for c in re.findall(r"json_extract\((?:\w+\.)?raw_json,\s*'(\$[^']*)'\)", texto)
                      if not _OTRAS_TABLAS_SQL.match(c)}
        for linea in texto.splitlines():
            if "json.loads(" in linea and "raw_json" in linea and not _OTRAS_TABLAS_PY.search(linea):
                encontrado = re.search(r"json\.loads\(item\.raw_json\)\.get\(\"(\w+)\"\)", linea)
                assert encontrado, f"{fichero.name}: lectura de raw_json sin clasificar: {linea.strip()}"
                leido_py.add(f"$.{encontrado.group(1)}")

    assert len(leido_sql) >= 7 and leido_py == {"$.status"}  # que las expresiones sigan encontrando algo
    assert leido_sql | leido_py <= guardado
