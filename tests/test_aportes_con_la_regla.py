"""Lo que se guarda en `library_items.emu_aportes` (filtro «Aportas», «Mejoras posibles», peticiones) sale de
conclusion_duplicados, la misma regla que enseña la ficha: hay aporte si y solo si la conclusión es un trump."""
from __future__ import annotations

import json

import pytest

from emuwarez_uploadrr.database import set_state
from emuwarez_uploadrr.services.emu_catalog import (
    SYNC_COMPLETED_STATE_KEY, _compute_and_store_item_status, get_item_comparison,
)
from tests.test_catalogo_por_tracker import db_conn  # noqa: F401 (fixture)

_FIXTURES_COMPARTIDAS = (db_conn,)


def _peli(conn, resolution="1080p", source="WEB-DL", codec=None, audio=None, size=None):
    item = conn.execute(
        "INSERT INTO library_items (item_type, title, tmdb_id, resolution, source, video_codec, audio_languages, "
        "size_bytes, local_status) VALUES ('movie', 'Peli', '1', ?, ?, ?, ?, ?, 'Disponible')",
        (resolution, source, codec, audio, size)).lastrowid
    conn.commit()
    return item


def _torrent(conn, name, resolution="1080p", source="WEB-DL", codec=None, has_spanish=None, size=None):
    conn.execute("INSERT INTO emu_catalog (tracker, category, tmdb_id, resolution, source, codec, has_spanish, size, "
                 "name) VALUES ('emu', 'Películas', '1', ?, ?, ?, ?, ?, ?)",
                 (resolution, source, codec, has_spanish, size, name))
    set_state(conn, SYNC_COMPLETED_STATE_KEY, "2026-11-24T00:00:00")
    conn.commit()


def _guardado_y_ficha(conn, item):
    """Lo que guarda la sincronización y lo que dice la ficha del mismo elemento."""
    _compute_and_store_item_status(conn)
    row = conn.execute("SELECT emu_in_catalog, emu_aportes FROM library_items WHERE id = ?", (item,)).fetchone()
    ficha = get_item_comparison(conn, item)
    assert row["emu_in_catalog"] == 1
    assert json.loads(row["emu_aportes"]) == ficha["aportes"]
    assert ficha.get("only_episodes") or bool(ficha["aportes"]) == (ficha["conclusion"]["nivel"] == "trump")
    return ficha


def test_un_codec_que_el_torrent_no_dice_es_un_aporte(db_conn):
    """El códec es obligatorio en el nombre: el que no lo dice se mejora subiéndolo con él."""
    item = _peli(db_conn, codec="HEVC")
    _torrent(db_conn, "Peli (2020) 1080p WEB-DL")

    ficha = _guardado_y_ficha(db_conn, item)

    assert ficha["conclusion"]["nivel"] == "trump" and ficha["aportes"] == [{"dim": "codec", "detail": "H.265"}]


def test_audio_esp_que_no_tiene_ninguno_es_un_aporte(db_conn):
    item = _peli(db_conn, audio="spa")
    _torrent(db_conn, "Peli (2020) 1080p WEB-DL VOSE", has_spanish=0)

    assert _guardado_y_ficha(db_conn, item)["aportes"] == [{"dim": "idioma", "detail": "ESP"}]


def test_audio_esp_con_peor_calidad_sigue_siendo_un_aporte_de_idioma(db_conn):
    item = _peli(db_conn, resolution="720p", audio="spa")
    _torrent(db_conn, "Peli (2020) 1080p WEB-DL VOSE", has_spanish=0)

    assert _guardado_y_ficha(db_conn, item)["aportes"] == [{"dim": "idioma", "detail": "ESP"}]


@pytest.mark.parametrize("mia, torrents, aporte", [
    ({"resolution": "2160p"}, [{}], {"dim": "resolucion", "detail": "2160p"}),
    # Frente al torrent más parecido (el 1080p) lo que mejora es el source, no la resolución del 720p.
    ({"source": "BluRay"}, [{}, {"resolution": "720p"}], {"dim": "source", "detail": "BluRay"}),
    ({"codec": "HEVC", "size": 2_000_000_000}, [{"codec": "H.264", "size": 5_000_000_000}],
     {"dim": "codec", "detail": "H.265 (−3.0 GB)"}),
])
def test_una_copia_mejor_que_todo_aporta_lo_que_la_hace_mejor(db_conn, mia, torrents, aporte):
    item = _peli(db_conn, **mia)
    for i, t in enumerate(torrents):
        _torrent(db_conn, f"Peli (2020) {i}", **t)

    assert _guardado_y_ficha(db_conn, item)["aportes"] == [aporte]


def test_mejor_que_uno_y_peor_que_otro_no_es_un_aporte(db_conn):
    item = _peli(db_conn, source="BluRay")
    _torrent(db_conn, "Peli (2020) 1080p WEB-DL")
    _torrent(db_conn, "Peli (2020) 2160p WEB-DL", resolution="2160p")

    ficha = _guardado_y_ficha(db_conn, item)

    assert ficha["conclusion"]["nivel"] == "mixto" and ficha["aportes"] == []


@pytest.mark.parametrize("tus_capitulos, aportes", [((1, 2), [{"dim": "pack", "detail": "Pack de temporada"}]),
                                                     ((1,), [])])
def test_una_temporada_con_solo_capitulos_sueltos_aporta_el_pack_si_los_tienes_todos(db_conn, tus_capitulos, aportes):
    ins = "INSERT INTO library_items (item_type, title, tvdb_id, parent_id, season_number, episode_number, " \
          "resolution, source, local_status) VALUES (?, 'Serie', '9', ?, ?, ?, '1080p', 'WEB-DL', 'Disponible')"
    serie = db_conn.execute(ins, ("series", None, None, None)).lastrowid
    temporada = db_conn.execute(ins, ("season", serie, 1, None)).lastrowid
    for n in tus_capitulos:
        db_conn.execute(ins, ("episode", temporada, 1, n))
    for n in (1, 2):
        db_conn.execute("INSERT INTO emu_catalog (tracker, category, tvdb_id, season_number, resolution, source, name) "
                        "VALUES ('emu', 'Series', '9', 1, '720p', 'WEB-DL', ?)", (f"Serie S01E0{n} 720p WEB-DL",))
    set_state(db_conn, SYNC_COMPLETED_STATE_KEY, "2026-11-24T00:00:00")
    db_conn.commit()

    ficha = _guardado_y_ficha(db_conn, temporada)

    assert ficha["only_episodes"] and ficha["aportes"] == aportes


@pytest.mark.parametrize("audio", ["spa / eng", "ESP / ENG", "eng / es", "es", "Latino", "LAT", "eng / spa-419", "zxx"])
def test_tu_audio_en_castellano_es_el_de_la_columna_espanol(db_conn, audio):
    """Si tu copia aporta audio ESP lo decide la misma regla que la columna Español: el latino no cuenta."""
    from emuwarez_uploadrr.services.emu_format import has_castellano
    item = _peli(db_conn, audio=audio)
    _torrent(db_conn, "Peli (2020) 1080p WEB-DL VOSE", has_spanish=0)

    assert _guardado_y_ficha(db_conn, item)["mine"]["has_es"] is (has_castellano(audio) == 1)
