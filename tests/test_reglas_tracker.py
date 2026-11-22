"""Las normas de idioma y categoría de cada tracker (services/reglas_tracker.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from emuwarez_uploadrr.services import upload_checks as uc
from emuwarez_uploadrr.services.reglas_tracker import VARIANTES, Reglas, es_anime, reglas_de
from emuwarez_uploadrr.services.uploadrr_contract import argv_variante, build_uploadrr_emu_command

TABLAS = Path(__file__).resolve().parents[1] / "uploadrr-emu" / "data" / "trackers"


def _item(audio, subs=None):
    return {"audio_languages": audio, "subtitle_languages": subs}


# (audio, subtítulos, anime) → por tracker: None si se bloquea, o la variante que le toca ("" = la normal)
MATRIZ = [
    ("spa / eng", None, False, {"emu": "", "nobs": "", "mnv": ""}),
    ("spa / jpn", None, True, {"emu": "", "nobs": "anime", "mnv": "anime"}),
    ("eng", "spa", False, {"emu": "", "nobs": "", "mnv": ""}),
    ("jpn", "spa / eng", True, {"emu": "", "nobs": "anime", "mnv": "anime"}),
    ("es-419 / eng", None, False, {"emu": None, "nobs": "", "mnv": "latino"}),
    ("es-419 / jpn", None, True, {"emu": None, "nobs": "anime", "mnv": "latino"}),
    ("eng", "es-419", False, {"emu": None, "nobs": "", "mnv": ""}),
    ("cat", None, False, {"emu": None, "nobs": None, "mnv": ""}),
    ("cat / jpn", None, True, {"emu": None, "nobs": None, "mnv": "anime_catalan"}),
    ("ca-valencia", None, False, {"emu": None, "nobs": None, "mnv": ""}),
    ("cat / es-419", None, True, {"emu": None, "nobs": "anime", "mnv": "anime_catalan"}),
    ("spa / cat / jpn", None, True, {"emu": "", "nobs": "anime", "mnv": "anime"}),
    ("cat", "spa", False, {"emu": "", "nobs": "", "mnv": ""}),
    ("eng", "eng", False, {"emu": None, "nobs": None, "mnv": None}),
    (None, None, False, {"emu": None, "nobs": None, "mnv": None}),
]


@pytest.mark.parametrize("audio, subs, anime, esperado", MATRIZ)
@pytest.mark.parametrize("tracker", ["emu", "nobs", "mnv"])
def test_matriz_de_idiomas_por_tracker(tracker, audio, subs, anime, esperado):
    reglas = reglas_de(tracker, _item(audio, subs), anime=anime)
    if esperado[tracker] is None:
        assert reglas.permitido is False and reglas.variante is None
    else:
        assert reglas.permitido is True and (reglas.variante or "") == esperado[tracker]
    assert reglas.motivo


# Variante → id de categoría con la tabla de Uploadrr-EMU (token <categoría>_<variante>; la normal, `movie`/`tv`)
def _id_categoria(tracker: str, categoria: str, variante: str | None) -> int:
    tabla = json.loads((TABLAS / f"{tracker}.json").read_text(encoding="utf-8"))
    ids = {t: e["id"] for e in tabla["categories"] for t in e["tokens"]}
    return ids.get(f"{categoria}_{variante}", ids[categoria])


@pytest.mark.parametrize("tracker, audio, anime, pelicula, serie", [
    ("NOBS", "spa", False, 1, 2),
    ("NOBS", "spa / jpn", True, 4, 5),
    ("MNV", "spa", False, 1, 2),
    ("MNV", "spa / jpn", True, 3, 4),
    ("MNV", "es-419", False, 10, 14),
    ("MNV", "cat / jpn", True, 8, 8),
    ("MNV", "cat", False, 1, 2),
    ("EMU", "spa / jpn", True, 1, 2),
])
def test_la_variante_da_la_categoria_de_cada_tracker(tracker, audio, anime, pelicula, serie):
    variante = reglas_de(tracker.lower(), _item(audio), anime=anime).variante
    assert (_id_categoria(tracker, "movie", variante), _id_categoria(tracker, "tv", variante)) == (pelicula, serie)


def test_emu_nunca_lleva_variante_y_dice_lo_de_siempre():
    assert reglas_de("emu", _item("spa"), anime=True) == Reglas(
        True, "Audio en castellano. No son obligatorios subtítulos en castellano.", None)
    assert reglas_de("emu", _item("eng", "spa")).motivo == "Sin audio en castellano (eng), pero subtítulos en castellano."
    assert reglas_de("emu", _item("eng", "eng")).motivo == "Ni el audio (eng) ni los subtítulos (eng) están en castellano."


def test_el_bloqueo_dice_el_tracker_y_lo_que_hay():
    motivo = reglas_de("nobs", _item("cat / eng", "eng")).motivo
    assert motivo.startswith("N.O.B.S no lo admite") and "cat, eng" in motivo and "ni latino" in motivo
    assert "catalán" in reglas_de("mnv", _item("eng")).motivo


def test_tracker_desconocido_es_un_error():
    with pytest.raises(ValueError):
        reglas_de("xyz", _item("spa"))


def test_las_variantes_estan_en_alguna_tabla():
    tokens = {t for tr in ("NOBS", "MNV")
              for e in json.loads((TABLAS / f"{tr}.json").read_text(encoding="utf-8"))["categories"] for t in e["tokens"]}
    assert all(f"movie_{v}" in tokens and f"tv_{v}" in tokens for v in VARIANTES)


# --variante en el argv: solo cuando no es la categoría normal

def test_el_argv_lleva_variante_solo_si_la_hay():
    base = dict(python_path="py", uploadrr_script_path="upload.py", prepared_path="/data/x.mkv",
                torrent_name="X", category="movie")
    assert "--variante" not in build_uploadrr_emu_command(**base)
    assert "--variante" not in build_uploadrr_emu_command(**base, variante=None)
    cmd = build_uploadrr_emu_command(**base, variante="anime")
    assert cmd[cmd.index("--variante") + 1] == "anime"
    assert argv_variante(None) == [] and argv_variante("latino") == ["--variante", "latino"]


# Si es anime: la instancia marcada de anime, o un id de MyAnimeList

@pytest.fixture
def db_conn(isolated_db):
    from emuwarez_uploadrr.database import connect, init_db
    conn = connect(isolated_db / "emuwarez_uploadrr.db")
    init_db(conn)
    return conn


def _instancia(conn, nombre, anime):
    conn.execute("INSERT INTO instances (type, name, url, api_key, enabled, is_anime) VALUES ('sonarr', ?, 'http://x', 'k', 1, ?)",
                 (nombre, int(anime)))
    return conn.execute("SELECT id FROM instances WHERE name = ?", (nombre,)).fetchone()[0]


def test_es_anime_por_la_instancia_o_por_mal_id(db_conn):
    anime = _instancia(db_conn, "Anime", True)
    normal = _instancia(db_conn, "Series", False)
    assert es_anime(db_conn, {"instance_id": anime}) is True
    assert es_anime(db_conn, {"instance_id": normal}) is False
    assert es_anime(db_conn, {"instance_id": normal, "mal_id": "5114"}) is True
    assert es_anime(db_conn, {"instance_id": None}) is False


# La comprobación «spanish» de la subida sigue las normas del tracker

@pytest.fixture(autouse=True)
def _carpeta_de_subidas_aislada(tmp_path, monkeypatch):
    from emuwarez_uploadrr.services import carpeta_subida
    monkeypatch.setattr(carpeta_subida, "RAIZ_SUBIDAS", str(tmp_path / "uploads"))


def _pelicula(conn, ruta, audio, subs=None):
    conn.execute(
        """INSERT INTO library_items (item_type, title, local_status, request_status, nomenclature_status,
            path, size_bytes, audio_languages, subtitle_languages, source, updated_at)
           VALUES ('movie', 'X', 'Disponible', 'Sin petición', 'N/A', ?, 100, ?, ?, 'WEB-DL', CURRENT_TIMESTAMP)""",
        (ruta, audio, subs),
    )
    conn.commit()
    return conn.execute("SELECT id FROM library_items ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _comprobacion(conn, iid, **kw):
    return next(c for c in uc.run_upload_checks(conn, iid, scope="content", **kw)["checks"] if c["id"] == "spanish")


def test_la_comprobacion_de_idiomas_es_la_del_tracker(db_conn, tmp_path):
    f = tmp_path / "peli.mkv"
    f.write_bytes(b"x")
    iid = _pelicula(db_conn, str(f), "cat")
    assert _comprobacion(db_conn, iid)["label"] == "No tiene castellano"
    assert _comprobacion(db_conn, iid, tracker="nobs") | {"message": ""} == {
        "id": "spanish", "label": "Idiomas no admitidos en N.O.B.S", "status": "fail", "message": ""}
    assert _comprobacion(db_conn, iid, tracker="mnv") == {
        "id": "spanish", "label": "Idiomas admitidos en Milnueve", "status": "ok",
        "message": "Audio en catalán, sin castellano."}
