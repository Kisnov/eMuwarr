"""Una pista de audio sin idioma bloquea la subida; el idioma elegido en «Ver pistas» cuenta para las normas; y `zxx`
(sin diálogo, como Flow) es un idioma válido que no necesita castellano."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from emuwarez_uploadrr.services import emu_format, mkv_remux
from emuwarez_uploadrr.services import media_info_service as svc
from emuwarez_uploadrr.services import upload_checks as uc
from emuwarez_uploadrr.services.reglas_tracker import con_idiomas_elegidos, reglas_de


def _mkv(*audio: dict) -> dict:
    """La salida de `mkvmerge -J` con estas pistas de audio (ids desde 1, tras el vídeo 0)."""
    return {"ok": True, "data": {"tracks": [
        {"id": 0, "type": "video", "properties": {}},
        *[{"id": i, "type": "audio", "properties": p} for i, p in enumerate(audio, 1)],
    ]}}


@pytest.fixture
def pistas(monkeypatch):
    """Lo que `mkvmerge -J` devuelve para cualquier fichero: se fija con pistas(...)."""
    leido = {}
    monkeypatch.setattr(mkv_remux, "read_tracks_json", lambda ruta: leido["json"])

    def fijar(*audio: dict):
        leido["json"] = _mkv(*audio)
    return fijar


# --- Códigos ---------------------------------------------------------------------------------------------------

def test_zxx_es_un_idioma_que_no_va_al_nombre():
    assert emu_format.resolve_language("zxx") == "ZXX"
    assert emu_format._split_languages("ZXX / ESP") == ["ESP"]
    assert emu_format.build_audio_string("EAC3", "zxx", "5.1") == "DD+ 5.1"
    assert emu_format.solo_sin_dialogo("zxx") and emu_format.solo_sin_dialogo("ZXX / ZXX")
    assert not emu_format.solo_sin_dialogo("ZXX / ENG") and not emu_format.solo_sin_dialogo(None)


@pytest.mark.parametrize("valor", ["eng/und", "UND", "spa / mis", "mul"])
def test_und_mis_y_mul_son_audio_sin_idioma(valor):
    assert emu_format.audio_sin_idioma(valor)


@pytest.mark.parametrize("valor", ["eng / spa", "zxx", None, ""])
def test_lo_etiquetado_no_es_audio_sin_idioma(valor):
    assert not emu_format.audio_sin_idioma(valor)


# --- Normas ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("tracker", ["emu", "nobs", "mnv"])
def test_sin_dialogo_pasa_en_todos_los_trackers(tracker):
    r = reglas_de(tracker, {"audio_languages": "ZXX", "subtitle_languages": None})
    assert r.permitido and r.variante is None


def test_sin_dialogo_anime_va_a_anime():
    assert reglas_de("nobs", {"audio_languages": "zxx", "subtitle_languages": None}, anime=True).variante == "anime"


def test_con_otro_idioma_zxx_no_cuenta():
    # Música sin diálogo más un comentario en inglés: se mira el inglés, que no es castellano.
    assert not reglas_de("emu", {"audio_languages": "ZXX / ENG", "subtitle_languages": None}).permitido


def test_sin_dialogo_cuenta_como_doblada_en_la_biblioteca():
    from emuwarez_uploadrr.services.library.castellano import _leaf_audio, _leaf_ok
    fila = {"audio_languages": "ZXX", "subtitle_languages": None}
    assert _leaf_audio(fila, emu_format.has_castellano) == 1
    assert _leaf_ok(fila, emu_format.has_castellano) == 1


# --- Las pistas con lo elegido ---------------------------------------------------------------------------------

def test_lo_elegido_solo_va_a_las_pistas_sin_idioma(pistas):
    pistas({"language": "eng"}, {"language": "und"}, {"language": "und"})
    # Por posición (el asistente) o por id de pista (el trabajo): la 1 ya tiene idioma y no se pisa.
    assert mkv_remux.idiomas_de_las_pistas("a.mkv", idiomas={"1": "spa", "2": "spa"}) == ["ENG", "ESP", "UND"]
    assert mkv_remux.idiomas_de_las_pistas("a.mkv", overrides={"3": {"language": "zxx"}}) == ["ENG", "UND", "ZXX"]
    assert mkv_remux.idiomas_de_las_pistas("a.mp4") is None


def test_zxx_se_ofrece_en_ver_pistas():
    assert {"code": "zxx", "label": "Sin diálogo (zxx)"} in mkv_remux.opciones_de_idioma()


def test_con_idiomas_elegidos_cambia_el_audio_que_miran_las_normas(pistas):
    pistas({"language": "und"})
    item = {"path": "/m/a.mkv", "audio_languages": "UND", "subtitle_languages": "es-419"}
    # Sin elegir nada, lo de la biblioteca: sin castellano.
    assert con_idiomas_elegidos(item) is item
    assert not reglas_de("emu", item).permitido
    elegido = con_idiomas_elegidos(item, overrides={1: {"language": "spa"}})
    assert elegido["audio_languages"] == "ESP"
    assert reglas_de("emu", elegido).permitido


# --- Comprobaciones del asistente ------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _carpeta_de_subidas_aislada(tmp_path, monkeypatch):
    from emuwarez_uploadrr.services import carpeta_subida
    monkeypatch.setattr(carpeta_subida, "RAIZ_SUBIDAS", str(tmp_path / "uploads"))


@pytest.fixture
def db_conn(isolated_db):
    from emuwarez_uploadrr.database import connect, init_db
    conn = connect(isolated_db / "emuwarez_uploadrr.db")
    init_db(conn)
    return conn


def _pelicula(conn, tmp_path, audio: str, subs: str | None = None) -> int:
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x" * 2048)
    conn.execute(
        """INSERT INTO library_items (item_type, title, local_status, request_status, nomenclature_status,
            path, size_bytes, audio_languages, subtitle_languages, source, updated_at)
           VALUES ('movie', 'X', 'Disponible', 'Sin petición', 'N/A', ?, 100, ?, ?, 'WEB-DL', CURRENT_TIMESTAMP)""",
        (str(f), audio, subs))
    conn.commit()
    return conn.execute("SELECT id FROM library_items ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _check(r: dict, cid: str) -> dict:
    return next(c for c in r["checks"] if c["id"] == cid)


def test_una_pista_sin_idioma_bloquea_y_dice_cual(db_conn, tmp_path, pistas):
    pistas({"language": "spa"}, {"language": "und"})
    r = uc.run_upload_checks(db_conn, _pelicula(db_conn, tmp_path, "ESP / UND"), scope="content")
    c = _check(r, "idioma_audio")
    assert r["ready"] is False and c["status"] == "fail"
    assert "pista de audio 2" in c["message"] and "Ver pistas" in c["message"]


def test_el_idioma_elegido_desbloquea_y_cuenta_para_el_castellano(db_conn, tmp_path, pistas):
    # Audio sin idioma y subtítulos latinos: sin elegir, no pasa; con la pista en castellano, sí.
    pistas({"language": "und"})
    iid = _pelicula(db_conn, tmp_path, "UND", "LAT")
    r = uc.run_upload_checks(db_conn, iid, scope="content", ajustes={"idiomas": {"1": "spa"}})
    assert _check(r, "idioma_audio")["status"] == "ok"
    assert _check(r, "spanish")["status"] == "ok"
    assert r["ready"] is True


def test_sin_leer_las_pistas_manda_la_biblioteca(db_conn, tmp_path, monkeypatch):
    monkeypatch.setattr(mkv_remux, "read_tracks_json", lambda ruta: {"ok": False, "error": "x"})
    r = uc.run_upload_checks(db_conn, _pelicula(db_conn, tmp_path, "eng/und"), scope="content")
    assert _check(r, "idioma_audio")["status"] == "fail"


def test_un_mp4_sin_idioma_dice_que_la_app_no_lo_escribe(db_conn, tmp_path):
    iid = _pelicula(db_conn, tmp_path, "eng/und")
    db_conn.execute("UPDATE library_items SET path = ? WHERE id = ?", (str(tmp_path / "movie.mp4"), iid))
    (tmp_path / "movie.mp4").write_bytes(b"x" * 2048)
    db_conn.commit()
    c = _check(uc.run_upload_checks(db_conn, iid, scope="content"), "idioma_audio")
    assert c["status"] == "fail" and "solo lo escribe en un MKV" in c["message"]


def test_con_las_pistas_en_ninguno_lo_elegido_no_cuenta(db_conn, tmp_path, pistas):
    from emuwarez_uploadrr.services.settings import set_setting
    set_setting(db_conn, "remux.track_mode", "ninguno")
    db_conn.commit()
    pistas({"language": "und"})
    iid = _pelicula(db_conn, tmp_path, "UND")
    c = _check(uc.run_upload_checks(db_conn, iid, scope="content", ajustes={"idiomas": {"1": "spa"}}), "idioma_audio")
    assert c["status"] == "fail" and "«Ninguno»" in c["message"]


def test_una_pelicula_sin_dialogo_se_puede_subir(db_conn, tmp_path, pistas):
    pistas({"language": "zxx"})
    r = uc.run_upload_checks(db_conn, _pelicula(db_conn, tmp_path, "ZXX"), scope="content")
    assert r["ready"] is True, r["checks"]


# --- El nombre de una película sin diálogo ---------------------------------------------------------------------

def _informe(tmp_path, *audio: dict) -> dict:
    f = tmp_path / "flow.mkv"
    f.write_text("fake")
    info = {"media": {"track": [{"@type": "General"},
                                {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080"},
                                *[{"@type": "Audio", **a} for a in audio]]}}
    with patch.object(svc, "mediainfo_available", return_value=True), \
            patch.object(svc.media_probe, "run_mediainfo_json", return_value=info):
        return svc.build_mediainfo_report(str(f))


def test_sin_dialogo_lleva_codec_y_canales_sin_idioma(tmp_path):
    r = _informe(tmp_path, {"Format": "E-AC-3", "Language": "zxx", "Channels": "6"})
    assert r["audio_summary"] == "DD+ 5.1"
    assert r["pistas_sin_idioma"] == []


def test_sin_dialogo_va_al_audio_del_pack(tmp_path):
    r = _informe(tmp_path, {"Format": "AC-3", "Language": "zxx", "Channels": "6"})
    assert r["audio_by_language"] == {"ZXX": {"codec": "DD", "channels": "5.1"}}
    assert emu_format.build_audio_string("DD", "ZXX", "5.1") == "DD 5.1"


def test_con_otro_idioma_zxx_no_entra_en_el_nombre(tmp_path):
    r = _informe(tmp_path, {"Format": "E-AC-3", "Language": "zxx", "Channels": "6"},
                 {"Format": "AC-3", "Language": "es", "Channels": "2"})
    assert r["audio_summary"] == "ESP DD 2.0"


# --- Antes de lanzar Uploadrr: el fichero preparado ------------------------------------------------------------

def test_el_fichero_preparado_sin_idioma_no_se_sube(tmp_path, pistas):
    from emuwarez_uploadrr.services.upload_queue import _sin_idioma_preparado
    pack = tmp_path / "Pack S01"
    pack.mkdir()
    (pack / "e01.mkv").write_text("x")
    (pack / "e02.mkv").write_text("x")
    pistas({"language": "spa"})
    assert _sin_idioma_preparado(str(pack)) is None
    assert _sin_idioma_preparado(None) is None
    pistas({"language": "spa"}, {"language": "und"})
    assert _sin_idioma_preparado(str(pack / "e02.mkv")) == (str(pack / "e02.mkv"), [2])
    assert _sin_idioma_preparado(str(pack)) == (str(pack / "e01.mkv"), [2])
