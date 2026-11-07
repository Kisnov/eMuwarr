"""El año de una serie en eMuwarez es el del estreno de cada temporada, no el de la serie
(nomenclature.anio_de_temporada): la temporada 17 de una serie de 2000 que empezó en 2020 lleva 2020."""
from __future__ import annotations

import json

import pytest

from emuwarez_uploadrr.database import connect, init_db
from emuwarez_uploadrr.services.nomenclature import anio_de_temporada, build_nomenclature_proposal


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    c = connect(tmp_path / "test.db")
    init_db(c)
    yield c
    c.close()


def _serie(conn, *, temporada=17, year=2000):
    serie = conn.execute(
        "INSERT INTO library_items (item_type, title, year, local_status, request_status) "
        "VALUES ('series', 'Los Simpson', ?, 'Parcial', 'Sin petición')", (year,)).lastrowid
    season = conn.execute(
        "INSERT INTO library_items (item_type, title, year, local_status, request_status, parent_id, season_number) "
        "VALUES ('season', 'Los Simpson - Temporada', ?, 'Parcial', 'Sin petición', ?, ?)",
        (year, serie, temporada)).lastrowid
    return serie, season


def _capitulo(conn, season, *, numero, temporada=17, air_date=None, air_date_utc=None, raw=None, path=None):
    episodio = {k: v for k, v in (("airDate", air_date), ("airDateUtc", air_date_utc)) if v}
    raw_json = raw if raw is not None else json.dumps({"episode": episodio})
    return conn.execute(
        "INSERT INTO library_items (item_type, title, year, local_status, request_status, parent_id, "
        "season_number, episode_number, raw_json, path, quality) "
        "VALUES ('episode', 'Los Simpson', 2000, 'Disponible', 'Sin petición', ?, ?, ?, ?, ?, 'WEBDL-1080p')",
        (season, temporada, numero, raw_json, path)).lastrowid


def _item(conn, item_id):
    return dict(conn.execute("SELECT * FROM library_items WHERE id = ?", (item_id,)).fetchone())


def test_la_temporada_lleva_el_anio_de_su_primer_capitulo(conn):
    _, season = _serie(conn)
    _capitulo(conn, season, numero=2, air_date="2021-01-10")
    _capitulo(conn, season, numero=1, air_date="2020-09-27")
    assert anio_de_temporada(_item(conn, season), conn) == 2020


def test_un_capitulo_lleva_el_anio_de_su_temporada_aunque_se_emitiera_despues(conn):
    _, season = _serie(conn)
    _capitulo(conn, season, numero=1, air_date="2020-12-20")
    ep2 = _capitulo(conn, season, numero=2, air_date="2021-01-03")
    assert anio_de_temporada(_item(conn, ep2), conn) == 2020


def test_sin_airdate_usa_airdateutc(conn):
    _, season = _serie(conn)
    ep = _capitulo(conn, season, numero=1, air_date_utc="2019-05-01T02:00:00Z")
    assert anio_de_temporada(_item(conn, ep), conn) == 2019


@pytest.mark.parametrize("raw", ["no es json", "", json.dumps({"episode": {}})])
def test_sin_fechas_no_hay_anio_de_temporada(conn, raw):
    _, season = _serie(conn)
    ep = _capitulo(conn, season, numero=1, raw=raw)
    assert anio_de_temporada(_item(conn, ep), conn) is None


def test_los_especiales_y_las_peliculas_no_tienen_anio_de_temporada(conn):
    _, season = _serie(conn, temporada=0)
    ep = _capitulo(conn, season, numero=1, temporada=0, air_date="2015-01-01")
    assert anio_de_temporada(_item(conn, ep), conn) is None
    assert anio_de_temporada({"item_type": "movie", "year": 1999}, conn) is None


def test_no_mezcla_capitulos_de_otra_temporada(conn):
    _, s17 = _serie(conn)
    _, s1 = _serie(conn, temporada=1)
    _capitulo(conn, s1, numero=1, temporada=1, air_date="2000-01-01")
    ep = _capitulo(conn, s17, numero=1, air_date="2020-09-27")
    assert anio_de_temporada(_item(conn, ep), conn) == 2020


def test_la_propuesta_de_un_capitulo_lleva_el_anio_de_la_temporada(conn):
    _, season = _serie(conn)
    ep = _capitulo(conn, season, numero=1, air_date="2020-09-27", path="/no/existe/ep.mkv")
    propuesta = build_nomenclature_proposal(ep, conn=conn, guardar=False)
    assert propuesta["proposed_torrent_name"].startswith("Los Simpson S17E01 2020 ")
    assert propuesta["proposed_filename"].startswith("Los.Simpson.S17E01.2020.")
    assert propuesta["year"] == 2020


def test_sin_fechas_la_propuesta_sigue_con_el_anio_de_la_serie(conn):
    _, season = _serie(conn)
    ep = _capitulo(conn, season, numero=1, raw="{}", path="/no/existe/ep.mkv")
    propuesta = build_nomenclature_proposal(ep, conn=conn, guardar=False)
    assert propuesta["proposed_torrent_name"].startswith("Los Simpson S17E01 2000 ")
