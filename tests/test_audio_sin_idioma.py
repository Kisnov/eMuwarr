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


@pytest.fixture(autouse=True)
def _con_mkvmerge(monkeypatch):
    """mkvmerge disponible, sin depender de la máquina: los tests que lo quieren ausente lo dicen."""
    monkeypatch.setattr(mkv_remux, "check_mkvmerge_available", lambda: {"available": True, "path": "mkvmerge"})


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


def test_con_idiomas_elegidos_lee_el_fichero_de_ver_pistas(tmp_path, monkeypatch):
    """Sin el original, el preparado: el mismo que lee «Ver pistas» (path_mapping.ruta_de_lectura)."""
    leidas = []
    monkeypatch.setattr(mkv_remux, "read_tracks_json",
                        lambda ruta: leidas.append(ruta) or _mkv({"language": "und"}))
    preparado = tmp_path / "Peli.mkv"
    preparado.write_bytes(b"x")
    item = {"path": "/biblioteca/ya/no/esta.mkv", "prepared_path": str(preparado), "audio_languages": "UND"}
    assert con_idiomas_elegidos(item, idiomas={"1": "spa"})["audio_languages"] == "ESP"
    assert leidas == [str(preparado)]


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


def _con_ruta(conn, tmp_path, iid: int, nombre: str) -> None:
    (tmp_path / nombre).write_bytes(b"x" * 2048)
    conn.execute("UPDATE library_items SET path = ? WHERE id = ?", (str(tmp_path / nombre), iid))
    conn.commit()


def test_un_mp4_sin_idioma_avisa_de_pasarlo_a_mkv_sin_bloquear(db_conn, tmp_path):
    iid = _pelicula(db_conn, tmp_path, "spa/und")
    _con_ruta(db_conn, tmp_path, iid, "movie.mp4")
    r = uc.run_upload_checks(db_conn, iid, scope="content")
    c = _check(r, "idioma_audio")
    assert c["status"] == "skip" and c["message"].startswith("Es un MP4: la mayoría de trackers piden MKV.")
    assert r["ready"] is True


@pytest.mark.parametrize("nombre", ["movie.avi", "movie.m2ts"])
def test_otros_contenedores_avisan_como_un_mp4(db_conn, tmp_path, nombre):
    iid = _pelicula(db_conn, tmp_path, "spa/und")
    _con_ruta(db_conn, tmp_path, iid, nombre)
    assert _check(uc.run_upload_checks(db_conn, iid, scope="content"), "idioma_audio")["status"] == "skip"


def test_un_iso_sin_idioma_no_avisa_del_idioma_pero_no_se_sube(db_conn, tmp_path):
    iid = _pelicula(db_conn, tmp_path, "spa/und")
    _con_ruta(db_conn, tmp_path, iid, "movie.iso")
    r = uc.run_upload_checks(db_conn, iid, scope="content")
    assert "idioma_audio" not in {c["id"] for c in r["checks"]}
    assert r["ready"] is False and _check(r, "formato")["status"] == "fail"


def test_el_aviso_del_nombre_depende_del_contenedor():
    from emuwarez_uploadrr.services.nomenclature import aviso_pistas_sin_idioma
    assert aviso_pistas_sin_idioma([2], "/m/a.mkv").endswith("que sin él no se puede subir.")
    assert "Conviértelo a MKV" in aviso_pistas_sin_idioma([2], "/m/a.mp4")
    assert aviso_pistas_sin_idioma([2], "/m/a.iso").endswith("no la incluye.")


def test_un_pack_en_mp4_avisa_sin_bloquear(tmp_path, monkeypatch):
    from emuwarez_uploadrr.services import season_pack
    monkeypatch.setattr(svc, "build_mediainfo_report", lambda ruta, **k: {"pistas_sin_idioma": [2]})
    entradas = [{"episode": {"episode_number": 1, "path": "/s/e01.mkv"}},
                {"episode": {"episode_number": 2, "path": "/s/e02.mp4"}},
                {"episode": {"episode_number": 3, "path": "/s/e03.iso"}}]
    assert season_pack._capitulos_con_audio_sin_idioma(entradas, None) == [1]
    entradas.append({"episode": {"episode_number": 4, "path": "/s/e04.mp4"}})
    # Una línea por aviso, con todos sus capítulos.
    assert season_pack._avisos_no_mkv(entradas) == [
        "Capítulo(s) 2, 4: " + emu_format_aviso("/s/e02.mp4")]
    # Y el selector del pack solo ofrece pistas de un MKV.
    assert [p["capitulos"] for p in season_pack._pistas_sin_idioma_del_pack(entradas, None)] == [[1]]


def emu_format_aviso(ruta):
    from emuwarez_uploadrr.services.reglas_tracker import aviso_no_mkv
    return aviso_no_mkv(ruta)


def test_un_pack_custom_con_un_capitulo_sin_dialogo_no_lleva_zxx(monkeypatch):
    from emuwarez_uploadrr.services import season_pack
    por_capitulo = {1: {"ZXX": "DD 5.1"}, 2: {"ESP": "DD 5.1"}}
    monkeypatch.setattr(season_pack, "_episode_audio_by_language",
                        lambda ep, conn, idiomas=None: por_capitulo[ep["episode_number"]])
    entradas = [{"episode": {"episode_number": n}} for n in (1, 2)]
    assert season_pack._aggregate_pack_audio(entradas) == ("ESP DD 5.1", True)


def test_un_mkv_que_no_se_lee_no_se_da_por_bueno(tmp_path, monkeypatch):
    from emuwarez_uploadrr.services.upload_queue import _sin_idioma_preparado
    monkeypatch.setattr(mkv_remux, "read_tracks_json", lambda ruta: {"ok": False, "error": "x"})
    (tmp_path / "a.mkv").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    assert _sin_idioma_preparado(str(tmp_path / "a.mkv")) == (str(tmp_path / "a.mkv"), None)
    assert _sin_idioma_preparado(str(tmp_path / "b.mp4")) is None
    # Un preparado que ya no está no es «no se lee»: eso lo dice quien lo busca.
    assert _sin_idioma_preparado(str(tmp_path / "no.mkv")) is None
    # Sin mkvmerge no se puede mirar, como en la integridad: no se para.
    monkeypatch.setattr(mkv_remux, "check_mkvmerge_available", lambda: {"available": False})
    assert _sin_idioma_preparado(str(tmp_path / "a.mkv")) is None


def test_antes_de_lanzar_un_mp4_no_se_para(tmp_path):
    from emuwarez_uploadrr.services.upload_queue import _sin_idioma_preparado
    (tmp_path / "a.mp4").write_bytes(b"x")
    assert _sin_idioma_preparado(str(tmp_path / "a.mp4")) is None


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

def _informe(tmp_path, *audio: dict, nombre: str = "flow.mkv", idiomas: dict | None = None) -> dict:
    f = tmp_path / nombre
    f.write_text("fake")
    info = {"media": {"track": [{"@type": "General"},
                                {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080"},
                                *[{"@type": "Audio", **a} for a in audio]]}}
    with patch.object(svc, "mediainfo_available", return_value=True), \
            patch.object(svc.media_probe, "run_mediainfo_json", return_value=info):
        return svc.build_mediainfo_report(str(f), idiomas_audio=idiomas)


def test_lo_elegido_solo_cuenta_en_un_mkv(tmp_path):
    # En un MP4 (también un capítulo de un pack) no se escribe: el nombre no lo puede decir.
    und = {"Format": "AC-3", "Channels": "6"}
    assert _informe(tmp_path, und, idiomas={1: "spa"})["audio_summary"] == "ESP DD 5.1"
    mp4 = _informe(tmp_path, und, nombre="e01.mp4", idiomas={1: "spa"})
    assert mp4["pistas_sin_idioma"] == [1] and mp4["audio_summary"] is None


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
